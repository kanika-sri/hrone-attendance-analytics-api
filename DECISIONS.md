# Part C — Decisions

**Indexes.** The unique `employees.emp_code` index prevents duplicate business identifiers, while unique `(emp_code,date)` prevents duplicate attendance days. I added compound indexes for department/join-date counts, paginated attendance by date and code, employee record lookup, and status filtering. Startup creates them idempotently.

**Concurrent punch-ins.** Both requests might reach insertion simultaneously; the unique MongoDB index is the arbiter. One insert succeeds with HTTP 201; another raises `DuplicateKeyError` and becomes 409. An application-level pre-check would be unsafe.

**Ties at cutoff.** The leaderboard groups records per employee, ranks totals with `$rank`, then filters on rank. If ranks are 1, 2, 2, 4 and limit is 2, the first three employees are returned. Tied rows are sorted by `emp_code`.

**Employees with no logs.** Department aggregation starts from employees joined by the month end and uses a `$lookup` with preserved empty arrays. This includes zero-log staff in headcount but excludes future joiners. Work-hour averages are calculated over eligible records, not averages of employee averages.

**At 100x scale.** First profile explain execution stats and index selectivity. Then consider materialized daily/monthly aggregates and incremental refreshes for expensive analytics. Keep atomic attendance writes and business-key uniqueness, and assess sharding only after profiling sustained bottlenecks.
