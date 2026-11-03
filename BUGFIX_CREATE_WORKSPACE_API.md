# Bugfix: Create Workspace and Enterprise API

## What changed
- Creating a Workspace now creates the Enterprise SQLite database and Super Admin account.
- The app starts the TFTP UDP server and Admin API automatically after successful setup.
- The GUI authenticates against `POST /api/ent/login` rather than the legacy `/api/auth/login` endpoint.
- Existing TFTP Pro API routes accept Enterprise bearer tokens, allowing the legacy tabs to reuse the Enterprise account session.
- The Admin Setup screen is destroyed before the main TFTP Pro UI is rendered.
- Closing the GUI stops the embedded TFTP and Admin API servers.

## Run
```powershell
python -m venv .venv
.venv\Scripts\activate
python tftp_gui.py
```
Create Server from the welcome screen. The app starts both services itself; do not start `tftp_pro.py` separately for this embedded-server flow.

Default local ports: Admin API TCP 6971, TFTP UDP 6969.
