# HR Employee Management

A full-stack web application that displays and searches a global employee directory across multiple office locations. Users can browse the employee list with live filtering by name, location, company, department, position, and status.

**Tech stack:** FastAPI · PostgreSQL · Redis · React · Docker

---

## Architecture

```
Browser → React (Nginx :3000) → FastAPI backend (:8000) → PostgreSQL
                                        ↕
                                      Redis
                              (cache + rate limiter)
```

---

## Performance Design

### Cursor-based Pagination

Traditional offset pagination (`LIMIT n OFFSET k`) forces the database to scan and discard `k` rows on every page request — O(k) cost that grows with each page. At millions of rows this becomes unusably slow.

This API uses **keyset (seek) pagination** instead:

- Results are sorted by `(last_name, first_name, id)` — a stable, unique ordering.
- The cursor is a base64-encoded JSON snapshot of the last row returned: `{"ln": "...", "fn": "...", "id": N}`.
- The next page is fetched with a single tuple comparison:

  ```sql
  WHERE (last_name, first_name, id) > (:ln, :fn, :id)
  ORDER BY last_name, first_name, id
  LIMIT page_size + 1
  ```

- Cost is O(1) per page regardless of how deep into the dataset you are.
- The cursor is opaque to the client and stable under concurrent inserts.

### Database Indexing

The `employees` table has indexes on the columns used for sorting and filtering:

| Column | Index | Purpose |
|---|---|---|
| `id` | Primary Key (B-tree) | Unique row identity, cursor tie-break |
| `(last_name, first_name, id)` | Composite B-tree | Satisfies the cursor `WHERE` clause and `ORDER BY` in one index scan |
| `location`, `company`, `department`, `position`, `status` | Individual B-tree | Accelerate single-column filter predicates |
| `first_name`, `last_name` | B-tree (for `ILIKE`) | Speed up partial-name searches (pair with `pg_trgm` GIN index for full `ILIKE` at scale) |

With these indexes the database never performs a sequential table scan for any supported query shape.

### Redis Cache

Repeated queries (same filters + same cursor + same page size) are served from Redis instead of hitting PostgreSQL:

- **Cache key:** a sorted, deterministic string of all query parameters, e.g. `employees:company=Accenture:location=VN:page_size=10`
- **TTL:** 60 seconds — fresh enough for near-real-time data, old enough to absorb bursty traffic.
- **Graceful degradation:** if Redis is unavailable, the request falls through to the database transparently.

At millions of rows and thousands of concurrent users, caching the most-queried pages collapses database load dramatically while keeping response times in the single-digit millisecond range.

---

## Rate Limiting — Sliding Window Counter

Each client IP is limited to **60 requests per 60-second window**.

### Algorithm

Time is divided into fixed 60-second buckets. For each request the system reads two Redis counters — the current bucket and the previous bucket — and computes a weighted estimate:

```
weighted = prev_count × (1 − elapsed_fraction) + current_count
```

Where `elapsed_fraction` is how far (0.0–1.0) we are through the current 60-second window.

- At the start of a window (`elapsed_fraction ≈ 0`) the previous window contributes almost fully, preventing burst abuse across the boundary.
- At the end of a window (`elapsed_fraction ≈ 1`) only the current window matters.

If `weighted ≥ 60` the request is rejected with **HTTP 429**.

### Redis Keys

```
rl:<client_ip>:<window_index>      # current window counter
rl:<client_ip>:<window_index - 1>  # previous window counter (TTL = 120 s)
```

Both keys are read in a single pipelined round-trip. The current key is incremented only when the request is allowed. If Redis is unavailable the check is skipped and the request passes through.

---

## Running with Docker

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (includes Docker Compose)

### Steps

**1. Clone the repository**

```bash
git clone <repo-url>
cd TechnicalAssignment
```

**2. Build and start all services**

```bash
docker compose up --build
```

This starts four containers in dependency order:

| Container | Role | Internal port |
|---|---|---|
| `hr-db` | PostgreSQL 16 | 5432 |
| `hr-redis` | Redis 7 | 6379 |
| `hr-backend` | FastAPI (Uvicorn) | 8000 |
| `hr-frontend` | React app (Nginx) | 80 → **3000** |

The backend seeds 20 sample employees automatically on first startup.

**3. Open the app in your browser**

```
http://localhost:3000
```

**4. (Optional) Explore the API directly**

```
http://localhost:3000/api/employees
http://localhost:3000/api/options
```

**5. Stop all services**

```bash
docker compose down
```

To also remove the database volume (wipes all data):

```bash
docker compose down -v
```

---

## Running Unit Tests

The test suite requires live PostgreSQL and Redis connections. Run it inside the backend container while the stack is up:

```bash
# Start the stack first (if not already running)
docker compose up -d

# Run tests
docker compose exec backend python -m pytest test_main.py -v
```

Or with the built-in `unittest` runner:

```bash
docker compose exec backend python -m unittest test_main -v
```

---

## Unit Test Cases

### `TestEmployeeList` — core list endpoint behaviour

| # | Test | What it checks |
|---|---|---|
| 1 | `test_returns_200` | `GET /api/employees` responds with HTTP 200 |
| 2 | `test_response_has_required_fields` | Response body contains `items`, `has_next`, `next_cursor`, `total`, `page_size` |
| 3 | `test_each_item_has_employee_fields` | Every employee object has all nine fields (`id`, `first_name`, `last_name`, `contact`, `location`, `company`, `department`, `position`, `status`) |
| 4 | `test_total_equals_seed_count` | `total` matches the 20 seeded employees |
| 5 | `test_default_page_size_returns_10_items` | Default call returns exactly 10 items with `page_size=10` |
| 6 | `test_has_next_true_when_total_exceeds_page_size` | `has_next=true` and a non-null `next_cursor` when more pages exist |
| 7 | `test_cursor_navigates_to_second_page` | Using `next_cursor` fetches page 2 with no duplicate IDs |
| 8 | `test_last_page_has_next_false_and_no_cursor` | Final page has `has_next=false` and `next_cursor=null` |
| 9 | `test_all_pages_cover_full_dataset` | Walking all pages via cursors yields every one of the 20 employees |
| 10 | `test_results_sorted_by_last_name_ascending` | First result is "Karen Brown" (earliest last name alphabetically) |
| 11 | `test_last_names_within_page_are_ordered` | Last names on page 1 are in ascending alphabetical order |
| 12 | `test_sample_employee_values` | Spot-check: Karen Brown has correct location, company, department, position, and status |

### `TestEmployeeFilters` — filter parameters

| # | Test | What it checks |
|---|---|---|
| 13 | `test_filter_location_vn` | `location=VN` returns only VN employees and correct total |
| 14 | `test_filter_status` | `status=Active` returns only Active employees and correct total |
| 15 | `test_filter_name` | `name=Nguyen` matches the one employee with that last name |
| 16 | `test_filter_name_case_insensitive` | `NGUYEN`, `nguyen`, and `NgUyEn` all return the same count |
| 17 | `test_filter_no_match_returns_empty` | A name that matches nobody returns `total=0`, empty `items`, `has_next=false` |

### `TestRateLimit` — sliding window counter

| # | Test | What it checks |
|---|---|---|
| 18 | `test_first_request_is_allowed` | First request in a fresh window returns HTTP 200 |
| 19 | `test_multiple_requests_below_limit_are_allowed` | 5 consecutive requests all return 200 |
| 20 | `test_counter_starts_at_zero_on_fresh_window` | Counter is absent before first request; equals 1 immediately after |
| 21 | `test_returns_429_when_limit_is_reached` | Pre-seeding counter to MAX causes the next request to return 429 |
| 22 | `test_429_response_contains_detail_message` | 429 body has a `detail` field containing "Too many requests" |
| 23 | `test_exact_boundary_one_below_allows_then_blocks` | Request at MAX−1 succeeds (counter → MAX); the very next request is blocked |
| 24 | `test_sliding_window_high_previous_count_blocks_request` | A high previous-window count (`prev × 0.7 > MAX`) blocks even when current window is 0 |
| 25 | `test_sliding_window_low_previous_count_allows_request` | A low previous-window count (`prev × 0.5 = 5`) allows the request through |
| 26 | `test_sliding_window_weight_decreases_as_window_advances` | Same `prev_count` blocks at 10 % elapsed (`× 0.9`) but allows at 90 % elapsed (`× 0.1`) |
