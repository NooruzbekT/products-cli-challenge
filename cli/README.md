# Products CLI

A command-line client for the Products API in [`../server`](../server).

Implemented with **argparse** (standard library) and **httpx**. The only runtime
dependency is `httpx` — the CLI adds nothing else.

## Requirements

- Python **3.13.5** (the version this was developed and tested against; the
  package declares `requires-python = ">=3.10"`)
- [uv](https://docs.astral.sh/uv/)

## Quick start

**1. Start the API server** (from the repository root):

```bash
docker compose up --build
```

or, without Docker:

```bash
cd server
uv sync
uv run uvicorn app.main:app --port 8000
```

**2. Install and run the CLI:**

```bash
cd cli
uv sync
uv run products-cli login --base-url http://localhost:8000 --username demo --password password123
uv run products-cli products list
```

`uv sync` creates `cli/.venv` and installs the project. After that the
`products-cli` executable also lives at `cli/.venv/bin/products-cli`
(`cli\.venv\Scripts\products-cli.exe` on Windows), so it can be called directly
instead of through `uv run`.

## Commands

Every data command prints **only** JSON to stdout and exits `0`. On failure it
prints a message to **stderr** and exits `1`, leaving stdout empty.

### `login`

```bash
uv run products-cli login --base-url http://localhost:8000 --username demo --password password123
# {"status": "ok"}
```

Authenticates and stores the base URL together with the token pair. `--base-url`
is required here and only here — every `products` subcommand reads it back from
disk.

### `products list`

```bash
uv run products-cli products list
uv run products-cli products list --section electronics
uv run products-cli products list --name mouse
uv run products-cli products list --min-price 40 --max-price 100
uv run products-cli products list --has-discount
uv run products-cli products list --no-discount
uv run products-cli products list --limit 5 --offset 10
```

The API returns a paginated envelope; this command prints the bare `items`
array:

```json
[{"id": 1, "name": "Wireless Mouse", "section": "electronics", "description": "Ergonomic 2.4GHz wireless mouse", "discount": 0.0, "price": 25.0}]
```

`--has-discount` and `--no-discount` are mutually exclusive. When neither is
given, the `has_discount` query parameter is omitted entirely rather than sent
as `false`. The same rule applies to every other filter: only flags actually
passed are forwarded to the API.

### `products get`

```bash
uv run products-cli products get --id 1
# {"id": 1, "name": "Wireless Mouse", ...}
```

### `products create`

```bash
uv run products-cli products create --name "Desk Lamp" --section furniture --price 45.0
uv run products-cli products create --name "Desk Lamp" --section furniture --price 45.0 --description "LED desk lamp" --discount 10
```

Prints the created product. `--name`, `--section` and `--price` are required.

### `products update`

```bash
uv run products-cli products update --id 1 --price 27.5
uv run products-cli products update --id 1 --name "Wireless Mouse Pro" --discount 15
```

Sends a `PATCH` containing only the fields you passed, and prints the updated
product. Passing `--description ""` clears the description; omitting a flag
leaves that field untouched.

### `products delete`

```bash
uv run products-cli products delete --id 16
# {"status": "ok"}
```

**Deleting requires the `admin` account.** The server restricts
`products:delete` to the `ADMIN` role, so `demo` gets an HTTP 403. Log in as
`admin` / `admin123` first:

```bash
uv run products-cli login --base-url http://localhost:8000 --username admin --password admin123
uv run products-cli products delete --id 16
```

### `products batch-update`

```bash
uv run products-cli products batch-update --section furniture --discount 30
# {"updated": 2}
```

Sets the discount on every product in a section and prints how many were
updated.

### `--base-url` override

Every `products` subcommand accepts an optional `--base-url` to point a single
call at a different host without re-running `login`. It is never required; the
stored value is used by default.

## Design decisions

### Where tokens are stored

`~/.products-cli/credentials.json`, holding the base URL and the current token
pair. The file lives in the user's home directory, so it is outside the
repository and cannot be committed by accident. It is written atomically (to a
temporary file, then `os.replace`) so an interrupted refresh cannot leave a
half-written file behind, and the directory and file are given `0700` / `0600`
permissions.

Note that `chmod` is only meaningful on POSIX systems. On Windows it toggles the
read-only bit and does not restrict other users; there, the file is protected by
the ACL on the user's profile directory rather than by permission bits. Tokens
are never printed to stdout or stderr.

Setting `PRODUCTS_CLI_HOME` overrides the directory, which is useful for running
several isolated sessions or for testing.

### Refresh strategy: reactive, not proactive

Refresh is **purely reactive** — triggered by a `401`, never by the
`X-Token-Requests-Used` / `X-Token-Expires-At` headers. Each authenticated
request follows exactly the flow the assignment specifies:

1. send the stored access token;
2. if the status is not `401`, return the response;
3. if it is `401`, `POST` the stored refresh token to `/auth/refresh`, persist
   the new pair to disk, and retry the original request once.

Reactive was chosen deliberately. The proactive variant has to duplicate the
server's budget accounting client-side, and it still needs the reactive path as
a fallback, because a token can be invalidated by things the headers do not
describe — a server restart, for instance, drops all in-memory token state. That
is a second mechanism to keep correct in exchange for saving one round trip. The
headers are still sent by the server and can be adopted later without changing
the command layer.

The retry is deliberately capped at one attempt. If the request fails again, the
refresh token itself is no longer usable, and the right answer is to tell the
user to log in again rather than to loop.

Because the rotated pair is written to disk on every refresh, this works both
within a single process (`batch-update` making dozens of calls) and across
separate invocations.

### `batch-update`: sequential loop, server untouched

The server has no bulk endpoint. This implementation pages through
`GET /products?section=...` to collect every matching product, then issues one
`PATCH` per product.

Two alternatives were considered:

- **Adding a bulk endpoint to the server.** This is the fastest option — one
  request instead of N, and it could update the rows in a single transaction, so
  a partial failure would not leave the section half-updated. It was rejected
  because the assignment specifically poses "no bulk endpoint exists" as the
  constraint to solve, and because it moves the work into a component the
  validator may run from its own copy. In a real system with large sections this
  would be the correct fix, and the CLI-side loop is what you write while
  waiting for that endpoint to ship.
- **Concurrent PATCHes.** Writes carry ~0.4 s of simulated downstream latency,
  so concurrency would give a real wall-clock win. It was rejected because it
  interacts badly with the refresh flow: N in-flight requests hitting the token
  budget produce N simultaneous `401`s, and each one racing to `/auth/refresh`
  would rotate the refresh token out from under the others. Doing it correctly
  means serialising refresh behind a lock and having the losers wait and retry
  with the winner's token — a meaningful amount of concurrency-correctness code
  for a command that operates on tens of items.

The sequential loop is O(N) requests and takes roughly `0.4 × N` seconds, but it
is straightforward and keeps the refresh logic trivially correct. It is not
atomic: if a `PATCH` fails midway, the products already updated stay updated,
the error goes to stderr, and the exit code is non-zero. The count printed on
success reflects products actually updated.

Products are collected in full **before** any `PATCH` is issued, rather than
updating each page as it is fetched. Paginating and mutating at the same time
risks skipped or repeated rows if the result ordering shifts underneath the
cursor.

### Why argparse

The validator asserts command and flag names literally, and argparse gives
direct control over every one of them, including the `--has-discount` /
`--no-discount` pair, with no name mangling in between. It also keeps the
dependency set at exactly one package.

### Structure

| Module | Responsibility |
|---|---|
| `src/products_cli/__init__.py` | `main()`, the argparse command tree, JSON output |
| `src/products_cli/config.py` | reading and writing the credentials file |
| `src/products_cli/auth.py` | login, refresh, and the authenticated-request session |
| `src/products_cli/client.py` | wrapper over the products endpoints |
| `src/products_cli/errors.py` | `CliError`, the single error type `main()` catches |

Anything raised as a `CliError` becomes a stderr message and exit code `1`.
HTTP failures are reported with the method, path, status and the server's
`detail` field, for example:

```
GET /products/9999 failed with HTTP 404: not found
```

## Changes made to the server

The server's dependencies were pinned to versions that predate Python 3.13 and
**cannot be installed on it at all**: `pydantic==2.7.4` requires
`pydantic-core==2.18.4`, which publishes no cp313 wheels, so installing it needs
a Rust toolchain to build from source. Running the server on 3.13.5 therefore
required updating them.

| Package | Before | After |
|---|---|---|
| fastapi | 0.111.0 | 0.141.1 |
| uvicorn[standard] | 0.30.1 | 0.52.4 |
| pydantic | 2.7.4 | 2.13.5 |
| pydantic-settings | 2.3.4 | 2.15.0 |
| PyJWT | 2.8.0 | 2.13.0 |
| bcrypt | 4.0.1 | 5.0.0 |
| loguru | 0.7.2 | 0.7.3 |
| passlib | 1.7.4 | *removed* |

`passlib` was dropped rather than kept: it has been unmaintained since 2020, it
reads `bcrypt.__about__.__version__`, which was removed in bcrypt 4.1, and it
imports the stdlib `crypt` module, which PEP 594 removed in Python 3.13. Its
only use here was `CryptContext(schemes=["bcrypt"])`, so the two call sites now
use the `bcrypt` package directly:

- `server/app/tables/users.py` — `hash_password()` wrapping `bcrypt.hashpw`
- `server/app/services/auth_service.py` — `verify_password()` wrapping
  `bcrypt.checkpw`

Supporting changes: `server/.python-version` and `cli/.python-version` moved from
`3.12` to `3.13`, `server/Dockerfile` from `python:3.12-slim` to
`python:3.13-slim`, `server/pyproject.toml` now declares
`requires-python = ">=3.13"`, and the `filterwarnings` entry suppressing
passlib's `crypt` deprecation was removed as no longer applicable.

**No API behaviour was changed** — no endpoint, request/response schema, token
lifetime, request budget, or authorization rule was touched. The server's own
test suite passes unmodified: **36 passed** on Python 3.13.5.

## Testing notes

To exercise the refresh path, start the server with a request budget of one, so
that every single authenticated request returns `401` and forces a refresh:

```bash
cd server
MAX_REQUESTS_PER_TOKEN=1 ACCESS_TOKEN_TTL_SECONDS=5 uv run uvicorn app.main:app --port 8000
```

Every command then still behaves normally, and `batch-update` completes with no
extra output. Verified against this configuration: 25 consecutive `products get`
invocations as separate processes, and a `batch-update` making 30+ authenticated
requests inside a single process — 38 `401`s, 38 transparent refreshes, nothing
on stdout but the expected JSON.
