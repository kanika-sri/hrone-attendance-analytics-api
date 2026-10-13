# Part A — Starter review

| Where | What's wrong and how to notice | Fix |
|---|---|---|
| `health` | MongoDB stopped: reports 200 incorrectly. | Ping database, return 503 on failure. |
| `create_employee` | Two simultaneous duplicate creates can both pass the lookup. | Unique business-code index and catch `DuplicateKeyError`. |
| `create_employee` | `datetime.now()` stores machine-local naive creation instant. | UTC clock and epoch-ms conversion at response boundary. |
| `EmployeeIn` | Invalid code, email, dates, shifts, equal shifts can be accepted. | Contract-shaped Pydantic validation. |
| `list_employees` | Page 1 skips first 20 (`skip=page*size`). | `skip=(page-1)*size`. |
| `list_employees` | Filtered department still reports unfiltered `total`. | Count same filter. |
| `list_employees` | No stable `emp_code` ordering; page size unbounded. | Indexed sort and query bounds. |
| `compute_late_minutes` | For a 09:30 shift, 09:40:01 must return 10 late minutes, but the starter truncates elapsed minutes before checking the grace threshold and returns 0. | Compare elapsed seconds strictly against 600, then floor the full minutes since shift start. |
| `compute_work_hours` | Binary floating-point and banker's rounding at ties. | `Decimal` with `ROUND_HALF_UP`. |
| `compute_overtime` | 1 extra minute incorrectly counted and overnight shift end incorrect. | Require >= 30 complete minutes and shift-end on proper date. |
| `punch_in` | Unknown employee leads to 500 by indexing `None`. | Return 404. |
| `punch_in` | `if body.punched_at` confuses missing and falsey values. | Explicit `is None` decision and strict validation. |
| `punch_in` | Machine-local date instead of IST / previous night shift. | IST date calculation using R1. |
| `punch_in` | Arbitrary attendance status accepted. | Presence-status literal validation. |
| `punch_in` | Check then insert is not race-safe. | Unique `(emp_code,date)` database index. |
| `punch_in` | Leaks MongoDB `_id` as public `id`. | Exclude internal identifiers. |
| `list_attendance` | Fetches every document and sorts in Python, bad at 100k records. | Sort, skip, limit in indexed MongoDB query. |
| `list_attendance` | Unstable sorting when dates tie. | Sort `date` descending then `emp_code` ascending. |
| `list_attendance` | Raw datetime objects and `id` exposed; seeded optional fields absent. | Serialize timestamps and default `history=[]`, `half_day=false`. |
| `list_attendance` | No date format/range and page-size validation. | Validate according to contract, return 422. |
| startup | No indexes created. | Idempotent indexes on business keys and query access paths. |

## Examined and retained

Using `emp_code` as the public employee identity is correct; attendance remains addressed by `(emp_code,date)`. The starter's default employee shift hours and default punch-in `PRESENT` are consistent with the contract.
