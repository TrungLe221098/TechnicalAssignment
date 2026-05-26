"""
Integration tests for the HR Employee API.

Requirements:
  - PostgreSQL must be reachable at DATABASE_URL (defaults to the docker-compose value)
  - Redis must be reachable at REDIS_URL (defaults to the docker-compose value)

Run:
  cd backend
  DATABASE_URL=postgresql://postgres:postgres@localhost:5432/employees \
  REDIS_URL=redis://localhost:6379/0 \
  python -m pytest test_main.py -v
  # or: python -m unittest test_main -v
"""

import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from database import Base, SessionLocal, engine
import models
from main import (
    app,
    redis_client,
    RATE_LIMIT_MAX,
    RATE_LIMIT_WINDOW,
    SEED_EMPLOYEES,
)

# ── shared helpers ────────────────────────────────────────────────────────────

# Starlette's TestClient always sets request.client.host to this value
_TEST_CLIENT_IP = "testclient"


def _flush_rate_keys(client_id: str = _TEST_CLIENT_IP) -> None:
    """Delete the current and previous sliding-window counters for a client."""
    now = time.time()
    window = int(now // RATE_LIMIT_WINDOW)
    redis_client.delete(
        f"rl:{client_id}:{window}",
        f"rl:{client_id}:{window - 1}",
    )


def _flush_cache() -> None:
    """Delete all cached employee-list keys."""
    keys = redis_client.keys("employees:*")
    if keys:
        redis_client.delete(*keys)


# ── Employee list tests ───────────────────────────────────────────────────────

class TestEmployeeList(unittest.TestCase):
    """
    Tests for GET /api/employees using the real PostgreSQL database.
    Seed data (20 employees) is inserted once per test run if the table is empty.
    """

    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        db = SessionLocal()
        try:
            if db.query(models.Employee).count() == 0:
                for emp in SEED_EMPLOYEES:
                    db.add(models.Employee(**emp))
                db.commit()
        finally:
            db.close()

        cls.client = TestClient(app, raise_server_exceptions=True)

    def setUp(self):
        _flush_rate_keys()
        _flush_cache()

    # ── status code & shape ───────────────────────────────────────────────────

    def test_returns_200(self):
        res = self.client.get("/api/employees")
        self.assertEqual(res.status_code, 200)

    def test_response_has_required_fields(self):
        data = self.client.get("/api/employees").json()
        for field in ("items", "has_next", "next_cursor", "total", "page_size"):
            self.assertIn(field, data, msg=f"Missing field: {field}")

    def test_each_item_has_employee_fields(self):
        item = self.client.get("/api/employees").json()["items"][0]
        for field in ("id", "first_name", "last_name", "contact",
                      "location", "company", "department", "position", "status"):
            self.assertIn(field, item, msg=f"Missing employee field: {field}")

    # ── total & pagination ────────────────────────────────────────────────────

    def test_total_equals_seed_count(self):
        data = self.client.get("/api/employees").json()
        self.assertEqual(data["total"], len(SEED_EMPLOYEES))

    def test_default_page_size_returns_10_items(self):
        data = self.client.get("/api/employees").json()
        self.assertEqual(len(data["items"]), 10)
        self.assertEqual(data["page_size"], 10)

    def test_has_next_true_when_total_exceeds_page_size(self):
        # 20 seed employees, page_size=10 → second page exists
        data = self.client.get("/api/employees").json()
        self.assertTrue(data["has_next"])
        self.assertIsNotNone(data["next_cursor"])

    def test_cursor_navigates_to_second_page(self):
        page1 = self.client.get("/api/employees").json()
        cursor = page1["next_cursor"]

        res2 = self.client.get(f"/api/employees?cursor={cursor}")
        self.assertEqual(res2.status_code, 200)
        page2 = res2.json()

        self.assertEqual(len(page2["items"]), 10)
        # No employee should appear on both pages
        ids1 = {e["id"] for e in page1["items"]}
        ids2 = {e["id"] for e in page2["items"]}
        self.assertTrue(ids1.isdisjoint(ids2), "Duplicate employees across pages")

    def test_last_page_has_next_false_and_no_cursor(self):
        page1 = self.client.get("/api/employees").json()
        page2 = self.client.get(
            f"/api/employees?cursor={page1['next_cursor']}"
        ).json()

        self.assertFalse(page2["has_next"])
        self.assertIsNone(page2["next_cursor"])

    def test_all_pages_cover_full_dataset(self):
        collected_ids = set()
        cursor = None

        while True:
            url = "/api/employees" if cursor is None else f"/api/employees?cursor={cursor}"
            data = self.client.get(url).json()
            for emp in data["items"]:
                collected_ids.add(emp["id"])
            if not data["has_next"]:
                break
            cursor = data["next_cursor"]

        self.assertEqual(len(collected_ids), len(SEED_EMPLOYEES))

    # ── sort order ────────────────────────────────────────────────────────────

    def test_results_sorted_by_last_name_ascending(self):
        """First item on page 1 must be the employee with the earliest last name."""
        data = self.client.get("/api/employees").json()
        first = data["items"][0]
        # Alphabetically the seed data's earliest last_name is "Brown"
        self.assertEqual(first["last_name"], "Brown")
        self.assertEqual(first["first_name"], "Karen")

    def test_last_names_within_page_are_ordered(self):
        data = self.client.get("/api/employees").json()
        last_names = [e["last_name"] for e in data["items"]]
        self.assertEqual(last_names, sorted(last_names))

    # ── sample value assertions ───────────────────────────────────────────────

    def test_sample_employee_values(self):
        """Spot-check a known seed record's field values."""
        # Karen Brown (AUS, TechVision, Marketing, Senior Engineer, Active)
        # is the first record when sorted by last name
        emp = self.client.get("/api/employees").json()["items"][0]
        self.assertEqual(emp["first_name"],  "Karen")
        self.assertEqual(emp["last_name"],   "Brown")
        self.assertEqual(emp["location"],    "AUS")
        self.assertEqual(emp["company"],     "TechVision")
        self.assertEqual(emp["department"],  "Marketing")
        self.assertEqual(emp["position"],    "Senior Engineer")
        self.assertEqual(emp["status"],      "Active")


# ── Filter tests ──────────────────────────────────────────────────────────────

class TestEmployeeFilters(unittest.TestCase):
    """Tests for every filter parameter on GET /api/employees."""

    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        db = SessionLocal()
        try:
            if db.query(models.Employee).count() == 0:
                for emp in SEED_EMPLOYEES:
                    db.add(models.Employee(**emp))
                db.commit()
        finally:
            db.close()

        cls.client = TestClient(app, raise_server_exceptions=True)

    def setUp(self):
        _flush_rate_keys()
        _flush_cache()

    def _expected(self, **kwargs):
        """Return seed employees matching all given field=value pairs."""
        return [
            e for e in SEED_EMPLOYEES
            if all(e.get(k) == v for k, v in kwargs.items())
        ]

    def test_filter_location_vn(self):
        data = self.client.get("/api/employees?location=VN").json()
        expected = self._expected(location="VN")
        self.assertEqual(data["total"], len(expected))
        for emp in data["items"]:
            self.assertEqual(emp["location"], "VN")

    def test_filter_status(self):
        data = self.client.get("/api/employees?status=Active").json()
        expected = self._expected(status="Active")
        self.assertEqual(data["total"], len(expected))
        for emp in data["items"]:
            self.assertEqual(emp["status"], "Active")

    def test_filter_name(self):
        data = self.client.get("/api/employees?name=Nguyen").json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["last_name"], "Nguyen")
        self.assertEqual(data["items"][0]["first_name"], "An")

    def test_filter_name_case_insensitive(self):
        upper = self.client.get("/api/employees?name=NGUYEN").json()["total"]
        lower = self.client.get("/api/employees?name=nguyen").json()["total"]
        mixed = self.client.get("/api/employees?name=NgUyEn").json()["total"]
        self.assertEqual(upper, lower)
        self.assertEqual(lower, mixed)
        self.assertGreater(upper, 0)

    def test_filter_no_match_returns_empty(self):
        data = self.client.get("/api/employees?name=ZZZNobodyHere").json()
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["items"], [])
        self.assertFalse(data["has_next"])
        self.assertIsNone(data["next_cursor"])


# ── Rate limit tests ──────────────────────────────────────────────────────────

class TestRateLimit(unittest.TestCase):
    """
    Tests for the Sliding Window Counter rate limiter.

    Rate limit keys have the form: rl:<client_ip>:<window_index>
    TestClient always sends request.client.host == "testclient".
    """

    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        db = SessionLocal()
        try:
            if db.query(models.Employee).count() == 0:
                for emp in SEED_EMPLOYEES:
                    db.add(models.Employee(**emp))
                db.commit()
        finally:
            db.close()

        # raise_server_exceptions=False so 429 responses are returned as-is
        # rather than being raised as exceptions
        cls.client = TestClient(app, raise_server_exceptions=False)

    def setUp(self):
        _flush_rate_keys()
        _flush_cache()

    def _set_current_window_count(self, count: int, client_id: str = _TEST_CLIENT_IP) -> str:
        """Pre-seed the current window's counter and return the key."""
        now = time.time()
        window = int(now // RATE_LIMIT_WINDOW)
        key = f"rl:{client_id}:{window}"
        redis_client.set(key, count)
        redis_client.expire(key, RATE_LIMIT_WINDOW * 2)
        return key

    # ── requests below limit are allowed ─────────────────────────────────────

    def test_first_request_is_allowed(self):
        res = self.client.get("/api/employees")
        self.assertEqual(res.status_code, 200)

    def test_multiple_requests_below_limit_are_allowed(self):
        for i in range(5):
            res = self.client.get("/api/employees")
            self.assertEqual(res.status_code, 200, msg=f"Request {i+1} was unexpectedly blocked")

    def test_counter_starts_at_zero_on_fresh_window(self):
        """After flushing, the counter should be absent (zero)."""
        now = time.time()
        window = int(now // RATE_LIMIT_WINDOW)
        key = f"rl:{_TEST_CLIENT_IP}:{window}"
        self.assertIsNone(redis_client.get(key))

        self.client.get("/api/employees")

        # After one request the counter must be exactly 1
        self.assertEqual(int(redis_client.get(key)), 1)

    # ── hitting the limit returns 429 ─────────────────────────────────────────

    def test_returns_429_when_limit_is_reached(self):
        """Pre-seed counter to MAX; the very next request must be blocked."""
        self._set_current_window_count(RATE_LIMIT_MAX)

        res = self.client.get("/api/employees")
        self.assertEqual(res.status_code, 429)

    def test_429_response_contains_detail_message(self):
        self._set_current_window_count(RATE_LIMIT_MAX)

        body = self.client.get("/api/employees").json()
        self.assertIn("detail", body)
        self.assertIn("Too many requests", body["detail"])

    def test_exact_boundary_one_below_allows_then_blocks(self):
        """
        At MAX-1 requests the next call must succeed (and increment to MAX).
        The call after that must be blocked.
        """
        self._set_current_window_count(RATE_LIMIT_MAX - 1)

        # This request brings the counter from MAX-1 to MAX → allowed
        res_allowed = self.client.get("/api/employees")
        self.assertEqual(res_allowed.status_code, 200)

        # Now counter == MAX → blocked
        res_blocked = self.client.get("/api/employees")
        self.assertEqual(res_blocked.status_code, 429)

    # ── sliding window: previous window contributes ───────────────────────────

    def test_sliding_window_high_previous_count_blocks_request(self):
        """
        Pin time to 30 % into a fake window so the math is deterministic:

            weighted = prev_count × (1 − 0.3) + current_count
                     = prev_count × 0.7

        Set prev_count = ceil(MAX / 0.7) + 1 so weighted > MAX → blocked,
        even though the current window counter is zero.
        """
        FAKE_WINDOW  = 16
        ELAPSED_FRAC = 0.3
        fake_now     = FAKE_WINDOW * RATE_LIMIT_WINDOW + ELAPSED_FRAC * RATE_LIMIT_WINDOW

        # Minimum previous count that pushes weighted over the limit
        prev_count = int(RATE_LIMIT_MAX / (1 - ELAPSED_FRAC)) + 1  # 87

        prev_key = f"rl:{_TEST_CLIENT_IP}:{FAKE_WINDOW - 1}"
        curr_key = f"rl:{_TEST_CLIENT_IP}:{FAKE_WINDOW}"
        redis_client.delete(prev_key, curr_key)
        redis_client.set(prev_key, prev_count)
        redis_client.expire(prev_key, RATE_LIMIT_WINDOW * 2)

        with patch("main.time.time", return_value=fake_now):
            res = self.client.get("/api/employees")

        self.assertEqual(res.status_code, 429)

    def test_sliding_window_low_previous_count_allows_request(self):
        """
        When both the previous and current windows are well under the limit,
        the weighted estimate is also under the limit → request is allowed.

        Pin time to 50 % into a fake window:
            weighted = 10 × (1 − 0.5) + 0 = 5  →  well under MAX
        """
        FAKE_WINDOW  = 17
        ELAPSED_FRAC = 0.5
        fake_now     = FAKE_WINDOW * RATE_LIMIT_WINDOW + ELAPSED_FRAC * RATE_LIMIT_WINDOW

        prev_key = f"rl:{_TEST_CLIENT_IP}:{FAKE_WINDOW - 1}"
        curr_key = f"rl:{_TEST_CLIENT_IP}:{FAKE_WINDOW}"
        redis_client.delete(prev_key, curr_key)
        redis_client.set(prev_key, 10)
        redis_client.expire(prev_key, RATE_LIMIT_WINDOW * 2)

        with patch("main.time.time", return_value=fake_now):
            res = self.client.get("/api/employees")

        self.assertEqual(res.status_code, 200)

    def test_sliding_window_weight_decreases_as_window_advances(self):
        """
        At 10 % elapsed: weighted = prev × 0.9  (high contribution)
        At 90 % elapsed: weighted = prev × 0.1  (low contribution)

        Use prev_count = MAX - 1 (just below the limit).
        At 10 % → (MAX-1)*0.9 < MAX → allowed.
        Set prev_count = ceil(MAX / 0.9) + 1 so at 10 % it's blocked,
        but at 90 % → (same prev) * 0.1 < MAX → allowed.
        """
        FAKE_WINDOW = 18
        prev_count  = int(RATE_LIMIT_MAX / 0.9) + 1  # 68 (with MAX=60: 68*0.9=61.2 > 60)

        prev_key = f"rl:{_TEST_CLIENT_IP}:{FAKE_WINDOW - 1}"

        # ── 10 % into window: should be blocked ──────────────────────────────
        curr_key = f"rl:{_TEST_CLIENT_IP}:{FAKE_WINDOW}"
        redis_client.delete(prev_key, curr_key)
        redis_client.set(prev_key, prev_count)
        redis_client.expire(prev_key, RATE_LIMIT_WINDOW * 2)

        fake_now_10 = FAKE_WINDOW * RATE_LIMIT_WINDOW + 0.1 * RATE_LIMIT_WINDOW
        with patch("main.time.time", return_value=fake_now_10):
            res_10 = self.client.get("/api/employees")
        self.assertEqual(res_10.status_code, 429)

        # ── 90 % into window: same prev count, should now be allowed ─────────
        curr_key_new = f"rl:{_TEST_CLIENT_IP}:{FAKE_WINDOW}"
        redis_client.delete(curr_key_new)   # reset current count

        fake_now_90 = FAKE_WINDOW * RATE_LIMIT_WINDOW + 0.9 * RATE_LIMIT_WINDOW
        with patch("main.time.time", return_value=fake_now_90):
            res_90 = self.client.get("/api/employees")
        self.assertEqual(res_90.status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
