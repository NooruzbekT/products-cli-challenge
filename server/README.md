# Products API — Reference Server

A small [FastAPI](https://fastapi.tiangolo.com/) service that backs the CLI
assignment. It provides token-based authentication and a products CRUD API on
top of a seeded SQLite database.

> You normally don't need to change anything here — just run it and point the CLI
> at it. You *may* modify the server if your approach calls for it (see the
> [top-level README](../README.md)); if you do, explain what you changed and why.

## Changes made for this submission

This server was updated to run on Python 3.13.5. Its dependencies were pinned to
versions that cannot be installed there at all (`pydantic==2.7.4` needs
`pydantic-core==2.18.4`, which publishes no cp313 wheels), so they were moved to
current releases, and `passlib` — unmaintained since 2020, broken with
bcrypt 4.1+, and dependent on the `crypt` module that PEP 594 removed in
Python 3.13 — was replaced by direct `bcrypt` calls in
`app/tables/users.py` and `app/services/auth_service.py`.

No endpoint, schema, token lifetime, request budget, or authorization rule was
changed. The full rationale is in [`../cli/README.md`](../cli/README.md#changes-made-to-the-server).
The test suite below passes unmodified.

## Running

From the repository root with Docker:

```bash
docker compose up --build
```

Or directly with [uv](https://docs.astral.sh/uv/) from this folder:

```bash
cd server
uv sync
uv run uvicorn app.main:app --port 8000
```

- Base URL: `http://localhost:8000`
- Interactive docs (Swagger UI): `http://localhost:8000/docs`
- Health check: `GET /health` → `{"status": "ok"}`

> **The database resets on every startup.** On boot the server deletes the SQLite
> file and re-seeds a fixed set of 15 products, so runs are deterministic. Any
> products you create/update/delete are lost when the server restarts.

## Demo users

| Username  | Password      |
|-----------|---------------|
| `demo`    | `password123` |
| `admin`   | `admin123`    |

## Authentication

Auth uses short-lived JWT access tokens plus rotating refresh tokens. All state
is kept **in memory**, so restarting the server invalidates every issued token.

- `POST /auth/login` with `{username, password}` → `{access_token, refresh_token, token_type}`.
- Send the access token on every protected request:
  `Authorization: Bearer <access_token>`.
- An access token expires when **either** limit is reached, whichever comes first:
  - a number of authenticated requests (`MAX_REQUESTS_PER_TOKEN`, default **20**), or
  - a wall-clock lifetime (`ACCESS_TOKEN_TTL_SECONDS`, default **60** seconds).
  After that, protected endpoints return **HTTP 401**.
- Every protected response echoes the current budget so clients can refresh
  pre-emptively:
  - `X-Token-Requests-Used` — requests made with this token so far.
  - `X-Token-Requests-Limit` — the configured request limit.
  - `X-Token-Expires-At` — ISO-8601 UTC expiry timestamp.
- On `401`, `POST /auth/refresh` with `{refresh_token}` to get a new pair.
  **Refresh tokens are rotated** — the old refresh token is invalidated, so always
  persist the newest pair.

## Endpoints

### Auth

| Method | Path            | Body                   | Response                                    |
|--------|-----------------|------------------------|---------------------------------------------|
| POST   | `/auth/login`   | `{username, password}` | `{access_token, refresh_token, token_type}` |
| POST   | `/auth/refresh` | `{refresh_token}`      | `{access_token, refresh_token, token_type}` |

### Products

All product endpoints require a valid `Authorization: Bearer <access_token>` header.

| Method | Path             | Purpose                             |
|--------|------------------|-------------------------------------|
| GET    | `/products`      | List products (filters below)       |
| GET    | `/products/{id}` | Get one product                     |
| POST   | `/products`      | Create a product                    |
| PATCH  | `/products/{id}` | Update fields of one product        |
| DELETE | `/products/{id}` | Delete a product                    |

A product has: `id`, `name`, `section`, `description`, `discount`, `price`.

`GET /products` query parameters:

| Parameter      | Type    | Meaning                                     |
|----------------|---------|---------------------------------------------|
| `section`      | string  | Exact section match                         |
| `name`         | string  | Case-insensitive substring match            |
| `min_price`    | float   | Minimum price (inclusive)                   |
| `max_price`    | float   | Maximum price (inclusive)                   |
| `has_discount` | bool    | `true` = discounted only, `false` = none    |
| `limit`        | int     | Page size, 1–200 (default 50)               |
| `offset`       | int     | Rows to skip (default 0)                     |

`min_price` must be `<= max_price`, otherwise the endpoint returns **HTTP 400**.

`GET /products` returns a paginated envelope rather than a bare array:

```json
{
  "items": [ { "id": 1, "name": "...", "section": "...", "description": "...", "discount": 0.0, "price": 25.0 } ],
  "pagination": { "limit": 50, "offset": 0, "count": 1, "total": 15 }
}
```

- `count` — items in this page. `total` — items matching the filter, ignoring `limit`/`offset`.

> Reads are served locally and are fast. Every catalog **mutation** (create,
> update, delete) is published to a downstream event bus / audit log before the
> response returns; that remote publish adds latency to each write. There is
> intentionally **no bulk/batch endpoint**.

## Configuration (env vars)

| Variable                 | Default       | Meaning                                          |
|--------------------------|---------------|--------------------------------------------------|
| `MAX_REQUESTS_PER_TOKEN` | `20`          | Authenticated requests allowed per access token. |
| `ACCESS_TOKEN_TTL_SECONDS` | `60`        | Access-token lifetime (seconds) before expiry.   |
| `REFRESH_TOKEN_TTL_SECONDS` | `3600`     | Refresh-token lifetime (seconds) before re-login.|
| `DOWNSTREAM_EVENT_BUS_LATENCY_SECONDS` | `0.07`       | Round-trip latency (s) of publishing a mutation to the downstream event bus / audit log (writes only). |
| `DATABASE_PATH`          | `products.db` | SQLite file location.                            |
| `JWT_SECRET`             | dev value     | HMAC secret for signing JWTs.                    |
| `JWT_ALGORITHM`          | `HS256`       | JWT signing algorithm.                           |

## Project layout

```
server/
├── Dockerfile
├── pyproject.toml
├── app/
│   ├── main.py            # FastAPI app + lifespan (resets & seeds the DB), middleware
│   ├── config.py          # Settings (env-var configurable)
│   ├── db.py              # SQLite connection lifecycle + init/reset
│   ├── auth.py            # Bearer dependency: resolves the current user from a token
│   ├── dependencies.py    # FastAPI providers pulling services off app.state
│   ├── logging_config.py  # loguru sink + stdlib logging interception
│   ├── schemas.py         # Pydantic request/response schemas
│   ├── controllers/       # HTTP routes (thin)
│   │   ├── auth_controller.py      # /auth login + refresh routes
│   │   └── products_controller.py  # /products routes
│   ├── services/          # Business logic
│   │   ├── auth_service.py          # Authentication + local authorization check
│   │   ├── auth_token_service.py    # Access/refresh lifecycle + request budget
│   │   └── products_service.py      # Product operations + event publishing
│   ├── repositories/      # Data access (SQL)
│   │   ├── products_repository.py
│   │   └── user_repository.py
│   ├── gateways/          # Clients for external services
│   │   └── event_bus_gateway.py     # Downstream event bus / audit log (writes)
│   └── tables/            # Per-table schema (DDL) + deterministic seed data
│       ├── products.py
│       └── users.py
└── tests/                 # pytest suite
```

## Tests

```bash
cd server
uv run pytest
```
