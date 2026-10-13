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

The following checks were performed against a running local MongoDB instance:

- Python syntax and route registration
- Employee creation and duplicate employee handling
- Attendance punch-in and punch-out
- Attendance derivation and timestamp conversion
- Monthly employee analytics and department summaries
- Late leaderboard ranking and cutoff ties
- Department trend generation, `$densify`, and moving averages
- Aggregation explain plans for five supported queries
- Concurrent punch-in: one successful creation and nine conflicts
- Concurrent punch-out: one successful update and nine conflicts
- Concurrent regularization: two successful updates with two additional history entries

Local explain checks reported `IXSCAN` and no `COLLSCAN` for the tested queries.

These checks were performed on a small local dataset. They do not constitute full verification of every R1–R10 edge case, performance with 100,000 attendance records, or all hidden evaluation scenarios.

## Implementation notes

Attendance dates and shift rules use Asia/Kolkata (IST). The implementation uses MongoDB indexes and aggregation pipelines for querying and analytics, with database-enforced uniqueness for employee identifiers and attendance days.

See `REVIEW.md` for the starter-code review and `DECISIONS.md` for design decisions.