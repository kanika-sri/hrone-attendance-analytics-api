# HROne Attendance & Analytics API

Python 3.11+, FastAPI, PyMongo, MongoDB 6.0+.

## Requirements

- Python 3.11 or newer
- MongoDB 6.0 or newer, running and accessible

## Run

From the repository root:

```cmd
python -m pip install -r requirements.txt
set MONGO_URI=mongodb://localhost:27017
set MONGO_DB=attendance_db
python -m uvicorn app.main:app --port 8000
```

Environment variables take precedence over `.env` configuration. Do not commit `.env` or credentials.

The application creates MongoDB indexes at startup.

## API

- Health endpoint: `http://127.0.0.1:8000/health`
- Interactive API documentation: `http://127.0.0.1:8000/docs`
- OpenAPI contract: `openapi.yaml`

The API supports employee management, attendance punch-in and punch-out, attendance regularization, paginated attendance queries, monthly and department analytics, late leaderboards, department trends, and aggregation explain plans.

## Verification

The following checks were performed against a running local MongoDB instance (MongoDB 9.0.2):

- Python syntax and all 12 API route registrations
- Employee creation and duplicate handling
- Attendance punch-in, punch-out, timestamp conversion, and derived-field calculations
- Monthly employee analytics and department summaries
- Late leaderboard ranking and cutoff ties
- Department trend generation using `$densify` and moving averages
- Concurrent punch-in: one HTTP 201 and nine HTTP 409 responses
- Concurrent punch-out: one HTTP 200 and nine HTTP 409 responses
- Concurrent regularization: two successful corrections with two additional audit-history entries
- Automated read-only API smoke tests: 10/10 passed, including pagination and invalid-date validation
- Invalid month validation (`0000-01`): HTTP 422
- Monthly attendance percentage rounding: verified to four decimal places

### 100,000-Record Performance Benchmark

An isolated local benchmark was executed using 200 synthetic employees and 100,000 synthetic attendance records in a separate MongoDB database (`attendance_perf_test`). The original application database remained unchanged.

| Endpoint | HTTP Status | Response Time |
|---|---|---|
| Attendance listing | 200 | 29 ms |
| Employee monthly summary | 200 | 32 ms |
| Department summary | 200 | 154 ms |
| Late leaderboard | 200 | 137 ms |
| Department trend | 200 | 172 ms |

All five corresponding explain-plan screenings reported `IXSCAN=True` and `COLLSCAN=False`.

These timings are single-run measurements on a local machine, not guaranteed performance thresholds. The explain-plan checks were screenings rather than exhaustive plan analysis.

The completed tests provide evidence of local functionality, concurrency handling, and performance with 100,000 records. They do not guarantee correctness for every R1–R10 edge case or hidden evaluation scenario.

## Implementation notes

Attendance dates and shift rules use Asia/Kolkata (IST). The implementation uses MongoDB indexes and aggregation pipelines for querying and analytics, with database-enforced uniqueness for employee identifiers and attendance days.

See `REVIEW.md` for the starter-code review and `DECISIONS.md` for design decisions.