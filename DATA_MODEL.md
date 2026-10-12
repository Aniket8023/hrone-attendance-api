# DATA_MODEL.md: what is stored in MongoDB

This file describes the two collections the grader **seeds directly into MongoDB**. Your code must read them exactly as
described here, and what your code writes must look the same. The HTTP contract and the business rules (R1 - R10) live in
`openapi.yaml`; this file refers to rules by number and does not repeat them.

| | |
|---|---|
| Database | the name in `MONGO_DB` |
| Collections | `employees`, `attendance_logs` |
| Indexes | **None** except the default `_id` index. Creating the ones you need, at startup, is your job. |

## 1. Types at a glance

| Kind of value | In MongoDB | In the HTTP API |
|---|---|---|
| An instant (a punch, a creation time, a history time) | BSON date (datetime, UTC) | integer epoch **milliseconds** |
| A calendar date (`date`, `joined_on`) | string `"YYYY-MM-DD"` | the same string |
| A time of day (`shift_start`, `shift_end`) | string `"HH:MM"`, 24-hour, IST | the same string |
| A duration | `work_hours` double, minutes as ints | the same numbers |

Calendar dates and times of day are plain strings **on purpose**: a date is not an instant, and turning it into one forces
a time zone on it. Instants are real datetimes in MongoDB, and your code is responsible for converting them to and from
epoch milliseconds at the API boundary. By default PyMongo returns **naive** datetimes (no time zone); decide how you
will handle that.

## 2. `employees`

One document per employee.

| Field | Type | Notes |
|---|---|---|
| `_id` | ObjectId | **Internal to MongoDB.** Never accepted or returned by the API; do not use it to identify employees. |
| `emp_code` | string | **The employee's identifier everywhere in this assignment.** A customer-generated business code assigned by the customer's HR team, for example `EMP0001`. It must be unique and never changes. It is **not** the `_id`. |
| `name` | string | |
| `email` | string | |
| `department` | string | Case-sensitive. |
| `shift_start` | string `"HH:MM"` | IST. Start of the employee's shift. |
| `shift_end` | string `"HH:MM"` | IST. If `shift_end` <= `shift_start`, the shift is overnight (see R1). |
| `joined_on` | string `"YYYY-MM-DD"` | First day of employment (see R7, R9). |
| `created_at` | BSON date | When the record was created. Set by the API when it creates an employee; never supplied by a client. |

Sample:
```json
{
  "emp_code": "EMP0001",
  "name": "Asha Rao",
  "email": "asha@example.com",
  "department": "Engineering",
  "shift_start": "09:30",
  "shift_end": "18:30",
  "joined_on": "2026-01-05",
  "created_at": {
    "$date": "2026-01-05T04:30:00Z"
  }
}
```

## 3. `attendance_logs`

At most **one document per (`emp_code`, `date`)**. `emp_code` points at `employees.emp_code`. There is no ObjectId reference.

| Field | Type | Notes |
|---|---|---|
| `_id` | ObjectId | Internal. Not part of the API; a record is addressed by (`emp_code`, `date`). |
| `emp_code` | string | The employee. |
| `date` | string `"YYYY-MM-DD"` | The **attendance day**, an IST calendar date, defined by R1. For an overnight shift it is the day the shift started. |
| `status` | string | Exactly one of the five values in the status table below. |
| `punch_in` | BSON date or null | Set at punch-in. Null (or missing) for `ABSENT` / `LEAVE`. |
| `punch_out` | BSON date or null | Null until the employee punches out. |
| `work_hours` | double or null | **Computed by the API**, never supplied by a client (R4). Null until `punch_out` exists. |
| `late_minutes` | int | **Computed by the API** at punch-in (R2). Missing means 0. |
| `overtime_minutes` | int | **Computed by the API** at punch-out (R3). Missing means 0. |
| `half_day` | bool | **Computed by the API** at punch-out (R5). Missing means false. |
| `history` | array | Audit trail of manual corrections. See section 4. Missing means empty. |

### Status values

| `status` | Meaning | Punch times | Counts as present (R6) | Who creates it |
|---|---|---|---|---|
| `PRESENT` | Worked on site | `punch_in` required | yes | Punch-in |
| `WFH` | Worked from home | `punch_in` required | yes | Punch-in |
| `ON_DUTY` | Official duty away from the office | `punch_in` required | yes | Punch-in |
| `ABSENT` | Did not work, not on approved leave | both null | no | Seeded back-office data, or a correction |
| `LEAVE` | On approved leave | both null | no | Seeded back-office data, or a correction |

No endpoint creates `ABSENT` or `LEAVE` records. A weekday with **no document at all** is different from an `ABSENT`
document; the day has simply not been recorded.

### Stored derived values are authoritative

`work_hours`, `late_minutes`, `overtime_minutes` and `half_day` are written by the API, and only by the API, when it
punches in, punches out or corrects a record. Analytics must **read the stored values** and not recompute them from the
punch times. In the grader's seeded data the stored values are what the analytics are defined over.

For a record your own code writes:

| Situation | `work_hours` | `late_minutes` | `overtime_minutes` | `half_day` |
|---|---|---|---|---|
| presence status, no `punch_out` yet | null | from R2 | 0 | false |
| presence status, with `punch_out` | R4 | from R2 | R3 | R5 |
| `ABSENT` or `LEAVE` | null | 0 | 0 | false |

## 4. `history`: the audit trail

`history` records **manual corrections only** (regularizations). Punch-in and punch-out are the original events and never
write to it. Each correction appends exactly one entry; entries are never edited or removed.

| Field | Type | Notes |
|---|---|---|
| `at` | BSON date | When the correction was made. |
| `by` | string | Who made it (free text, no authentication in this assignment). |
| `reason` | string | Why. |
| `changes` | object | One key per field that **actually changed**, each `{ "from": ..., "to": ... }`. Keys can be `status`, `punch_in`, `punch_out`, `work_hours`, `late_minutes`, `overtime_minutes`, `half_day`. |

In MongoDB the `from` / `to` values of `punch_in` and `punch_out` are BSON dates, like the fields themselves. In the API they
are epoch milliseconds. This is easy to forget because the dates sit inside a nested object.

## 5. Sample documents

The files in `sample_data/` hold these documents as MongoDB Extended JSON, so you can load them with
`python sample_seed.py` or `mongoimport --jsonArray` (`_id` is omitted; MongoDB generates it).

**They show the shapes only.** The grader's dataset is much larger and contains cases that these nine do not (ties, weekend
records, employees with no logs at all, people who join mid-month, and more). Passing on this sample proves nothing about
the analytics.

### 1. Normal full day
Punched in 09:28 IST, out 18:35 IST. Inside grace, overtime 5 min is under the 30-minute minimum.

```json
{
  "emp_code": "EMP0001",
  "date": "2026-07-06",
  "status": "PRESENT",
  "punch_in": {
    "$date": "2026-07-06T03:58:00Z"
  },
  "punch_out": {
    "$date": "2026-07-06T13:05:00Z"
  },
  "work_hours": 9.12,
  "late_minutes": 0,
  "overtime_minutes": 0,
  "half_day": false,
  "history": []
}
```

### 2. Late, with overtime
Punched in 10:05 IST (35 min after a 09:30 shift start), out 19:10 IST (40 min after shift end).

```json
{
  "emp_code": "EMP0003",
  "date": "2026-07-06",
  "status": "PRESENT",
  "punch_in": {
    "$date": "2026-07-06T04:35:00Z"
  },
  "punch_out": {
    "$date": "2026-07-06T13:40:00Z"
  },
  "work_hours": 9.08,
  "late_minutes": 35,
  "overtime_minutes": 40,
  "half_day": false,
  "history": []
}
```

### 3. Open record (not punched out yet)
`punch_out` is null, so `work_hours` is null and the other derived fields are at their defaults.

```json
{
  "emp_code": "EMP0006",
  "date": "2026-07-07",
  "status": "PRESENT",
  "punch_in": {
    "$date": "2026-07-07T04:05:00Z"
  },
  "punch_out": null,
  "work_hours": null,
  "late_minutes": 0,
  "overtime_minutes": 0,
  "half_day": false,
  "history": []
}
```

### 4. Half day, work from home
3.5 hours worked (< 4.50), so `half_day` is true. Counts as 0.5 of a present day.

```json
{
  "emp_code": "EMP0004",
  "date": "2026-07-07",
  "status": "WFH",
  "punch_in": {
    "$date": "2026-07-07T04:00:00Z"
  },
  "punch_out": {
    "$date": "2026-07-07T07:30:00Z"
  },
  "work_hours": 3.5,
  "late_minutes": 0,
  "overtime_minutes": 0,
  "half_day": true,
  "history": []
}
```

### 5. Absent
No punch times. Not counted as present.

```json
{
  "emp_code": "EMP0004",
  "date": "2026-07-08",
  "status": "ABSENT",
  "punch_in": null,
  "punch_out": null,
  "work_hours": null,
  "late_minutes": 0,
  "overtime_minutes": 0,
  "half_day": false,
  "history": []
}
```

### 6. Leave
No punch times. Not counted as present.

```json
{
  "emp_code": "EMP0003",
  "date": "2026-07-08",
  "status": "LEAVE",
  "punch_in": null,
  "punch_out": null,
  "work_hours": null,
  "late_minutes": 0,
  "overtime_minutes": 0,
  "half_day": false,
  "history": []
}
```

### 7. Overnight shift (22:00 - 06:00)
`date` is the day the shift **started** (2026-07-06) although the punch-out is on the 7th. Punch-in 21:55 IST, out 06:40 IST next day (40 min overtime).

```json
{
  "emp_code": "EMP0005",
  "date": "2026-07-06",
  "status": "PRESENT",
  "punch_in": {
    "$date": "2026-07-06T16:25:00Z"
  },
  "punch_out": {
    "$date": "2026-07-07T01:10:00Z"
  },
  "work_hours": 8.75,
  "late_minutes": 0,
  "overtime_minutes": 40,
  "half_day": false,
  "history": []
}
```

### 8. Regularized record (has `history`)
HR corrected the punch-in from 10:05 IST to 09:28 IST. The derived fields were recomputed and one history entry was appended (see section 4 of this document).

```json
{
  "emp_code": "EMP0001",
  "date": "2026-07-07",
  "status": "PRESENT",
  "punch_in": {
    "$date": "2026-07-07T03:58:00Z"
  },
  "punch_out": {
    "$date": "2026-07-07T13:00:00Z"
  },
  "work_hours": 9.03,
  "late_minutes": 0,
  "overtime_minutes": 0,
  "half_day": false,
  "history": [
    {
      "at": {
        "$date": "2026-07-08T05:00:00Z"
      },
      "by": "hr.admin",
      "reason": "biometric glitch",
      "changes": {
        "punch_in": {
          "from": {
            "$date": "2026-07-07T04:35:00Z"
          },
          "to": {
            "$date": "2026-07-07T03:58:00Z"
          }
        },
        "late_minutes": {
          "from": 35,
          "to": 0
        },
        "work_hours": {
          "from": 8.42,
          "to": 9.03
        }
      }
    }
  ]
}
```

### 9. Legacy record (no `history`, no `half_day`)
Older records may omit these two fields entirely. Treat missing as an empty array / false.

```json
{
  "emp_code": "EMP0002",
  "date": "2026-07-14",
  "status": "PRESENT",
  "punch_in": {
    "$date": "2026-07-14T04:02:00Z"
  },
  "punch_out": {
    "$date": "2026-07-14T13:01:00Z"
  },
  "work_hours": 8.98,
  "late_minutes": 0,
  "overtime_minutes": 0
}
```
