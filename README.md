<div align="center">

<img src="app/assets/fire2api-logo.svg" alt="Logo Fire2API" width="72">
<h1>Fire2API</h1>

<p><strong>Turn parameterized Firebird commands into authenticated HTTP APIs, without building a custom backend.</strong></p>

<img src="docs/assets/fire2api-security.png" alt="Fire2API secure API gateway" width="100%">

<p>
  <a href="https://fire2api.com/">Website</a>
  &nbsp;•&nbsp;
  <a href="https://fire2api.com/docs">Documentation</a>
</p>

</div>

Fire2API is an open-source FastAPI and NiceGUI application for exposing approved Firebird 3, 4, or 5 operations as secure HTTP endpoints. One instance connects to one external Firebird database, while a local SQLite metastore holds routes, parameter definitions, Access Keys, execution history, idempotency state, and administrative audit records.

## Key Capabilities

- Publish dynamic `GET`, `POST`, `PUT`, `PATCH`, and `DELETE` endpoints without maintaining a separate application for each integration.
- Configure routes from a responsive NiceGUI admin panel with a CodeMirror SQL editor and automatic parameter detection.
- Accept typed `path`, `query`, and flat JSON `body` parameters while binding values through the Firebird driver.
- Enforce method-aware SQL rules that reject DDL, multiple statements, transaction control, and `EXECUTE BLOCK`.
- Separate administrative authentication from consumer Access Keys; only SHA-256 hashes and key prefixes are stored.
- Execute reads and writes in explicit transactions, with rollback for administrative tests, errors, and cancellation.
- Support optional 24-hour write idempotency, execution history, cancellation, rate limiting, and content-safe auditing.
- Generate OpenAPI documentation for public system endpoints and active dynamic routes while keeping the Admin API hidden.

## Quick Start

Fire2API expects an existing Firebird server that is reachable from the host or container. Use a dedicated Firebird account with only the permissions required by the routes you plan to expose.

### Docker Compose (recommended)

Requirements:

- Docker Engine or Docker Desktop with Docker Compose v2.
- Network access to a Firebird 3, 4, or 5 server.

#### 1. Create the environment file

On Windows PowerShell:

```powershell
Copy-Item .env.example .env

$bytes = New-Object byte[] 48
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes)
[Convert]::ToBase64String($bytes)
$rng.Dispose()
```

On Linux:

```bash
cp .env.example .env
openssl rand -base64 48
```

Copy the generated value into `ADMIN_API_KEY` in `.env`, then configure:

| Variable | Purpose |
|---|---|
| `ADMIN_API_KEY` | Administrative secret with at least 32 characters. The public placeholder is rejected at startup. |
| `FIREBIRD_HOST` / `FIREBIRD_PORT` | Address of the Firebird server. |
| `FIREBIRD_DB_PATH` | Server-side database path or alias. |
| `FIREBIRD_USER` / `FIREBIRD_PASSWORD` | Least-privilege Firebird account. |
| `FIREBIRD_CHARSET` | Connection character set; defaults to `UTF8`. |
| `CORS_ALLOWED_ORIGINS` | Comma-separated browser origin allowlist; empty disables cross-origin access. |

The complete configuration inventory and defaults are documented in [`.env.example`](.env.example).

#### 2. Build and start Fire2API

From the project root:

```bash
docker compose up -d
docker compose ps
docker compose logs -f fire2api
```

Press `Ctrl+C` to leave the log stream; the container continues running. The Compose service builds `fire2api:local` from the checked-out source and stores the SQLite metastore in the `fire2api-data` named volume.

#### 3. Verify the service

- Admin panel: `http://localhost:8000/`
- Process health: `http://localhost:8000/health`
- Dependency readiness: `http://localhost:8000/ready`
- Interactive API documentation: `http://localhost:8000/docs`

`/health` confirms that the process is running. `/ready` returns HTTP 200 only when the metastore migration is current and the configured Firebird database is reachable.

### Native Python

Native installations require Python 3.11, 3.12, or 3.13 and the Firebird client library (`fbclient`) available to the process.

#### Windows PowerShell

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-runtime.txt
Copy-Item .env.example .env
python main.py
```

Generate an administrative key with the PowerShell command shown in the Docker section, place it in `.env`, and configure the Firebird connection before starting the application.

#### Linux

Install the Firebird client package for your distribution first. On Debian and Ubuntu, the package is commonly available as `libfbclient2`.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-runtime.txt
cp .env.example .env
python main.py
```

Generate an administrative key with `openssl rand -base64 48`, place it in `.env`, and configure the Firebird connection before starting the application.

For development and repository checks, install `requirements.txt` instead. It includes the runtime packages plus the test, type-checking, linting, lock-maintenance, and audit tools.

### Versioning releases

Run the standalone versioning helper from the repository root with a completely clean Git working tree:

```bash
python version.py --major
python version.py --minor
python version.py --patch
python version.py 1.2.3
```

The options are mutually exclusive. The helper updates `[project].version` in `pyproject.toml`, creates a commit named `vX.Y.Z`, and adds the local tag `vX.Y.Z`. It does not push the commit or tag. The application reads this same value at startup for `/health`, OpenAPI, and the admin panel, including when running from the Docker image.

## Updating

The SQLite metastore contains route definitions, Access Keys, execution state, and audit records. Back it up before every update using a SQLite-consistent backup procedure, or stop the service before backing up the named volume.

For a Git checkout:

```bash
git pull --ff-only
docker compose up -d
docker compose ps
docker compose logs --tail=100 fire2api
```

The same `docker compose up -d` command rebuilds the local image, recreates the service when required, and preserves the named volume. Fire2API runs `alembic upgrade head` before loading services, routes, and the admin panel.

After the update, verify `http://localhost:8000/health` and `http://localhost:8000/ready`.

> Do not run `docker compose down -v` during a normal update. The `-v` option deletes the named volume and therefore removes the metastore.

If the source was obtained as an archive, replace the application source while preserving `.env`, then run the same Compose commands from the updated project directory.

## Using the Admin Panel

1. Open `/` and sign in with `ADMIN_API_KEY`. `/admin` remains available as a compatibility redirect to `/`.
2. Create an Access Key for API consumers. The complete key is displayed once; store it securely before closing the dialog.
3. Create a route by choosing its path and HTTP method, then enter the Firebird SQL in the CodeMirror editor.
4. Review detected `{PATH}` placeholders and `:BINDS`. Names are stored and documented in uppercase but matched case-insensitively.
5. Configure every parameter as `string`, `integer`, `float`, `boolean`, `date`, or `datetime`, with a `path`, `query`, or `body` source.
6. Test the route with typed fields. Administrative tests always roll back and clearly display that behavior.
7. Save the route. Route configuration and the complete parameter snapshot are validated and persisted atomically before dynamic routes reload.

Parameter synchronization is additive: it creates missing drafts, forces path placeholders to be required path parameters, preserves existing configuration, and never silently deletes a parameter. Parameters removed manually in the editor are deleted only when the route is saved successfully.

## API and Authentication

Authenticated requests use the standard Bearer scheme:

```http
Authorization: Bearer <token>
```

- `ADMIN_API_KEY` protects `/api/base/admin/*`. Administrative endpoints are excluded from OpenAPI.
- Access Keys created in the metastore protect `/api/<route_path>`.
- Access Key values are stored only as SHA-256 hashes with a non-secret prefix; the complete value is shown only at creation.
- Manual Access Keys must contain at least 32 characters.

### System endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health` | Confirms that the application process is running. |
| `GET /ready` | Checks the metastore, Alembic revision, and Firebird connection. |
| `GET /docs` | Swagger UI for public and dynamic endpoints. |
| `GET /openapi.json` | OpenAPI schema for public and dynamic endpoints. |

### Response contracts

Responses use a consistent `success`, `message`, `data`, and `meta` envelope.

- Reads return `data` as a list and include `meta.count` and `meta.execution_id`.
- Writes return `data.rows`, `data.affected_rows`, and `meta.execution_id`.
- Unknown parameters and extra body properties are rejected. Request bodies must be flat JSON objects.
- GET routes may accept `LIMIT`, `OFFSET`, and `ORDER_BY`; each value is validated with Firebird-aware rules before a worker is created.

### SQL policy

| Method | Accepted operations |
|---|---|
| `GET` | `SELECT`, or a CTE ending in `SELECT` |
| `POST` | `INSERT` or `EXECUTE PROCEDURE` |
| `PUT` / `PATCH` | `UPDATE`, `MERGE`, `UPDATE OR INSERT`, or a procedure |
| `DELETE` | `DELETE` or a procedure |

DDL, multiple statements, transaction control, and `EXECUTE BLOCK` are rejected. SQL policy is an additional safeguard and does not replace least-privilege permissions on the Firebird account. Free-form input is never interpolated into SQL values.

### Write idempotency

Write routes may receive an optional `Idempotency-Key` header. Keys remain valid for 24 hours by default. Fire2API stores only SHA-256 hashes of the key, normalized payload, and response, together with state and execution ID-never their contents.

A processed key, a different payload for the same key, a concurrent request, or an unavailable stored result returns HTTP 409 and never authorizes automatic replay.

## Production and Internet Warning

> **Production and Internet:** The Compose configuration publishes HTTP on port 8000 for local use or a trusted internal network. Never expose this port directly to the Internet. When external access is required, keep Fire2API on a private network and place it behind a reverse proxy with mandatory HTTPS, a valid certificate, HTTP-to-HTTPS redirection, and HSTS. Bearer tokens must never travel over plaintext connections.

Also ensure that the Firebird server is not publicly reachable, restrict network access between Fire2API and Firebird, rotate credentials deliberately, and back up the metastore with a SQLite-consistent procedure.

## Troubleshooting

| Symptom | What to check |
|---|---|
| Container exits during startup | Run `docker compose logs --tail=200 fire2api`; confirm that `.env` exists and `ADMIN_API_KEY` is not the public placeholder. |
| `/health` is unavailable | Check `docker compose ps`, the port mapping, host firewall, and application logs. |
| `/ready` returns HTTP 503 | Verify Firebird host, port, database path or alias, credentials, network routing, and that `fbclient` is present for native installations. |
| Admin panel returns to login | Verify `ADMIN_API_KEY` and `ADMIN_SESSION_MINUTES`; expired sessions are revalidated before every administrative action. |
| Access Key is rejected | Confirm that the key is active and that the complete one-time value was copied at creation. |
| Dynamic route is missing | Save and activate the route, define every detected placeholder and bind, then trigger the administrative refresh if needed. |
| Changes are not visible after an update | Run `docker compose up -d`, then inspect `docker compose ps` and the latest service logs. |
| Metastore appears empty | Confirm that the `fire2api-data` volume is mounted and that the stack was not removed with `docker compose down -v`. |

For vulnerability reports, follow [SECURITY.md](SECURITY.md). Contribution guidance is available in [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Fire2API is licensed under the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for attribution details. The vendored Inter font is distributed under the SIL Open Font License 1.1; its license is included at [`app/assets/fonts/OFL.txt`](app/assets/fonts/OFL.txt).
