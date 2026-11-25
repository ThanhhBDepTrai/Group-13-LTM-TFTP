# AGENTS.md — TFTP Pro

**What this repo is**: Python TFTP client/server (RFC 1350/2347/7440/7409), Admin API with token auth, SHA-256 integrity, Tkinter GUI + optional Web console. No third-party runtime deps; `pytest`/`ruff` are dev-only. Python 3.10+. Vietnamese user docs; English at `README.md`.

---

## Entrypoints (repo root)

- `tftp_pro.py` — Legacy: TFTP server + Admin API in one process. TFTP `0.0.0.0:6969`, API `127.0.0.1:6971`. **Requires `TFTP_ADMIN_PASSWORD` before running.**
- `tftp_gui.py` — Workspace Enterprise GUI (recommended flow). TFTP + Admin API bind `127.0.0.1` only; accounts/data under `%APPDATA%\TFTPPro\servers\<workspace_id>\efm.db`.
- `render_server.py` — Render deploy: launches `tftp_pro.py` backend + web proxy on `$PORT`. Auto-mounts Enterprise workspace at `web_workspace/efm.db` + `web_workspace/files/`. **Use this for the new web UI.**
- `admin_api.py` — Standalone Admin API.
- `tftp_server.py` / `tftp_client.py` — Standalone TFTP.
- `enterprise/cli.py` (`efm`) — Create server/workspace/super-admin.
- `scripts/set_admin_password.py` — Reset legacy admin password in existing `tftp_admin.db`.

**Packages**: `enterprise`, `database`, `client`; root py-modules in `pyproject.toml`. Shared defaults in `tftp_config.py`.

---

## Setup

- Python 3.10+, Tkinter (Windows: include Tcl/Tk component).
- No runtime pip deps: `python -m pip install -e ".[dev]"` installs only `pytest`/`ruff`.
- `.venv/` and `.env` are gitignored; create the venv before installing.
- **Enterprise workspace**: First start with `render_server.py` will create `web_workspace/efm.db`. Set `TFTP_SUPER_ADMIN_PASSWORD` (or `TFTP_ADMIN_PASSWORD`) to auto-create the first SUPER_ADMIN account.

---

## Developer commands (exact)

- **Lint**: `ruff check .` — config in `pyproject.toml`: `line-length = 100`, `target-version = "py310"`.
- **Test**: `python -m pytest -q` — `testpaths = ["testfiles"]`, files `test_*.py`.
- **Single file**: `python -m pytest tests/<file>.py -q`.
- **Legacy server (PowerShell)**: `$env:TFTP_ADMIN_PASSWORD="..."; python tftp_pro.py`.
- **Legacy GUI**: `python tftp_gui.py`.
- **Legacy client**: `python tftp_client.py --host 127.0.0.1 --port 6969 get README.md .\README.downloaded.md`.
- **Benchmark**: `python scripts/benchmark_tftp.py --size 2MiB --runs 3`.
- **Reset legacy admin password**: `python scripts/set_admin_password.py --db tftp_admin.db` (follow prompts) or `python scripts/set_admin_password.py --db tftp_admin.db --password "NewPass123!"`.
- **Enterprise workspace**: `set TFTP_ADMIN_PASSWORD="..." && python render_server.py` (first run creates SUPER_ADMIN from this env var).

---

## Ports & conflicts

- TFTP `6969`, Admin API `6971` (GUI uses the same on localhost).
- **Do not run `tftp_pro.py` and `tftp_gui.py` simultaneously on default ports** — one will fail to bind.
- **Render server**: Web UI on `$PORT` (default 10000), Admin API on `6971`, TFTP on `6969`.
- CI: ubuntu/latest + windows/latest × Python 3.10/3.11/3.12; steps `pip install -e ".[dev]"`, `ruff check .`, `pytest -q`; 10 min timeout.

---

## Tests

- All tests spin up local `127.0.0.1` servers on ephemeral ports (`port=0`); no external services, network, or secrets required. Safe to run offline.
- **Do not commit**: `.env`, `tftp_admin.db*`, `*.metrics.db*`, `pxe_profiles.json`, `server_storage/`, `*.downloaded.md` (see `.gitignore`). Runtime DBs are relative to the cwd of the command.
- **Legacy DB password gotcha**: `TFTP_ADMIN_PASSWORD` is only used when `tftp_admin.db` has **zero users**. If the DB already exists (from a prior run), the password env var is silently ignored. To reset: run `python scripts/set_admin_password.py --db tftp_admin.db`.

---

## Security / behavior gotchas

- TFTP has no auth/encryption; Admin API binds localhost by default. Do not expose either to the Internet.
- `tftp_gui.py` Workspace binds localhost only — a firewall cannot make it reachable from other machines.
- **Web UI login flow**: First tries Enterprise `/ent/login`; if server returns 404 (Enterprise not configured), automatically falls back to legacy `/auth/login` using `tftp_admin.db`. On wrong password, shows `"Sai tên đăng nhập hoặc mật khẩu"` (not generic "session expired").
- Web UI dark mode: `.btn-primary{color:#04201D}` and `.btn-danger{color:#250A0A}` for contrast on light accent.
- Icons: hidden inline SVG sprite (`<symbol>` + `<use>`), stroke 1.75, `currentColor`.
- Sidebar: ≥1121px = 248px; 701–1120px = 76px; ≤700px = off-canvas drawer + hamburger + scrim.
- Tables become cards ≤640px via `td::before{content:attr(data-label)}`.
- Dark-mode button contrast preserved.

---