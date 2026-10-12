openapi: 3.0.3
info:
  title: Employee Attendance & Analytics API
  version: 2.0.0
  description: |
    Contract for the HROne engineering assignment. Your FastAPI app must follow this document exactly: paths, methods,
    parameter names, status codes and response field names. All code lives in `app/main.py` and must start with
    `uvicorn app.main:app --port 8000`.

    **Where things are defined.** This file defines the HTTP contract and the business rules R1-R10. What is stored in
    MongoDB (collections, field types, what the grader seeds, nine sample documents) is defined in `DATA_MODEL.md`.

    ## Configuration
    `MONGO_URI` and `MONGO_DB` are read from environment variables (a local `.env` is fine; real environment variables
    must win). Create all indexes at startup, idempotently.

    ## Conventions used by every endpoint
    * **Employees are identified by `emp_code`.** It is a customer-generated business code assigned by the customer's HR
      team (this API does not generate it). It is unique, never changes, and is meant for humans. It is **not** MongoDB's
      `_id`. `_id` is internal: it never appears in a request or a response. Attendance records have no id either; a
      record is addressed by (`emp_code`, `date`).
    * **Instants are epoch milliseconds.** Every point in time in a request or response (`punched_at`, `punch_in`,
      `punch_out`, `created_at`, `history[].at`, and the time values inside `history[].changes`) is an integer count of
      **milliseconds** since 1970-01-01T00:00:00Z. There is no time zone to get wrong. Values that look like seconds
      (below 100000000000), floats, strings and out-of-range numbers are rejected with 422. MongoDB stores these as BSON
      datetimes.
    * **Calendar values are strings.** Dates are `YYYY-MM-DD` (an IST calendar date), months are `YYYY-MM`, shift times
      are `HH:MM` (24-hour, IST).
    * **Derived fields are read-only.** `work_hours`, `late_minutes`, `overtime_minutes` and `half_day` are computed by the
      API from the punch times, the employee's shift and rules R2-R5. They never appear in a request body. If a client
      sends one, ignore it.

    ## Status values
    | `status` | Meaning | Punch times | Counts as present (R6) | Created by |
    |---|---|---|---|---|
    | `PRESENT` | Worked on site | `punch_in` required | yes | punch-in |
    | `WFH` | Worked from home | `punch_in` required | yes | punch-in |
    | `ON_DUTY` | Official duty away from the office | `punch_in` required | yes | punch-in |
    | `ABSENT` | Did not work, no approved leave | both null | no | seeded data, or a correction |
    | `LEAVE` | On approved leave | both null | no | seeded data, or a correction |

    No endpoint creates `ABSENT` or `LEAVE` records. A weekday with **no record at all** is different from an `ABSENT`
    record.

    ## Who computes what
    | Field | Computed when | From |
    |---|---|---|
    | `late_minutes` | punch-in, and after every correction | `punch_in`, shift start (R2) |
    | `work_hours`, `overtime_minutes`, `half_day` | punch-out, and after every correction | `punch_in`, `punch_out`, shift end (R3-R5) |
    | `created_at` | employee creation | server clock |

    For analytics, **read the stored derived values**; do not recompute them from the punch times.

    ## History
    `history` is the audit trail of **manual corrections only**. Punch-in and punch-out never write to it. Each
    successful `PATCH` appends exactly one entry `{at, by, reason, changes}`: when (epoch ms), who (`regularized_by`),
    why (`reason`), and for every field that **actually changed**, `{"from": ..., "to": ...}`. Entries are never edited or
    removed. A record that has never been corrected has `history: []`.

    ## Business rules (R1-R10) - these are NOT visible in the schemas below
    * **R1 Time zone and attendance date.** Shift times and attendance dates are IST (UTC+05:30). A record's `date` is the
      IST calendar date of its `punch_in` instant. Exception - overnight shifts (`shift_end` <= `shift_start`, e.g.
      22:00-06:00): a punch-in whose IST time is *earlier than* `shift_end` belongs to the shift that started the previous
      day. The shift end of an overnight shift falls on the next calendar day. Before any calculation, instants are
      truncated to whole seconds (09:40:00.900 counts as 09:40:00), and the truncated value is what is stored and returned.
    * **R2 Late.** Grace period is 10 minutes. A punch-in is late only if it is *strictly more than* 10:00 after
      `shift_start`. When late, `late_minutes` = whole minutes (floor) elapsed since `shift_start` - not since the end of
      the grace period. Otherwise 0. Example (shift 09:30): 09:40:00 -> 0; 09:40:01 -> 10; 10:15:59 -> 45.
    * **R3 Overtime.** `overtime_minutes` = whole minutes (floor) between `shift_end` and `punch_out`, counted only when
      that is >= 30; otherwise 0.
    * **R4 Work hours.** (`punch_out` - `punch_in`) in seconds / 3600, rounded to 2 decimals **half-up** (not banker's
      rounding, not float rounding). `null` until punch-out.
    * **R5 Half day.** After punch-out, `half_day` = true when the **rounded** `work_hours` < 4.50. A half day counts
      as 0.5 of a present day.
    * **R6 Presence.** `PRESENT`, `WFH` and `ON_DUTY` count as present. `ABSENT` and `LEAVE` do not.
    * **R7 Working days.** Mon-Fri, no holiday calendar. For an employee who joined mid-month, working days are counted
      from `joined_on`. `present_days` only counts records that fall on Mon-Fri (weekend records still contribute to
      late and overtime totals).
    * **R8 Rounding of reported numbers.** 2 decimals half-up (rates: 4 decimals half-up).
    * **R9 Headcount.** Employees whose `joined_on` is on or before the end of the period (the last day of the month, or
      the given day for the trend), whether or not they have any logs.
    * **R10 Pagination.** `page` starts at 1. `page_size` max is 100 (default 20). `total` always reflects the applied filters.

    ## Errors
    Errors use FastAPI's default body, `{"detail": ...}`. Validation failures are 422.
  contact:
    name: HROne Engineering
tags:
  - name: system
  - name: employees
  - name: attendance
  - name: analytics
  - name: admin
paths:
  /health:
    get:
      tags: [system]
      operationId: health
      summary: Readiness probe
      description: Returns 200 only if MongoDB answers a ping, otherwise 503. The runner polls this before testing.
      responses:
        '200':
          description: Ready
          content:
            application/json:
              schema:
                type: object
                required: [status]
                properties:
                  status: {type: string, enum: [ok]}
        '503': {$ref: '#/components/responses/Error'}
  /employees:
    post:
      tags: [employees]
      operationId: createEmployee
      summary: Create an employee
      description: |
        The client supplies `emp_code` (the customer's own code). It must be unique, **enforced by a database unique
        index** (concurrent duplicate creates must yield exactly one 201 and the rest 409). `shift_start` must differ
        from `shift_end`. `created_at` is set by the server. There is no endpoint to change an `emp_code`.
      requestBody:
        required: true
        content:
          application/json:
            schema: {$ref: '#/components/schemas/EmployeeCreate'}
      responses:
        '201':
          description: Created
          content:
            application/json:
              schema: {$ref: '#/components/schemas/Employee'}
        '409': {$ref: '#/components/responses/Error'}
        '422': {$ref: '#/components/responses/ValidationError'}
    get:
      tags: [employees]
      operationId: listEmployees
      summary: List employees
      description: Sorted by `emp_code` ascending. `total` counts the filtered set, not the whole collection.
      parameters:
        - name: department
          in: query
          required: false
          schema: {type: string}
          description: Exact, case-sensitive match.
        - {$ref: '#/components/parameters/Page'}
        - {$ref: '#/components/parameters/PageSize'}
      responses:
        '200':
          description: A page of employees
          content:
            application/json:
              schema: {$ref: '#/components/schemas/EmployeePage'}
        '422': {$ref: '#/components/responses/ValidationError'}
  /attendance/punch-in:
    post:
      tags: [attendance]
      operationId: punchIn
      summary: Record a punch-in
      description: |
        Creates the day's record. `date` follows R1 and `late_minutes` follows R2; `punch_out` and `work_hours` start as
        `null`, `overtime_minutes` as 0, `half_day` as false, `history` as `[]`. If `punched_at` is omitted, use the
        current time. `status` defaults to `PRESENT`; only `PRESENT`, `WFH` and `ON_DUTY` are accepted here.
        **Concurrency:** if the same employee punches in twice for the same attendance date at the same moment,
        exactly one request gets 201 and the rest get 409. Do not rely on check-then-insert.
        Errors: 404 unknown employee; 409 already punched in for that date; 422 invalid body.
      requestBody:
        required: true
        content:
          application/json:
            schema: {$ref: '#/components/schemas/PunchInRequest'}
      responses:
        '201':
          description: Created
          content:
            application/json:
              schema: {$ref: '#/components/schemas/AttendanceRecord'}
        '404': {$ref: '#/components/responses/Error'}
        '409': {$ref: '#/components/responses/Error'}
        '422': {$ref: '#/components/responses/ValidationError'}
  /attendance/punch-out:
    post:
      tags: [attendance]
      operationId: punchOut
      summary: Record a punch-out
      description: |
        Closes the employee's most recent record whose `punch_in` is at or before `punched_at` (this must work across
        midnight for overnight shifts). Computes `work_hours` (R4), `overtime_minutes` (R3) and `half_day` (R5).
        Errors: 404 unknown employee, or no punch-in found; 409 that record is already punched out (two simultaneous
        punch-outs must yield exactly one 200); 422 if `punched_at` <= `punch_in` or more than 24 hours after it.
      requestBody:
        required: true
        content:
          application/json:
            schema: {$ref: '#/components/schemas/PunchOutRequest'}
      responses:
        '200':
          description: Updated record
          content:
            application/json:
              schema: {$ref: '#/components/schemas/AttendanceRecord'}
        '404': {$ref: '#/components/responses/Error'}
        '409': {$ref: '#/components/responses/Error'}
        '422': {$ref: '#/components/responses/ValidationError'}
  /attendance:
    get:
      tags: [attendance]
      operationId: listAttendance
      summary: List attendance records
      description: |
        Sorted by `date` descending, then `emp_code` ascending. Must stay fast on a collection of ~100,000 records;
        do not load the collection into memory. `date_from`/`date_to` are inclusive; `date_from` > `date_to` -> 422.
      parameters:
        - {name: emp_code, in: query, required: false, schema: {type: string}}
        - {name: date_from, in: query, required: false, schema: {type: string, format: date}}
        - {name: date_to, in: query, required: false, schema: {type: string, format: date}}
        - {name: status, in: query, required: false, schema: {$ref: '#/components/schemas/Status'}}
        - {$ref: '#/components/parameters/Page'}
        - {$ref: '#/components/parameters/PageSize'}
      responses:
        '200':
          description: A page of records
          content:
            application/json:
              schema: {$ref: '#/components/schemas/AttendancePage'}
        '422': {$ref: '#/components/responses/ValidationError'}
  /attendance/{emp_code}/{date}:
    patch:
      tags: [attendance]
      operationId: regularizeAttendance
      summary: Regularize (manually correct) one day's record
      description: |
        The record is addressed by its natural key: the employee's `emp_code` and the attendance `date`
        (`YYYY-MM-DD`, the record's `date` field).
        Omitted fields are unchanged. After applying the change, **recompute** `late_minutes`, `work_hours`,
        `overtime_minutes` and `half_day` from the final times (R2-R5) and **append** one entry to `history` (never
        overwrite it); see "History" above. `history[].changes` contains only the fields that actually changed.
        Rules: setting status `ABSENT` or `LEAVE` clears both punch times, so supplying `punch_in` or `punch_out` with it
        is 422; a presence status requires a `punch_in` (supplied or already stored); `punch_in` must stay on the record's
        attendance date (R1); `punch_out` must be after `punch_in` and within 24 hours; a request that changes nothing
        is 422. 404 if the employee does not exist or has no record for that date. Concurrent edits must not silently
        lose each other's history (409 is acceptable for the loser).
      parameters:
        - {name: emp_code, in: path, required: true, schema: {type: string}}
        - {name: date, in: path, required: true, schema: {type: string, format: date}, description: "The record's attendance date, YYYY-MM-DD."}
      requestBody:
        required: true
        content:
          application/json:
            schema: {$ref: '#/components/schemas/RegularizeRequest'}
      responses:
        '200':
          description: Updated record
          content:
            application/json:
              schema: {$ref: '#/components/schemas/AttendanceRecord'}
        '404': {$ref: '#/components/responses/Error'}
        '409': {$ref: '#/components/responses/Error'}
        '422': {$ref: '#/components/responses/ValidationError'}
  /analytics/employees/{emp_code}/monthly:
    get:
      tags: [analytics]
      operationId: employeeMonthly
      summary: Monthly attendance summary for one employee
      description: |
        Backed by a MongoDB aggregation. `working_days` per R7. `present_days` per R5-R7 (half day = 0.5, Mon-Fri only).
        `leave_days` = number of `LEAVE` records. `late_count` = records with `late_minutes` > 0.
        `attendance_pct` = `present_days` / `working_days` x 100, R8 rounding, or `null` when `working_days` is 0.
        404 for an unknown employee.
      parameters:
        - {name: emp_code, in: path, required: true, schema: {type: string}}
        - {$ref: '#/components/parameters/Month'}
      responses:
        '200':
          description: Summary
          content:
            application/json:
              schema: {$ref: '#/components/schemas/EmployeeMonthly'}
        '404': {$ref: '#/components/responses/Error'}
        '422': {$ref: '#/components/responses/ValidationError'}
  /analytics/departments/summary:
    get:
      tags: [analytics]
      operationId: departmentSummary
      summary: Department summary for a month
      description: |
        One item per department, sorted by `department` ascending, via a MongoDB aggregation across both collections.
        * `headcount` per R9 - **people with zero logs still count**, people who join later do not.
        * `present_days` as in the employee summary, summed over the department.
        * `avg_work_hours` = mean `work_hours` over records that are present-status **and** have a `work_hours` value
          (average over records, not an average of per-employee averages); `null` if there are none. R8 rounding.
        * `late_count` = records with `late_minutes` > 0; `total_late_minutes` = their sum.
        * `leave_count` = `LEAVE` records; `on_duty_count` = `ON_DUTY` records.
        A department whose headcount is 0 for the month is omitted. Department comes from the `employees` collection.
      parameters:
        - {$ref: '#/components/parameters/Month'}
        - name: department
          in: query
          required: false
          schema: {type: string}
          description: Restrict to one department (exact match).
      responses:
        '200':
          description: Summary
          content:
            application/json:
              schema: {$ref: '#/components/schemas/DepartmentSummary'}
        '422': {$ref: '#/components/responses/ValidationError'}
  /analytics/leaderboard/late:
    get:
      tags: [analytics]
      operationId: lateLeaderboard
      summary: Late-comers leaderboard for a month
      description: |
        Ranks employees by total `late_minutes` in the month (only employees with a total > 0), highest first.
        * **Ranking is standard competition ranking** - ties share a rank and the next rank is skipped
          (e.g. 1, 2, 2, 4). Do not use the array position as the rank.
        * `limit` applies **after** ranking: return every employee whose `rank` <= `limit`. The response can therefore
          contain more than `limit` rows when employees tie at a rank <= `limit`.
        * Order of rows: `total_late_minutes` descending, then `emp_code` ascending.
        * When `department` is given, ranking happens inside that department only.
        `name` and `department` come from `employees`; logs whose employee does not exist are ignored.
      parameters:
        - {$ref: '#/components/parameters/Month'}
        - {name: limit, in: query, required: false, schema: {type: integer, minimum: 1, maximum: 50, default: 10}}
        - {name: department, in: query, required: false, schema: {type: string}}
      responses:
        '200':
          description: Leaderboard
          content:
            application/json:
              schema: {$ref: '#/components/schemas/Leaderboard'}
        '422': {$ref: '#/components/responses/ValidationError'}
  /analytics/departments/{department}/trend:
    get:
      tags: [analytics]
      operationId: departmentTrend
      summary: Daily attendance trend for a department
      description: |
        Returns **exactly one row per calendar day** from `from` to `to` inclusive - weekends and days with no records
        at all included (use a gap-filling stage such as `$densify`; do not loop in Python).
        * `is_working_day` = Mon-Fri.
        * `headcount` = department employees with `joined_on` <= that day (R9).
        * `present_count` = presence-status records that day (half day = 0.5), any weekday or weekend.
        * `late_count` = records that day with `late_minutes` > 0.
        * `attendance_rate` = `present_count` / `headcount`, 4 decimals half-up, **only on working days with headcount > 0,
          otherwise `null`**. A working day with no records has rate 0.0.
        * `moving_avg_7d` = mean of the non-null `attendance_rate` values among the current row and up to 6 preceding rows
          **inside the requested range** (rows before `from` are not considered, so the first rows use a shorter
          window). `null` if the window holds no non-null rate. 4 decimals half-up.
        Errors: 404 unknown department (no employees); 422 if `to` < `from` or the range exceeds 92 days.
      parameters:
        - {name: department, in: path, required: true, schema: {type: string}}
        - {name: from, in: query, required: true, schema: {type: string, format: date}}
        - {name: to, in: query, required: true, schema: {type: string, format: date}}
      responses:
        '200':
          description: Trend
          content:
            application/json:
              schema: {$ref: '#/components/schemas/Trend'}
        '404': {$ref: '#/components/responses/Error'}
        '422': {$ref: '#/components/responses/ValidationError'}
  /admin/explain/{endpoint}:
    get:
      tags: [admin]
      operationId: explainEndpoint
      summary: MongoDB explain for an endpoint's main query
      description: |
        Runs `explain` with `executionStats` verbosity for the **main query or aggregation of the named endpoint,
        built exactly as that endpoint builds it** (same filters, sort, pagination), and returns the raw explain output.
        Accepts the same query parameters as the endpoint it explains (for `department_trend` pass `department`,
        `from`, `to`; for `employee_monthly` pass `emp_code`, `month`; etc.). Missing required parameters -> 422.
        The grader checks that the plan contains `IXSCAN` and no `COLLSCAN`, on a ~100k-record dataset.
      parameters:
        - name: endpoint
          in: path
          required: true
          schema:
            type: string
            enum: [attendance_list, employee_monthly, department_summary, late_leaderboard, department_trend]
        - {name: emp_code, in: query, required: false, schema: {type: string}}
        - {name: month, in: query, required: false, schema: {type: string, pattern: '^\d{4}-(0[1-9]|1[0-2])$'}}
        - {name: department, in: query, required: false, schema: {type: string}}
        - {name: limit, in: query, required: false, schema: {type: integer, minimum: 1, maximum: 50, default: 10}}
        - {name: date_from, in: query, required: false, schema: {type: string, format: date}}
        - {name: date_to, in: query, required: false, schema: {type: string, format: date}}
        - {name: status, in: query, required: false, schema: {$ref: '#/components/schemas/Status'}}
        - {name: from, in: query, required: false, schema: {type: string, format: date}}
        - {name: to, in: query, required: false, schema: {type: string, format: date}}
        - {$ref: '#/components/parameters/Page'}
        - {$ref: '#/components/parameters/PageSize'}
      responses:
        '200':
          description: Explain output
          content:
            application/json:
              schema:
                type: object
                required: [endpoint, collection, explain]
                properties:
                  endpoint: {type: string}
                  collection: {type: string, description: Collection the explained operation ran on.}
                  explain: {type: object, additionalProperties: true, description: Raw MongoDB explain document as JSON.}
        '422': {$ref: '#/components/responses/ValidationError'}
components:
  parameters:
    Page:
      name: page
      in: query
      required: false
      schema: {type: integer, minimum: 1, default: 1}
    PageSize:
      name: page_size
      in: query
      required: false
      schema: {type: integer, minimum: 1, maximum: 100, default: 20}
    Month:
      name: month
      in: query
      required: true
      description: Calendar month, `YYYY-MM`.
      schema: {type: string, pattern: '^\d{4}-(0[1-9]|1[0-2])$'}
  responses:
    Error:
      description: Error
      content:
        application/json:
          schema:
            type: object
            required: [detail]
            properties:
              detail: {type: string}
    ValidationError:
      description: Validation error (FastAPI default body)
      content:
        application/json:
          schema:
            type: object
            required: [detail]
            properties:
              detail: {type: array, items: {type: object, additionalProperties: true}}
  schemas:
    EpochMillis:
      type: integer
      format: int64
      minimum: 100000000000
      maximum: 4102444800000
      description: |
        An instant as milliseconds since 1970-01-01T00:00:00Z. Seconds-looking values (below 100000000000), floats and
        strings are rejected with 422.
      example: 1783312500000
    EpochMillisNullable:
      type: integer
      format: int64
      nullable: true
      minimum: 100000000000
      maximum: 4102444800000
      description: An instant as epoch milliseconds, or null.
      example: 1783312500000
    Status:
      type: string
      enum: [PRESENT, ABSENT, LEAVE, WFH, ON_DUTY]
      description: See the status table in the API description.
    PresenceStatus:
      type: string
      enum: [PRESENT, WFH, ON_DUTY]
    EmployeeCreate:
      type: object
      required: [emp_code, name, email, department, joined_on]
      properties:
        emp_code:
          type: string
          pattern: '^EMP\d{4,6}$'
          example: EMP0001
          description: |
            The customer's own employee code, supplied by the client. Unique and immutable. Not MongoDB's `_id`.
            Format for this assignment: `EMP` followed by 4-6 digits.
        name: {type: string, minLength: 1, maxLength: 100}
        email: {type: string, maxLength: 120, pattern: '^[^@\s]+@[^@\s]+\.[^@\s]+$'}
        department: {type: string, minLength: 1, maxLength: 50}
        shift_start: {type: string, pattern: '^([01]\d|2[0-3]):[0-5]\d$', default: '09:30', description: "24-hour HH:MM, IST."}
        shift_end: {type: string, pattern: '^([01]\d|2[0-3]):[0-5]\d$', default: '18:30', description: "24-hour HH:MM, IST. <= shift_start means an overnight shift."}
        joined_on: {type: string, format: date, description: "First day of employment, YYYY-MM-DD."}
    Employee:
      type: object
      required: [emp_code, name, email, department, shift_start, shift_end, joined_on, created_at]
      properties:
        emp_code: {type: string, description: "The customer-generated employee code. Unique, immutable. Not the MongoDB _id."}
        name: {type: string}
        email: {type: string}
        department: {type: string}
        shift_start: {type: string}
        shift_end: {type: string}
        joined_on: {type: string, format: date}
        created_at:
          readOnly: true
          allOf:
            - {$ref: '#/components/schemas/EpochMillis'}
          description: Server-set creation time, epoch milliseconds.
    EmployeePage:
      type: object
      required: [items, total, page, page_size]
      properties:
        items: {type: array, items: {$ref: '#/components/schemas/Employee'}}
        total: {type: integer}
        page: {type: integer}
        page_size: {type: integer}
    PunchInRequest:
      type: object
      required: [emp_code]
      properties:
        emp_code: {type: string}
        punched_at:
          allOf:
            - {$ref: '#/components/schemas/EpochMillis'}
          description: Epoch milliseconds. Defaults to now.
        status:
          allOf:
            - {$ref: '#/components/schemas/PresenceStatus'}
          default: PRESENT
    PunchOutRequest:
      type: object
      required: [emp_code]
      properties:
        emp_code: {type: string}
        punched_at:
          allOf:
            - {$ref: '#/components/schemas/EpochMillis'}
          description: Epoch milliseconds. Defaults to now.
    RegularizeRequest:
      type: object
      required: [reason, regularized_by]
      description: Only these fields can be changed. Derived fields can not be sent.
      properties:
        status: {$ref: '#/components/schemas/Status'}
        punch_in: {$ref: '#/components/schemas/EpochMillis'}
        punch_out: {$ref: '#/components/schemas/EpochMillis'}
        reason: {type: string, minLength: 5, maxLength: 200}
        regularized_by: {type: string, minLength: 1, maxLength: 50}
    HistoryEntry:
      type: object
      required: [at, by, reason, changes]
      properties:
        at: {$ref: '#/components/schemas/EpochMillis'}
        by: {type: string}
        reason: {type: string}
        changes:
          type: object
          description: |
            One key per field that actually changed (`status`, `punch_in`, `punch_out`, `work_hours`, `late_minutes`,
            `overtime_minutes`, `half_day`). The `from` / `to` values of `punch_in` and `punch_out` are epoch
            milliseconds (or null); the others keep their normal types.
          additionalProperties:
            type: object
            properties:
              from: {nullable: true}
              to: {nullable: true}
          example:
            punch_in: {from: 1783312500000, to: 1783310280000}
            late_minutes: {from: 35, to: 0}
    AttendanceRecord:
      type: object
      required: [emp_code, date, status, punch_in, punch_out, work_hours, late_minutes, overtime_minutes, half_day, history]
      properties:
        emp_code: {type: string}
        date: {type: string, format: date, description: "The attendance day (R1), YYYY-MM-DD."}
        status: {$ref: '#/components/schemas/Status'}
        punch_in: {$ref: '#/components/schemas/EpochMillisNullable'}
        punch_out: {$ref: '#/components/schemas/EpochMillisNullable'}
        work_hours: {type: number, nullable: true, readOnly: true, description: Computed (R4). Null until punch-out.}
        late_minutes: {type: integer, readOnly: true, description: Computed (R2).}
        overtime_minutes: {type: integer, readOnly: true, description: Computed (R3).}
        half_day: {type: boolean, readOnly: true, description: Computed (R5).}
        history: {type: array, items: {$ref: '#/components/schemas/HistoryEntry'}}
    AttendancePage:
      type: object
      required: [items, total, page, page_size]
      properties:
        items: {type: array, items: {$ref: '#/components/schemas/AttendanceRecord'}}
        total: {type: integer}
        page: {type: integer}
        page_size: {type: integer}
    EmployeeMonthly:
      type: object
      required: [emp_code, month, working_days, present_days, leave_days, late_count, total_late_minutes, total_overtime_minutes, attendance_pct]
      properties:
        emp_code: {type: string}
        month: {type: string}
        working_days: {type: integer}
        present_days: {type: number}
        leave_days: {type: integer}
        late_count: {type: integer}
        total_late_minutes: {type: integer}
        total_overtime_minutes: {type: integer}
        attendance_pct: {type: number, nullable: true}
    DepartmentSummary:
      type: object
      required: [month, items]
      properties:
        month: {type: string}
        items:
          type: array
          items:
            type: object
            required: [department, headcount, present_days, avg_work_hours, late_count, total_late_minutes, leave_count, on_duty_count]
            properties:
              department: {type: string}
              headcount: {type: integer}
              present_days: {type: number}
              avg_work_hours: {type: number, nullable: true}
              late_count: {type: integer}
              total_late_minutes: {type: integer}
              leave_count: {type: integer}
              on_duty_count: {type: integer}
    Leaderboard:
      type: object
      required: [month, items]
      properties:
        month: {type: string}
        items:
          type: array
          items:
            type: object
            required: [rank, emp_code, name, department, total_late_minutes, late_count]
            properties:
              rank: {type: integer, minimum: 1}
              emp_code: {type: string}
              name: {type: string}
              department: {type: string}
              total_late_minutes: {type: integer}
              late_count: {type: integer}
    Trend:
      type: object
      required: [department, items]
      properties:
        department: {type: string}
        items:
          type: array
          items:
            type: object
            required: [date, is_working_day, headcount, present_count, late_count, attendance_rate, moving_avg_7d]
            properties:
              date: {type: string, format: date}
              is_working_day: {type: boolean}
              headcount: {type: integer}
              present_count: {type: number}
              late_count: {type: integer}
              attendance_rate: {type: number, nullable: true}
              moving_avg_7d: {type: number, nullable: true}
