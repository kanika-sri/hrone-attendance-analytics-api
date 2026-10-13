"""HROne attendance API. Environment: MONGO_URI and MONGO_DB."""
import calendar
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Path
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
from bson import json_util
import json

load_dotenv(override=False)
IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
PRESENT = ['PRESENT', 'WFH', 'ON_DUTY']
STATUSES = ['PRESENT', 'ABSENT', 'LEAVE', 'WFH', 'ON_DUTY']
LO = 100_000_000_000
HI = 4_102_444_800_000
client = MongoClient(os.environ.get('MONGO_URI', 'mongodb://localhost:27017'), serverSelectionTimeoutMS=2500)
db = client[os.environ.get('MONGO_DB', 'attendance_db')]
app = FastAPI(title='Employee Attendance & Analytics API', version='2.0.0')


def invalid(message):
    raise HTTPException(422, message)


def parse_date(s):
    try:
        d = date.fromisoformat(s)
        if d.isoformat() == s:
            return d
    except (TypeError, ValueError):
        pass
    invalid('Expected date YYYY-MM-DD')


def bounds(month):
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', month or ''):
        invalid('Expected month YYYY-MM')
    y, m = map(int, month.split('-'))
    a = date(y, m, 1)
    b = date(y, m, calendar.monthrange(y, m)[1])
    return a, b


def ms_to_dt(value):
    if type(value) is not int or not LO <= value <= HI:
        invalid('Timestamp must be integer epoch milliseconds in range')
    return datetime.fromtimestamp(value / 1000, UTC).replace(microsecond=0, tzinfo=None)


def dt_to_ms(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1000)


def current_dt():
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def rounded(value, places=2):
    if value is None:
        return None
    return float(Decimal(str(value)).quantize(Decimal('1').scaleb(-places), rounding=ROUND_HALF_UP))


def local_dt(value):
    return value.replace(tzinfo=UTC).astimezone(IST)


def attendance_day(pin, emp):
    local = local_dt(pin)
    result = local.date()
    if emp['shift_end'] <= emp['shift_start'] and local.strftime('%H:%M') < emp['shift_end']:
        result -= timedelta(days=1)
    return result.isoformat()


def shift_times(day, emp):
    d = parse_date(day)
    sh, sm = map(int, emp['shift_start'].split(':'))
    eh, em = map(int, emp['shift_end'].split(':'))
    start = datetime.combine(d, time(sh, sm), IST).astimezone(UTC).replace(tzinfo=None)
    if emp['shift_end'] <= emp['shift_start']:
        d += timedelta(days=1)
    end = datetime.combine(d, time(eh, em), IST).astimezone(UTC).replace(tzinfo=None)
    return start, end


def derive(pin, pout, day, emp):
    if pin is None:
        return {'late_minutes': 0, 'work_hours': None, 'overtime_minutes': 0, 'half_day': False}
    start, end = shift_times(day, emp)
    delay = (pin - start).total_seconds()
    late = int(delay // 60) if delay > 600 else 0
    if pout is None:
        return {'late_minutes': late, 'work_hours': None, 'overtime_minutes': 0, 'half_day': False}
    hours = rounded(Decimal(int((pout - pin).total_seconds())) / Decimal(3600))
    extra = int((pout - end).total_seconds() // 60)
    return {'late_minutes': late, 'work_hours': hours,
            'overtime_minutes': extra if extra >= 30 else 0, 'half_day': hours < 4.50}


def serialize_employee(obj):
    return {k: (dt_to_ms(v) if k == 'created_at' else v) for k, v in obj.items() if k != '_id'}


def serialize_log(obj):
    result = {k: v for k, v in obj.items() if k != '_id'}
    result.setdefault('half_day', False)
    result.setdefault('history', [])
    for k in ('punch_in', 'punch_out'):
        result[k] = dt_to_ms(result.get(k))
    result.setdefault('work_hours', None)
    result.setdefault('late_minutes', 0)
    result.setdefault('overtime_minutes', 0)
    history = []
    for entry in result['history'] or []:
        changes = {}
        for field, pair in entry['changes'].items():
            changes[field] = {k: dt_to_ms(v) if field in ('punch_in', 'punch_out') else v for k, v in pair.items()}
        history.append({'at': dt_to_ms(entry['at']), 'by': entry['by'], 'reason': entry['reason'], 'changes': changes})
    result['history'] = history
    return result


def employee(code):
    obj = db.employees.find_one({'emp_code': code})
    if obj is None:
        raise HTTPException(404, 'Employee not found')
    return obj


def weekdays(a, b):
    if a > b:
        return 0
    full, rest = divmod((b - a).days + 1, 7)
    return full * 5 + sum((a + timedelta(days=i)).weekday() < 5 for i in range(rest))


class EmployeeIn(BaseModel):
    model_config = ConfigDict(extra='ignore')
    emp_code: str = Field(pattern=r'^EMP\d{4,6}$')
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field('09:30', pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    shift_end: str = Field('18:30', pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    joined_on: str

    @field_validator('joined_on')
    @classmethod
    def joined(cls, v):
        parse_date(v)
        return v

    @model_validator(mode='after')
    def shifts(self):
        if self.shift_start == self.shift_end:
            raise ValueError('shift start and end must differ')
        return self


class PunchIn(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    emp_code: str
    punched_at: Optional[int] = Field(None, ge=LO, le=HI)
    status: Literal['PRESENT', 'WFH', 'ON_DUTY'] = 'PRESENT'


class PunchOut(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    emp_code: str
    punched_at: Optional[int] = Field(None, ge=LO, le=HI)


class Correction(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)
    status: Optional[Literal['PRESENT', 'WFH', 'ON_DUTY', 'ABSENT', 'LEAVE']] = None
    punch_in: Optional[int] = Field(None, ge=LO, le=HI)
    punch_out: Optional[int] = Field(None, ge=LO, le=HI)


@app.on_event('startup')
def indexes():
    db.employees.create_index([('emp_code', 1)], unique=True, name='employee_code_unique')
    db.employees.create_index([('department', 1), ('joined_on', 1)], name='employee_department_joined')
    db.employees.create_index([('department', 1), ('emp_code', 1)], name='employee_department_code')
    db.attendance_logs.create_index([('emp_code', 1), ('date', 1)], unique=True, name='attendance_natural_key')
    db.attendance_logs.create_index([('date', -1), ('emp_code', 1)], name='attendance_listing')
    db.attendance_logs.create_index([('status', 1), ('date', -1), ('emp_code', 1)], name='attendance_status_listing')
    db.attendance_logs.create_index([('date', 1), ('emp_code', 1)], name='attendance_month')
    db.attendance_logs.create_index([('emp_code', 1), ('punch_in', -1)], name='attendance_latest_punch')


@app.get('/health')
def health():
    try:
        client.admin.command('ping')
        return {'status': 'ok'}
    except PyMongoError:
        raise HTTPException(503, 'MongoDB unavailable')


@app.post('/employees', status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc['created_at'] = current_dt()
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, 'emp_code already exists')
    return serialize_employee(doc)


@app.get('/employees')
def list_employees(department: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    q = {'department': department} if department is not None else {}
    cur = db.employees.find(q).sort('emp_code', 1).skip((page - 1) * page_size).limit(page_size)
    return {'items': [serialize_employee(d) for d in cur], 'total': db.employees.count_documents(q), 'page': page, 'page_size': page_size}


@app.post('/attendance/punch-in', status_code=201)
def punch_in(body: PunchIn):
    emp = employee(body.emp_code)
    ts = ms_to_dt(body.punched_at) if body.punched_at is not None else current_dt()
    d = attendance_day(ts, emp)
    record = {'emp_code': body.emp_code, 'date': d, 'status': body.status,
              'punch_in': ts, 'punch_out': None, 'history': [], **derive(ts, None, d, emp)}
    try:
        db.attendance_logs.insert_one(record)
    except DuplicateKeyError:
        raise HTTPException(409, 'Already punched in for that date')
    return serialize_log(record)


@app.post('/attendance/punch-out')
def punch_out(body: PunchOut):
    emp = employee(body.emp_code)
    ts = ms_to_dt(body.punched_at) if body.punched_at is not None else current_dt()
    rec = db.attendance_logs.find_one({'emp_code': body.emp_code, 'punch_in': {'$lte': ts}}, sort=[('punch_in', -1)])
    if rec is None:
        raise HTTPException(404, 'No punch-in found')
    if rec.get('punch_out') is not None:
        raise HTTPException(409, 'Already punched out')
    duration = (ts - rec['punch_in']).total_seconds()
    if duration <= 0 or duration > 86400:
        invalid('Punch-out must be after punch-in, within 24 hours')
    values = derive(rec['punch_in'], ts, rec['date'], emp)
    changes = {**values, 'punch_out': ts}
    # No audit entry: punch-out is not a manual correction.
    query = {'_id': rec['_id'], 'punch_out': None, 'punch_in': rec['punch_in'], 'status': rec['status']}
    updated = db.attendance_logs.find_one_and_update(query, {'$set': changes}, return_document=ReturnDocument.AFTER)
    if updated is None:
        raise HTTPException(409, 'Attendance was concurrently modified')
    return serialize_log(updated)


def attendance_filter(emp_code=None, date_from=None, date_to=None, status=None):
    q = {}
    if date_from is not None:
        parse_date(date_from)
    if date_to is not None:
        parse_date(date_to)
    if date_from and date_to and date_from > date_to:
        invalid('date_from must not exceed date_to')
    if emp_code is not None:
        q['emp_code'] = emp_code
    if status is not None:
        if status not in STATUSES:
            invalid('Invalid status')
        q['status'] = status
    if date_from is not None or date_to is not None:
        q['date'] = {}
        if date_from is not None:
            q['date']['$gte'] = date_from
        if date_to is not None:
            q['date']['$lte'] = date_to
    return q


@app.get('/attendance')
def list_attendance(emp_code: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None,
                    status: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    q = attendance_filter(emp_code, date_from, date_to, status)
    cur = db.attendance_logs.find(q).sort([('date', -1), ('emp_code', 1)]).skip((page - 1) * page_size).limit(page_size)
    return {'items': [serialize_log(r) for r in cur], 'total': db.attendance_logs.count_documents(q), 'page': page, 'page_size': page_size}


@app.patch('/attendance/{emp_code}/{date}')
def regularize(
    emp_code: str,
    body: Correction,
    date_str: str = Path(..., alias='date')
):
    parse_date(date_str)
    emp = employee(emp_code)
    rec = db.attendance_logs.find_one({'emp_code': emp_code, 'date': date_str})
    if rec is None:
        raise HTTPException(404, 'Attendance not found')
    sent = body.model_fields_set
    state = rec.get('status') if 'status' not in sent else body.status
    if state is None:
        invalid('Status cannot be null')
    if state in ('ABSENT', 'LEAVE') and (('punch_in' in sent) or ('punch_out' in sent)):
        invalid('ABSENT/LEAVE cannot include punch times')
    if state in ('ABSENT', 'LEAVE'):
        pin = pout = None
    else:
        pin = rec.get('punch_in') if 'punch_in' not in sent else ms_to_dt(body.punch_in) if body.punch_in is not None else None
        pout = rec.get('punch_out') if 'punch_out' not in sent else ms_to_dt(body.punch_out) if body.punch_out is not None else None
        if pin is None:
            invalid('Presence requires punch_in')
        if attendance_day(pin, emp) != date_str:
            invalid('punch_in must remain on attendance date')
        if pout is not None and not 0 < (pout - pin).total_seconds() <= 86400:
            invalid('Invalid punch duration')
    final = {'status': state, 'punch_in': pin, 'punch_out': pout, **derive(pin, pout, date_str, emp)}
    changes = {}
    for key, value in final.items():
        old = rec.get(key, False if key == 'half_day' else [] if key == 'history' else None)
        if old != value:
            changes[key] = {'from': old, 'to': value}
    if not changes:
        invalid('No fields changed')
    entry = {'at': current_dt(), 'by': body.regularized_by, 'reason': body.reason, 'changes': changes}
    # CAS on all mutable fields + history avoids concurrent overwrite and lost audit entries.
    check = {'_id': rec['_id']}
    for k in final:
        check[k] = rec[k] if k in rec else {'$exists': False}
    if 'history' in rec:
        check['history'] = rec['history']
    else:
        check['history'] = {'$exists': False}
    updated = db.attendance_logs.find_one_and_update(check, {'$set': final, '$push': {'history': entry}}, return_document=ReturnDocument.AFTER)
    if updated is None:
        raise HTTPException(409, 'Concurrent correction detected')
    return serialize_log(updated)


# ------------------------ shared aggregation builders -----------------------
def weekday_expr(date_field='$date'):
    wd = {'$dayOfWeek': {'$dateFromString': {'dateString': date_field}}}
    return {'$and': [{'$gte': [wd, 2]}, {'$lte': [wd, 6]}]}


def credit_expr(status='$status', half='$half_day'):
    return {'$cond': [{'$in': [status, PRESENT]}, {'$cond': [{'$eq': [{'$ifNull': [half, False]}, True]}, 0.5, 1]}, 0]}


def monthly_pipeline(code, month):
    a, b = bounds(month)
    return [
        {'$match': {'emp_code': code, 'date': {'$gte': a.isoformat(), '$lte': b.isoformat()}}},
        {'$group': {'_id': None,
            'present_days': {'$sum': {'$cond': [weekday_expr(), credit_expr(), 0]}},
            'leave_days': {'$sum': {'$cond': [{'$eq': ['$status', 'LEAVE']}, 1, 0]}},
            'late_count': {'$sum': {'$cond': [{'$gt': [{'$ifNull': ['$late_minutes', 0]}, 0]}, 1, 0]}},
            'total_late_minutes': {'$sum': {'$ifNull': ['$late_minutes', 0]}},
            'total_overtime_minutes': {'$sum': {'$ifNull': ['$overtime_minutes', 0]}}}}
    ]


@app.get('/analytics/employees/{emp_code}/monthly')
def employee_monthly(emp_code: str, month: str):
    emp = employee(emp_code)
    a, b = bounds(month)
    wd = weekdays(max(a, parse_date(emp['joined_on'])), b)
    result = list(db.attendance_logs.aggregate(monthly_pipeline(emp_code, month)))
    r = result[0] if result else {}
    present = rounded(r.get('present_days', 0))
    return {'emp_code': emp_code, 'month': month, 'working_days': wd, 'present_days': present,
            'leave_days': r.get('leave_days', 0), 'late_count': r.get('late_count', 0),
            'total_late_minutes': r.get('total_late_minutes', 0),
            'total_overtime_minutes': r.get('total_overtime_minutes', 0),
            'attendance_pct': rounded(Decimal(str(present)) * 100 / wd) if wd else None}


def department_pipeline(month, department=None):
    a, b = bounds(month)
    match = {'joined_on': {'$lte': b.isoformat()}}
    if department is not None:
        match['department'] = department
    # Each eligible employee is retained even if the lookup returns no records.
    return [
        {'$match': match},
        {'$lookup': {'from': 'attendance_logs', 'let': {'code': '$emp_code'}, 'pipeline': [
            {'$match': {'date': {'$gte': a.isoformat(), '$lte': b.isoformat()},
                        '$expr': {'$eq': ['$emp_code', '$$code']}}}], 'as': 'logs'}},
        {'$unwind': {'path': '$logs', 'preserveNullAndEmptyArrays': True}},
        {'$group': {'_id': '$department',
            'headcount_codes': {'$addToSet': '$emp_code'},
            'present_days': {'$sum': {'$cond': [
                {'$and': [{'$ne': ['$logs.date', None]}, weekday_expr('$logs.date')]},
                credit_expr('$logs.status', '$logs.half_day'), 0]}},
            'hours_total': {'$sum': {'$cond': [{'$and': [{'$in': ['$logs.status', PRESENT]},
                           {'$ne': [{'$ifNull': ['$logs.work_hours', None]}, None]}]}, '$logs.work_hours', 0]}},
            'hours_n': {'$sum': {'$cond': [{'$and': [{'$in': ['$logs.status', PRESENT]},
                           {'$ne': [{'$ifNull': ['$logs.work_hours', None]}, None]}]}, 1, 0]}},
            'late_count': {'$sum': {'$cond': [{'$gt': [{'$ifNull': ['$logs.late_minutes', 0]}, 0]}, 1, 0]}},
            'total_late_minutes': {'$sum': {'$ifNull': ['$logs.late_minutes', 0]}},
            'leave_count': {'$sum': {'$cond': [{'$eq': ['$logs.status', 'LEAVE']}, 1, 0]}},
            'on_duty_count': {'$sum': {'$cond': [{'$eq': ['$logs.status', 'ON_DUTY']}, 1, 0]}}}},
        {'$project': {'_id': 0, 'department': '$_id', 'headcount': {'$size': '$headcount_codes'},
                      'present_days': 1, 'avg_work_hours': {'$cond': [{'$gt': ['$hours_n', 0]},
                      {'$divide': ['$hours_total', '$hours_n']}, None]},
                      'late_count': 1, 'total_late_minutes': 1, 'leave_count': 1, 'on_duty_count': 1}},
        {'$sort': {'department': 1}}
    ]


@app.get('/analytics/departments/summary')
def department_summary(month: str, department: Optional[str] = None):
    items = list(db.employees.aggregate(department_pipeline(month, department)))
    for item in items:
        item['present_days'] = rounded(item['present_days'])
        item['avg_work_hours'] = rounded(item['avg_work_hours'])
    return {'month': month, 'items': items}


def leaderboard_pipeline(month, limit=10, department=None):
    a, b = bounds(month)
    steps = [
        {'$match': {'date': {'$gte': a.isoformat(), '$lte': b.isoformat()}, 'late_minutes': {'$gt': 0}}},
        {'$group': {'_id': '$emp_code', 'total_late_minutes': {'$sum': '$late_minutes'},
                    'late_count': {'$sum': 1}}},
        {'$lookup': {'from': 'employees', 'localField': '_id', 'foreignField': 'emp_code', 'as': 'employee'}},
        {'$unwind': '$employee'}]
    if department is not None:
        steps.append({'$match': {'employee.department': department}})
    return steps + [
        {'$setWindowFields': {'sortBy': {'total_late_minutes': -1}, 'output': {'rank': {'$rank': {}}}}},
        {'$match': {'rank': {'$lte': limit}}},
        {'$sort': {'total_late_minutes': -1, '_id': 1}},
        {'$project': {'_id': 0, 'rank': 1, 'emp_code': '$_id', 'name': '$employee.name',
                      'department': '$employee.department', 'total_late_minutes': 1, 'late_count': 1}}
    ]


@app.get('/analytics/leaderboard/late')
def late_leaderboard(month: str, limit: int = Query(10, ge=1, le=50), department: Optional[str] = None):
    return {'month': month, 'items': list(db.attendance_logs.aggregate(leaderboard_pipeline(month, limit, department)))}


def trend_pipeline(department, from_date, to_date):
    a = parse_date(from_date)
    b = parse_date(to_date)
    if b < a or (b - a).days + 1 > 92:
        invalid('Invalid trend range (maximum 92 days)')
    # Employee-rooted input guarantees a seed row even without any attendance logs.
    # $densify runs inside MongoDB, not in application Python.
    left = datetime.combine(a, time.min)
    right = datetime.combine(b + timedelta(days=1), time.min)
    return [
        {'$match': {'department': department}},
        {'$lookup': {'from': 'attendance_logs', 'let': {'code': '$emp_code'}, 'pipeline': [
            {'$match': {'date': {'$gte': from_date, '$lte': to_date}, '$expr': {'$eq': ['$emp_code', '$$code']}}}],
            'as': 'logs'}},
        {'$unwind': {'path': '$logs', 'preserveNullAndEmptyArrays': True}},
        {'$set': {'day': {'$dateFromString': {'dateString': {'$ifNull': ['$logs.date', from_date]}}}}},
        {'$group': {'_id': '$day',
                    'present_count': {'$sum': credit_expr('$logs.status', '$logs.half_day')},
                    'late_count': {'$sum': {'$cond': [{'$gt': [{'$ifNull': ['$logs.late_minutes', 0]}, 0]}, 1, 0]}}}},
        {'$project': {'_id': 0, 'day': '$_id', 'present_count': 1, 'late_count': 1}},
        {'$densify': {'field': 'day', 'range': {'step': 1, 'unit': 'day', 'bounds': [left, right]}}},
        {'$set': {'present_count': {'$ifNull': ['$present_count', 0]},
                  'late_count': {'$ifNull': ['$late_count', 0]},
                  'date': {'$dateToString': {'date': '$day', 'format': '%Y-%m-%d'}}}},
        {'$lookup': {'from': 'employees', 'let': {'d': '$date'}, 'pipeline': [
            {'$match': {'department': department, '$expr': {'$lte': ['$joined_on', '$$d']}}},
            {'$count': 'n'}], 'as': 'hc'}},
        {'$set': {'headcount': {'$ifNull': [{'$arrayElemAt': ['$hc.n', 0]}, 0]},
                  'is_working_day': {'$and': [{'$gte': [{'$dayOfWeek': '$day'}, 2]},
                                               {'$lte': [{'$dayOfWeek': '$day'}, 6]}]}}},
        {'$set': {'attendance_rate': {'$cond': [
            {'$and': ['$is_working_day', {'$gt': ['$headcount', 0]}]},
            {'$divide': ['$present_count', '$headcount']}, None]}}},
        {'$setWindowFields': {'sortBy': {'day': 1}, 'output': {
            'moving_avg_7d': {'$avg': '$attendance_rate', 'window': {'documents': [-6, 0]}}}}},
        {'$sort': {'day': 1}},
        {'$project': {'_id': 0, 'date': 1, 'is_working_day': 1, 'headcount': 1,
                      'present_count': 1, 'late_count': 1, 'attendance_rate': 1, 'moving_avg_7d': 1}}
    ]


@app.get('/analytics/departments/{department}/trend')
def department_trend(department: str, from_date: str = Query(..., alias='from'), to_date: str = Query(..., alias='to')):
    pipeline = trend_pipeline(department, from_date, to_date)
    if db.employees.find_one({'department': department}) is None:
        raise HTTPException(404, 'Department not found')
    items = list(db.employees.aggregate(pipeline))
    for item in items:
        item['present_count'] = rounded(item['present_count'])
        item['attendance_rate'] = rounded(item['attendance_rate'], 4)
        item['moving_avg_7d'] = rounded(item['moving_avg_7d'], 4)
    return {'department': department, 'items': items}


def explain_aggregation(collection, pipeline):
    return db.command({'explain': {'aggregate': collection, 'pipeline': pipeline, 'cursor': {}}, 'verbosity': 'executionStats'})


@app.get('/admin/explain/{endpoint}')
def explain(endpoint: str, emp_code: Optional[str] = None, month: Optional[str] = None,
            department: Optional[str] = None, limit: int = Query(10, ge=1, le=50),
            date_from: Optional[str] = None, date_to: Optional[str] = None,
            status: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
            from_date: Optional[str] = Query(None, alias='from'), to_date: Optional[str] = Query(None, alias='to')):
    if endpoint == 'attendance_list':
        query = attendance_filter(emp_code, date_from, date_to, status)
        cmd = {'find': 'attendance_logs', 'filter': query, 'sort': {'date': -1, 'emp_code': 1},
               'skip': (page - 1) * page_size, 'limit': page_size}
        collection = 'attendance_logs'
        data = db.command({'explain': cmd, 'verbosity': 'executionStats'})
    elif endpoint == 'employee_monthly':
        if not emp_code or not month:
            invalid('emp_code and month are required')
        collection = 'attendance_logs'
        data = explain_aggregation(collection, monthly_pipeline(emp_code, month))
    elif endpoint == 'department_summary':
        if not month:
            invalid('month is required')
        collection = 'employees'
        data = explain_aggregation(collection, department_pipeline(month, department))
    elif endpoint == 'late_leaderboard':
        if not month:
            invalid('month is required')
        collection = 'attendance_logs'
        data = explain_aggregation(collection, leaderboard_pipeline(month, limit, department))
    elif endpoint == 'department_trend':
        if not department or not from_date or not to_date:
            invalid('department, from and to are required')
        collection = 'employees'
        data = explain_aggregation(collection, trend_pipeline(department, from_date, to_date))
    else:
        invalid('Unknown explain endpoint')
    # MongoDB explain response may include BSON types such as timestamps and Decimal128.
    return {'endpoint': endpoint, 'collection': collection, 'explain': json.loads(json_util.dumps(data))}
