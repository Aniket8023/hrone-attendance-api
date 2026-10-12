

import calendar
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, EmailStr, Field, StrictInt, field_validator
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError
from pymongo.collection import Collection

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "attendance_db")
client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
db = client[MONGO_DB]

IST = timezone(timedelta(hours=5, minutes=30))
MIN_EPOCH_MS = 100_000_000_000
MAX_EPOCH_MS = 4_102_444_800_000
PRESENCE_STATUSES = ("PRESENT", "WFH", "ON_DUTY")
ALL_STATUSES = ("PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY")

app = FastAPI(title="Employee Attendance & Analytics API", version="2.1.0")


@app.on_event("startup")
def create_indexes():
    db.employees.create_index("emp_code", unique=True, name="uq_employee_code")
    db.employees.create_index([("department", 1), ("emp_code", 1)], name="ix_employee_department_code")
    db.employees.create_index("joined_on", name="ix_employee_joined_on")
    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)], unique=True, name="uq_attendance_employee_date"
    )
    db.attendance_logs.create_index(
        [("date", -1), ("emp_code", 1)], name="ix_attendance_date_employee"
    )
    db.attendance_logs.create_index(
        [("status", 1), ("date", -1)], name="ix_attendance_status_date"
    )
    db.attendance_logs.create_index(
        [("date", 1), ("late_minutes", -1), ("emp_code", 1)], name="ix_attendance_date_late"
    )


# ------------------------------- Helpers -----------------------------------

def epoch_ms_to_utc(value: Optional[int] = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Timestamp must be an integer in epoch milliseconds")
    if not MIN_EPOCH_MS <= value <= MAX_EPOCH_MS:
        raise ValueError("Timestamp is outside the allowed epoch-millisecond range")
    return datetime.fromtimestamp(value // 1000, tz=timezone.utc)


def to_ist(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(IST)


def to_epoch_ms(value: Optional[datetime]) -> Optional[int]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp()) * 1000


def round_half_up(value: float, places: int = 2) -> float:
    quantum = Decimal("1").scaleb(-places)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def parse_month(month: str) -> tuple[date, date]:
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(status_code=422, detail="month must use YYYY-MM format")
    try:
        year, month_num = map(int, month.split("-"))
        first = date(year, month_num, 1)
        last = date(year, month_num, calendar.monthrange(year, month_num)[1])
        return first, last
    except ValueError:
        raise HTTPException(status_code=422, detail="month must be a valid month in YYYY-MM format")


def validate_iso_date(value: str, field_name: str = "date") -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail=f"{field_name} must use YYYY-MM-DD format")


def shift_boundary(attendance_date: str, shift_time: str) -> datetime:
    day = date.fromisoformat(attendance_date)
    hour, minute = map(int, shift_time.split(":"))
    return datetime.combine(day, time(hour, minute), tzinfo=IST)


def is_overnight(shift_start: str, shift_end: str) -> bool:
    return tuple(map(int, shift_end.split(":"))) <= tuple(map(int, shift_start.split(":")))


def attendance_date_for(local_timestamp: datetime, shift_start: str, shift_end: str) -> date:
    day = local_timestamp.date()
    if is_overnight(shift_start, shift_end):
        end_hm = tuple(map(int, shift_end.split(":")))
        if (local_timestamp.hour, local_timestamp.minute) < end_hm:
            day -= timedelta(days=1)
    return day


def compute_late_minutes(punch_in: datetime, shift_start: str, attendance_date: str) -> int:
    local_punch = to_ist(punch_in)
    start = shift_boundary(attendance_date, shift_start)
    delay_seconds = int((local_punch - start).total_seconds())
    # 10-minute grace period; exactly 10 minutes is not late.
    if delay_seconds <= 600:
        return 0
    return max(0, delay_seconds // 60)


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    return round_half_up((punch_out - punch_in).total_seconds() / 3600, 2)


def compute_overtime(
    punch_out: datetime, shift_end: str, attendance_date: str, shift_start: str
) -> int:
    local_out = to_ist(punch_out)
    end = shift_boundary(attendance_date, shift_end)
    if is_overnight(shift_start, shift_end):
        end += timedelta(days=1)
    extra_seconds = int((local_out - end).total_seconds())
    # No overtime unless the 30-minute threshold has been exceeded.
    if extra_seconds < 1800:
        return 0
    return max(0, extra_seconds // 60)


def serialize_attendance(record: dict) -> dict:
    record = dict(record)
    record.pop("_id", None)
    record.pop("id", None)
    for field in ("punch_in", "punch_out"):
        record[field] = to_epoch_ms(record.get(field))
    for entry in record.get("history", []):
        if isinstance(entry.get("at"), datetime):
            entry["at"] = to_epoch_ms(entry["at"])
        for change in entry.get("changes", {}).values():
            if isinstance(change, dict):
                for side in ("from", "to"):
                    if isinstance(change.get(side), datetime):
                        change[side] = to_epoch_ms(change[side])
    return record


def employee_monthly_pipeline(emp_code: str, start: date, end: date) -> list[dict]:
    return [
        {"$match": {"emp_code": emp_code, "date": {"$gte": start.isoformat(), "$lte": end.isoformat()}}},
        {"$addFields": {
            "parsed_date": {"$dateFromString": {"dateString": "$date"}},
            "is_present_status": {"$in": ["$status", list(PRESENCE_STATUSES)]},
        }},
        {"$group": {
            "_id": None,
            "present_days": {"$sum": {"$cond": [
                {"$and": [
                    "$is_present_status",
                    {"$gte": [{"$dayOfWeek": "$parsed_date"}, 2]},
                    {"$lte": [{"$dayOfWeek": "$parsed_date"}, 6]},
                ]},
                {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                0,
            ]}},
            "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
        }},
    ]


def department_summary_pipeline(start: date, end: date) -> list[dict]:
    start_s, end_s = start.isoformat(), end.isoformat()
    return [
        {"$match": {"joined_on": {"$lte": end_s}}},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"employee_code": "$emp_code"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$emp_code", "$$employee_code"]},
                    {"$gte": ["$date", start_s]},
                    {"$lte": ["$date", end_s]},
                ]}}},
                {"$addFields": {"day_number": {"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}}},
            ],
            "as": "attendance",
        }},
        {"$unwind": {"path": "$attendance", "preserveNullAndEmptyArrays": True}},
        {"$group": {
            "_id": {"department": "$department", "emp_code": "$emp_code"},
            "headcount": {"$first": 1},
            "present_days": {"$sum": {"$cond": [
                {"$and": [
                    {"$in": ["$attendance.status", list(PRESENCE_STATUSES)]},
                    {"$gte": ["$attendance.day_number", 2]},
                    {"$lte": ["$attendance.day_number", 6]},
                ]},
                {"$cond": [{"$eq": ["$attendance.half_day", True]}, 0.5, 1]},
                0,
            ]}},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$attendance.late_minutes", 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$attendance.late_minutes", 0]}},
            "leave_count": {"$sum": {"$cond": [{"$eq": ["$attendance.status", "LEAVE"]}, 1, 0]}},
            "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$attendance.status", "ON_DUTY"]}, 1, 0]}},
            "work_hours_total": {"$sum": {"$ifNull": ["$attendance.work_hours", 0]}},
            "work_hours_count": {"$sum": {"$cond": [{"$ne": [{"$ifNull": ["$attendance.work_hours", None]}, None]}, 1, 0]}},
        }},
        {"$group": {
            "_id": "$_id.department",
            "headcount": {"$sum": "$headcount"},
            "present_days": {"$sum": "$present_days"},
            "late_count": {"$sum": "$late_count"},
            "total_late_minutes": {"$sum": "$total_late_minutes"},
            "leave_count": {"$sum": "$leave_count"},
            "on_duty_count": {"$sum": "$on_duty_count"},
            "work_hours_total": {"$sum": "$work_hours_total"},
            "work_hours_count": {"$sum": "$work_hours_count"},
        }},
        {"$sort": {"_id": 1}},
    ]


def leaderboard_pipeline(start: date, end: date, limit: int) -> list[dict]:
    # Rank is based on late_count. Ties at the limit are retained.
    return [
        {"$match": {"date": {"$gte": start.isoformat(), "$lte": end.isoformat()}, "late_minutes": {"$gt": 0}}},
        {"$group": {"_id": "$emp_code", "late_count": {"$sum": 1}, "total_late_minutes": {"$sum": "$late_minutes"}}},
        {"$setWindowFields": {
           "sortBy": {"total_late_minutes": -1},
            "output": {"rank": {"$rank": {}}},
        }},
        {"$match": {"rank": {"$lte": limit}}},
        {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "employee"}},
        {"$unwind": "$employee"},
        {"$project": {
            "_id": 0, "emp_code": "$_id", "name": "$employee.name", "department": "$employee.department",
            "late_count": 1, "total_late_minutes": 1, "rank": 1,
        }},
        {"$sort": {"rank": 1, "emp_code": 1}},
    ]


def trend_pipeline(department: str, start: date, end: date) -> list[dict]:
    # Produce one row per date with $densify; moving average is calculated with a window.
    return [
        {"$match": {"department": department, "joined_on": {"$lte": end.isoformat()}}},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"employee_code": "$emp_code"},
            "pipeline": [{"$match": {"$expr": {"$and": [
                {"$eq": ["$emp_code", "$$employee_code"]},
                {"$gte": ["$date", start.isoformat()]},
                {"$lte": ["$date", end.isoformat()]},
            ]}}}],
            "as": "logs",
        }},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {"$group": {
            "_id": "$logs.date",
            "present_count": {"$sum": {"$cond": [{"$in": ["$logs.status", list(PRESENCE_STATUSES)]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]}, 1, 0]}},
        }},
        {"$project": {
            "_id": 0, "date": {"$ifNull": ["$_id", start.isoformat()]},
            "present_count": 1, "late_count": 1,
        }},
        {"$addFields": {"date_value": {"$dateFromString": {"dateString": "$date"}}}},
        {"$densify": {"field": "date_value", "range": {
            "step": 1, "unit": "day", "bounds": [datetime.combine(start, time.min), datetime.combine(end, time.min) + timedelta(days=1)]
        }}},
        {"$set": {"date": {"$dateToString": {"date": "$date_value", "format": "%Y-%m-%d"}}}},
        {"$lookup": {
            "from": "employees",
            "let": {"d": "$date"},
            "pipeline": [{"$match": {"$expr": {"$and": [
                {"$eq": ["$department", department]},
                {"$lte": ["$joined_on", "$$d"]},
            ]}}}, {"$count": "n"}],
            "as": "hc",
        }},
        {"$set": {
            "headcount": {"$ifNull": [{"$arrayElemAt": ["$hc.n", 0]}, 0]},
            "is_working_day": {"$and": [{"$gte": [{"$dayOfWeek": "$date_value"}, 2]}, {"$lte": [{"$dayOfWeek": "$date_value"}, 6]}]},
        }},
        {"$set": {
            "attendance_rate": {"$cond": [
                {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                {"$round": [{"$multiply": [{"$divide": ["$present_count", "$headcount"]}, 100]}, 4]},
                None,
            ]},
        }},
        {"$setWindowFields": {
            "sortBy": {"date_value": 1},
            "output": {"moving_avg_7d": {
                "$avg": "$attendance_rate",
                "window": {"range": [-6, 0], "unit": "day"},
            }},
        }},
        {"$project": {"_id": 0, "date": 1, "is_working_day": 1, "headcount": 1,
                      "present_count": 1, "late_count": 1, "attendance_rate": 1, "moving_avg_7d": 1}},
        {"$sort": {"date": 1}},
    ]


# -------------------------------- Models ------------------------------------

class EmployeeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

    @field_validator("shift_start", "shift_end")
    @classmethod
    def valid_shift_time(cls, value: str) -> str:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("Shift times must use HH:MM")
        return value

    @field_validator("joined_on")
    @classmethod
    def valid_joined_on(cls, value: str) -> str:
        validate_iso_date(value, "joined_on")
        return value

    @field_validator("name", "department", "email")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be blank")
        return value


class PunchInIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    emp_code: str
    punched_at: Optional[StrictInt] = Field(default=None, ge=MIN_EPOCH_MS, le=MAX_EPOCH_MS)
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"


class PunchOutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    emp_code: str
    punched_at: Optional[StrictInt] = Field(default=None, ge=MIN_EPOCH_MS, le=MAX_EPOCH_MS)


class RegularizeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None
    punch_in: Optional[StrictInt] = Field(default=None, ge=MIN_EPOCH_MS, le=MAX_EPOCH_MS)
    punch_out: Optional[StrictInt] = Field(default=None, ge=MIN_EPOCH_MS, le=MAX_EPOCH_MS)


# -------------------------------- Routes ------------------------------------

@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="MongoDB unavailable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="emp_code already exists")
    doc.pop("_id", None)
    doc["created_at"] = to_epoch_ms(doc["created_at"])
    return doc


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = {"department": department} if department else {}
    total = db.employees.count_documents(query)
    cursor = db.employees.find(query, {"_id": 0}).sort("emp_code", 1).skip((page - 1) * page_size).limit(page_size)
    items = []
    for employee in cursor:
        if isinstance(employee.get("created_at"), datetime):
            employee["created_at"] = to_epoch_ms(employee["created_at"])
        items.append(employee)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    if date_from:
        validate_iso_date(date_from, "date_from")
    if date_to:
        validate_iso_date(date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from cannot be after date_to")
    if status and status not in ALL_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {list(ALL_STATUSES)}")
    query = {}
    if emp_code:
        query["emp_code"] = emp_code
    if status:
        query["status"] = status
    if date_from or date_to:
        query["date"] = {}
        if date_from:
            query["date"]["$gte"] = date_from
        if date_to:
            query["date"]["$lte"] = date_to
    total = db.attendance_logs.count_documents(query)
    cursor = db.attendance_logs.find(query).sort([("date", -1), ("emp_code", 1)]).skip((page - 1) * page_size).limit(page_size)
    return {"items": [serialize_attendance(r) for r in cursor], "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    try:
        ts = epoch_ms_to_utc(body.punched_at)
    except (ValueError, TypeError, OSError):
        raise HTTPException(status_code=422, detail="Invalid timestamp")
    local_ts = to_ist(ts)
    attendance_day = attendance_date_for(local_ts, emp["shift_start"], emp["shift_end"])
    date_str = attendance_day.isoformat()
    doc = {
        "emp_code": body.emp_code,
        "date": date_str,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"], date_str),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        result = db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Already punched in for this attendance date")
    doc["id"] = str(result.inserted_id)
    doc["punch_in"] = to_epoch_ms(ts)
    return doc


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    try:
        ts = epoch_ms_to_utc(body.punched_at)
    except (ValueError, TypeError, OSError):
        raise HTTPException(status_code=422, detail="Invalid timestamp")
    attendance_day = attendance_date_for(to_ist(ts), emp["shift_start"], emp["shift_end"])
    record = db.attendance_logs.find_one({"emp_code": body.emp_code, "date": attendance_day.isoformat()})
    if record is None:
        raise HTTPException(status_code=404, detail="Attendance record not found for this shift")
    if record.get("punch_out") is not None:
        raise HTTPException(status_code=409, detail="Already punched out")
    punch_in_time = record.get("punch_in")
    if punch_in_time is None:
        raise HTTPException(status_code=409, detail="Punch-in timestamp is missing")
    if punch_in_time.tzinfo is None:
        punch_in_time = punch_in_time.replace(tzinfo=timezone.utc)
    if ts < punch_in_time:
        raise HTTPException(status_code=422, detail="Punch-out cannot be before punch-in")
    work_hours = compute_work_hours(punch_in_time, ts)
    overtime = compute_overtime(ts, emp["shift_end"], attendance_day.isoformat(), emp["shift_start"])
    result = db.attendance_logs.update_one(
        {"_id": record["_id"], "punch_out": None},
        {"$set": {"punch_out": ts, "work_hours": work_hours, "overtime_minutes": overtime, "half_day": work_hours < 4.5}},
    )
    if result.modified_count == 0:
        raise HTTPException(status_code=409, detail="Attendance record was updated concurrently")
    return serialize_attendance(db.attendance_logs.find_one({"_id": record["_id"]}))


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeIn):
    validate_iso_date(date, "date")
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if record is None:
        raise HTTPException(status_code=404, detail="Attendance record not found")
    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    requested = body.model_dump(exclude_unset=True)
    requested.pop("reason", None)
    requested.pop("regularized_by", None)
    if not requested:
        raise HTTPException(status_code=422, detail="Provide at least one field to regularize")

    old_status, old_in, old_out = record.get("status"), record.get("punch_in"), record.get("punch_out")
    new_status = requested.get("status", old_status)
    new_in = old_in
    new_out = old_out
    try:
        if "punch_in" in requested:
            new_in = epoch_ms_to_utc(requested["punch_in"]) if requested["punch_in"] is not None else None
        if "punch_out" in requested:
            new_out = epoch_ms_to_utc(requested["punch_out"]) if requested["punch_out"] is not None else None
    except (ValueError, TypeError, OSError):
        raise HTTPException(status_code=422, detail="Invalid timestamp")
    if new_in and new_out and new_out < new_in:
        raise HTTPException(status_code=422, detail="punch_out cannot be before punch_in")

    late_minutes = compute_late_minutes(new_in, employee["shift_start"], date) if new_in else 0
    work_hours = compute_work_hours(new_in, new_out) if new_in and new_out else None
    overtime = compute_overtime(new_out, employee["shift_end"], date, employee["shift_start"]) if new_in and new_out else 0
    half_day = bool(work_hours is not None and work_hours < 4.5)
    changes = {}
    if new_status != old_status:
        changes["status"] = {"from": old_status, "to": new_status}
    if new_in != old_in:
        changes["punch_in"] = {"from": to_epoch_ms(old_in), "to": to_epoch_ms(new_in)}
    if new_out != old_out:
        changes["punch_out"] = {"from": to_epoch_ms(old_out), "to": to_epoch_ms(new_out)}
    if not changes:
        raise HTTPException(status_code=422, detail="The supplied values do not change this record")
    history_entry = {
        "at": datetime.now(timezone.utc).replace(microsecond=0),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }
    result = db.attendance_logs.update_one(
        {"_id": record["_id"]},
        {"$set": {
            "status": new_status, "punch_in": new_in, "punch_out": new_out,
            "work_hours": work_hours, "late_minutes": late_minutes,
            "overtime_minutes": overtime, "half_day": half_day,
        }, "$push": {"history": history_entry}},
    )
    if result.modified_count == 0:
        raise HTTPException(status_code=409, detail="Attendance record could not be updated")
    return serialize_attendance(db.attendance_logs.find_one({"_id": record["_id"]}))


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str = Query(..., pattern=r"^\d{4}-\d{2}$")):
    start, end = parse_month(month)
    employee = db.employees.find_one({"emp_code": emp_code}, {"_id": 0, "emp_code": 1, "joined_on": 1})
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    joined = date.fromisoformat(employee["joined_on"])
    first = max(start, joined)
    working_days = 0
    if first <= end:
        for offset in range((end - first).days + 1):
            if (first + timedelta(days=offset)).weekday() < 5:
                working_days += 1
    results = list(db.attendance_logs.aggregate(employee_monthly_pipeline(emp_code, start, end)))
    stats = results[0] if results else {
        "present_days": 0, "leave_days": 0, "late_count": 0,
        "total_late_minutes": 0, "total_overtime_minutes": 0,
    }
    pct = round_half_up(stats["present_days"] / working_days * 100, 4) if working_days else None
    return {
        "emp_code": emp_code, "month": month, "working_days": working_days,
        "present_days": stats["present_days"], "leave_days": stats["leave_days"],
        "late_count": stats["late_count"], "total_late_minutes": stats["total_late_minutes"],
        "total_overtime_minutes": stats["total_overtime_minutes"], "attendance_pct": pct,
    }


@app.get("/analytics/departments/summary")
def department_summary(month: str = Query(..., pattern=r"^\d{4}-\d{2}$")):
    start, end = parse_month(month)
    results = list(db.employees.aggregate(department_summary_pipeline(start, end)))
    items = []
    for row in results:
        count = row["work_hours_count"]
        items.append({
            "department": row["_id"], "headcount": row["headcount"],
            "present_days": row["present_days"],
            "avg_work_hours": round_half_up(row["work_hours_total"] / count, 2) if count else None,
            "late_count": row["late_count"], "total_late_minutes": row["total_late_minutes"],
            "leave_count": row["leave_count"], "on_duty_count": row["on_duty_count"],
        })
    return {"month": month, "items": items}


@app.get("/analytics/leaderboard/late")
def late_arrival_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    limit: int = Query(default=10, ge=1, le=100),
):
    start, end = parse_month(month)
    items = list(db.attendance_logs.aggregate(leaderboard_pipeline(start, end, limit)))
    return {"month": month, "limit": limit, "items": items}


@app.get("/analytics/departments/{department}/trend")
def department_attendance_trend(
    department: str,
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
):
    start = validate_iso_date(from_date, "from")
    end = validate_iso_date(to_date, "to")
    if end < start:
        raise HTTPException(status_code=422, detail="'to' must be on or after 'from'")
    if (end - start).days + 1 > 92:
        raise HTTPException(status_code=422, detail="Date range cannot exceed 92 days")
    if db.employees.find_one({"department": department}) is None:
        raise HTTPException(status_code=404, detail="Department not found")
    items = list(db.employees.aggregate(trend_pipeline(department, start, end)))
    # Ensure numeric outputs have contract-defined rounding and nulls stay null.
    for row in items:
        if row.get("attendance_rate") is not None:
            row["attendance_rate"] = round_half_up(row["attendance_rate"], 4)
        if row.get("moving_avg_7d") is not None:
            row["moving_avg_7d"] = round_half_up(row["moving_avg_7d"], 4)
        row["present_count"] = int(row.get("present_count") or 0)
        row["late_count"] = int(row.get("late_count") or 0)
    return {"department": department, "from": from_date, "to": to_date, "items": items}


def explain_aggregate(collection: Collection, pipeline: list[dict]) -> dict:
    return db.command(
        "explain",
        {"aggregate": collection.name, "pipeline": pipeline, "cursor": {}},
        verbosity="executionStats",
    )


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    month: Optional[str] = None,
    emp_code: Optional[str] = None,
    department: Optional[str] = None,
    date_from: Optional[str] = Query(default=None, alias="from"),
    date_to: Optional[str] = Query(default=None, alias="to"),
    status: Optional[str] = None,
    limit: int = Query(default=10, ge=1, le=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    allowed = {"attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"}
    if endpoint not in allowed:
        raise HTTPException(status_code=422, detail=f"endpoint must be one of {sorted(allowed)}")

    if endpoint == "attendance_list":
        if date_from:
            validate_iso_date(date_from, "from")
        if date_to:
            validate_iso_date(date_to, "to")
        if date_from and date_to and date_from > date_to:
            raise HTTPException(status_code=422, detail="'from' cannot be after 'to'")
        if status and status not in ALL_STATUSES:
            raise HTTPException(status_code=422, detail="Invalid status")
        query = {}
        if emp_code:
            query["emp_code"] = emp_code
        if status:
            query["status"] = status
        if date_from or date_to:
            query["date"] = {}
            if date_from:
                query["date"]["$gte"] = date_from
            if date_to:
                query["date"]["$lte"] = date_to
        find_command = {
            "find": "attendance_logs",
            "filter": query,
            "sort": {"date": -1, "emp_code": 1},
            "skip": (page - 1) * page_size,
            "limit": page_size,
        }
        return db.command("explain", find_command, verbosity="executionStats")

    if endpoint == "employee_monthly":
        if not month or not emp_code:
            raise HTTPException(status_code=422, detail="month and emp_code are required")
        start, end = parse_month(month)
        return explain_aggregate(db.attendance_logs, employee_monthly_pipeline(emp_code, start, end))

    if endpoint == "department_summary":
        if not month:
            raise HTTPException(status_code=422, detail="month is required")
        start, end = parse_month(month)
        return explain_aggregate(db.employees, department_summary_pipeline(start, end))

    if endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(status_code=422, detail="month is required")
        start, end = parse_month(month)
        return explain_aggregate(db.attendance_logs, leaderboard_pipeline(start, end, limit))

    if endpoint == "department_trend":
        if not department or not date_from or not date_to:
            raise HTTPException(status_code=422, detail="department, from and to are required")
        start = validate_iso_date(date_from, "from")
        end = validate_iso_date(date_to, "to")
        if end < start or (end - start).days + 1 > 92:
            raise HTTPException(status_code=422, detail="Invalid date range")
        return explain_aggregate(db.employees, trend_pipeline(department, start, end))
    raise HTTPException(status_code=422, detail="Unsupported endpoint")
