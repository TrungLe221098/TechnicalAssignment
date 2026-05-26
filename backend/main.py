import base64
import json
import os
import time
import redis
from fastapi import FastAPI, Depends, Query, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import tuple_
from sqlalchemy.orm import Session
from typing import Optional

import models
import schemas
from database import engine, get_db, Base, SessionLocal

redis_client = redis.from_url(
    os.getenv("REDIS_URL", "redis://redis:6379/0"),
    decode_responses=True,
)

# ── fixed option values ───────────────────────────────────────────────────────
LOCATIONS   = ["VN", "SGP", "AUS"]
COMPANIES   = ["Accenture", "TechVision", "DataSync"]
DEPARTMENTS = ["HR", "Engineering", "Finance", "Marketing", "Operations"]
POSITIONS   = ["Director", "Manager", "Senior Engineer", "Analyst", "Junior Engineer"]
STATUSES    = ["Active", "Not Started", "Terminated"]

# ── seed data ─────────────────────────────────────────────────────────────────
SEED_EMPLOYEES = [
    {"first_name": "An",      "last_name": "Nguyen",  "contact": "+84901111001", "location": "VN",  "company": "Accenture",  "department": "Engineering", "position": "Senior Engineer",  "status": "Active"},
    {"first_name": "Binh",    "last_name": "Tran",    "contact": "+84901111002", "location": "VN",  "company": "TechVision", "department": "HR",          "position": "Manager",          "status": "Active"},
    {"first_name": "Cuong",   "last_name": "Le",      "contact": "+84901111003", "location": "VN",  "company": "DataSync",   "department": "Finance",     "position": "Analyst",          "status": "Not Started"},
    {"first_name": "Dung",    "last_name": "Pham",    "contact": "+84901111004", "location": "VN",  "company": "Accenture",  "department": "Operations",  "position": "Director",         "status": "Active"},
    {"first_name": "Emma",    "last_name": "Tan",     "contact": "+6591111001",  "location": "SGP", "company": "TechVision", "department": "Marketing",   "position": "Manager",          "status": "Active"},
    {"first_name": "Farid",   "last_name": "Hassan",  "contact": "+6591111002",  "location": "SGP", "company": "Accenture",  "department": "Engineering", "position": "Junior Engineer",  "status": "Active"},
    {"first_name": "Grace",   "last_name": "Lim",     "contact": "+6591111003",  "location": "SGP", "company": "DataSync",   "department": "Finance",     "position": "Senior Engineer",  "status": "Terminated"},
    {"first_name": "Hao",     "last_name": "Chen",    "contact": "+6591111004",  "location": "SGP", "company": "TechVision", "department": "HR",          "position": "Analyst",          "status": "Not Started"},
    {"first_name": "Isabella","last_name": "Wong",    "contact": "+6591111005",  "location": "SGP", "company": "Accenture",  "department": "Operations",  "position": "Director",         "status": "Active"},
    {"first_name": "Jack",    "last_name": "Smith",   "contact": "+61411111001", "location": "AUS", "company": "DataSync",   "department": "Engineering", "position": "Manager",          "status": "Active"},
    {"first_name": "Karen",   "last_name": "Brown",   "contact": "+61411111002", "location": "AUS", "company": "TechVision", "department": "Marketing",   "position": "Senior Engineer",  "status": "Active"},
    {"first_name": "Liam",    "last_name": "Johnson", "contact": "+61411111003", "location": "AUS", "company": "Accenture",  "department": "Finance",     "position": "Junior Engineer",  "status": "Not Started"},
    {"first_name": "Mia",     "last_name": "Taylor",  "contact": "+61411111004", "location": "AUS", "company": "DataSync",   "department": "HR",          "position": "Analyst",          "status": "Active"},
    {"first_name": "Nam",     "last_name": "Vo",      "contact": "+84901111005", "location": "VN",  "company": "TechVision", "department": "Engineering", "position": "Director",         "status": "Terminated"},
    {"first_name": "Oliver",  "last_name": "Wilson",  "contact": "+61411111005", "location": "AUS", "company": "Accenture",  "department": "Operations",  "position": "Manager",          "status": "Active"},
    {"first_name": "Phuong",  "last_name": "Dang",    "contact": "+84901111006", "location": "VN",  "company": "DataSync",   "department": "Marketing",   "position": "Junior Engineer",  "status": "Active"},
    {"first_name": "Quinn",   "last_name": "Lee",     "contact": "+6591111006",  "location": "SGP", "company": "Accenture",  "department": "Engineering", "position": "Analyst",          "status": "Not Started"},
    {"first_name": "Ryan",    "last_name": "Martin",  "contact": "+61411111006", "location": "AUS", "company": "TechVision", "department": "Finance",     "position": "Senior Engineer",  "status": "Terminated"},
    {"first_name": "Sara",    "last_name": "Kim",     "contact": "+6591111007",  "location": "SGP", "company": "DataSync",   "department": "HR",          "position": "Director",         "status": "Active"},
    {"first_name": "Tung",    "last_name": "Hoang",   "contact": "+84901111007", "location": "VN",  "company": "Accenture",  "department": "Operations",  "position": "Manager",          "status": "Active"},
]


def seed_db():
    db = SessionLocal()
    try:
        if db.query(models.Employee).count() == 0:
            for emp in SEED_EMPLOYEES:
                db.add(models.Employee(**emp))
            db.commit()
    finally:
        db.close()


# ── app ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="HR Employee Management API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── rate limiting (sliding window counter) ────────────────────────────────────
RATE_LIMIT_WINDOW = 60   # window size in seconds
RATE_LIMIT_MAX    = 60   # max requests allowed per window per client

def is_rate_limited(client_id: str) -> bool:
    """
    Sliding Window Counter using Redis.

    Splits time into fixed windows of RATE_LIMIT_WINDOW seconds.
    The effective count is a weighted blend of the previous window's count
    and the current window's count, proportional to how far we are into
    the current window:

        weighted = prev_count * (1 - elapsed_fraction) + current_count

    This smooths the hard boundary of a plain fixed-window counter while
    keeping only two O(1) Redis keys per client.

    Returns True if the request should be rejected (rate limit exceeded).
    """
    now            = time.time()
    current_window = int(now // RATE_LIMIT_WINDOW)
    prev_window    = current_window - 1
    elapsed_frac   = (now % RATE_LIMIT_WINDOW) / RATE_LIMIT_WINDOW

    current_key = f"rl:{client_id}:{current_window}"
    prev_key    = f"rl:{client_id}:{prev_window}"

    # Fetch both counters in a single round-trip
    pipe = redis_client.pipeline(transaction=False)
    pipe.get(prev_key)
    pipe.get(current_key)
    prev_raw, current_raw = pipe.execute()

    prev_count    = int(prev_raw    or 0)
    current_count = int(current_raw or 0)

    weighted = prev_count * (1 - elapsed_frac) + current_count
    if weighted >= RATE_LIMIT_MAX:
        return True

    # Increment current window; TTL = 2 windows so previous key stays readable
    pipe = redis_client.pipeline(transaction=False)
    pipe.incr(current_key)
    pipe.expire(current_key, RATE_LIMIT_WINDOW * 2)
    pipe.execute()

    return False


# ── cache helpers ─────────────────────────────────────────────────────────────
CACHE_TTL = 60  # seconds

def make_cache_key(**kwargs) -> str:
    parts = sorted(f"{k}={v}" for k, v in kwargs.items() if v is not None)
    return "employees:" + ":".join(parts)


# ── cursor helpers ────────────────────────────────────────────────────────────
def encode_cursor(last_name: str, first_name: str, id: int) -> str:
    payload = json.dumps({"ln": last_name, "fn": first_name, "id": id})
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_cursor(cursor: str) -> tuple:
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        return data["ln"], data["fn"], data["id"]
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid cursor")


@app.on_event("startup")
async def startup():
    Base.metadata.create_all(bind=engine)
    seed_db()


# ── endpoints ─────────────────────────────────────────────────────────────────
@app.get("/api/employees", response_model=schemas.CursorPage)
def get_employees(
    request: Request,
    name: Optional[str] = Query(None, description="Partial match on first or last name"),
    location: Optional[str] = Query(None),
    company: Optional[str] = Query(None),
    department: Optional[str] = Query(None),
    position: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    cursor: Optional[str] = Query(None, description="Opaque cursor from previous response"),
    page_size: int = Query(10, ge=1, le=100, description="Items per page"),
    db: Session = Depends(get_db),
):
    # ── rate limit ────────────────────────────────────────────────────────────
    try:
        if is_rate_limited(request.client.host):
            raise HTTPException(status_code=429, detail="Too many requests. Please slow down.")
    except HTTPException:
        raise
    except Exception:
        pass  # Redis unavailable — allow request through

    cache_key = make_cache_key(
        name=name, location=location, company=company,
        department=department, position=position, status=status,
        cursor=cursor, page_size=page_size,
    )

    # ── cache read ────────────────────────────────────────────────────────────
    try:
        cached = redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass  # Redis unavailable — fall through to DB

    # ── DB query ──────────────────────────────────────────────────────────────
    q = db.query(models.Employee)

    if name:
        q = q.filter(
            models.Employee.first_name.ilike(f"%{name}%")
            | models.Employee.last_name.ilike(f"%{name}%")
        )
    if location:
        q = q.filter(models.Employee.location == location)
    if company:
        q = q.filter(models.Employee.company == company)
    if department:
        q = q.filter(models.Employee.department == department)
    if position:
        q = q.filter(models.Employee.position == position)
    if status:
        q = q.filter(models.Employee.status == status)

    q = q.order_by(models.Employee.last_name, models.Employee.first_name, models.Employee.id)

    total = q.count()

    if cursor:
        ln, fn, cid = decode_cursor(cursor)
        q = q.filter(
            tuple_(models.Employee.last_name, models.Employee.first_name, models.Employee.id)
            > tuple_(ln, fn, cid)
        )

    rows = q.limit(page_size + 1).all()
    has_next = len(rows) > page_size
    items = rows[:page_size]

    next_cursor = None
    if has_next:
        last = items[-1]
        next_cursor = encode_cursor(last.last_name, last.first_name, last.id)

    result = schemas.CursorPage(
        items=items,
        next_cursor=next_cursor,
        has_next=has_next,
        total=total,
        page_size=page_size,
    )

    # ── cache write ───────────────────────────────────────────────────────────
    try:
        redis_client.setex(cache_key, CACHE_TTL, result.model_dump_json())
    except Exception:
        pass  # Redis unavailable — serve response without caching

    return result


@app.get("/api/options")
def get_options():
    return {
        "locations":   LOCATIONS,
        "companies":   COMPANIES,
        "departments": DEPARTMENTS,
        "positions":   POSITIONS,
        "statuses":    STATUSES,
    }
