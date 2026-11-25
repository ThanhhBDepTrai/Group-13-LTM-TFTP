# TFTP Pro UI refresh

This update refreshes the existing Tkinter interface and the browser console
without replacing the application framework or removing existing tabs and
commands.

## Tkinter GUI — visual changes
- Updated the main window size and minimum size for a more spacious desktop layout.
- Added a compact navy brand header to the main TFTP Pro workspace.
- Applied a consistent light enterprise palette and Segoe UI typography.
- Refreshed buttons, input fields, notebook tabs, Treeview tables, and progress bars.
- Improved spacing around the connection panel and main workspace.

## Tkinter GUI — preserved
- Existing Welcome / Create Workspace / Join Server / Login flows.
- Existing TFTP transfer, File Manager, Dashboard, Packet Analyzer, Users, and PXE tabs.
- Existing event handlers, API calls, transfer logic, and background queue behavior.

## Web console (`web/index.html`)
Single-file rewrite of the browser console served by `render_server.py`. No CDN,
external font, or npm dependency: it still works offline behind the proxy.

- Design: light-first Swiss enterprise layout with design tokens, navy top bar and
  teal accent; dark mode follows `prefers-color-scheme`.
- Icons: inline SVG sprite (29 outline symbols). Emoji icons and native
  `confirm()`/`prompt()` dialogs were removed.
- Layout: 248 px sidebar on desktop, icon rail between 701–1120 px, off-canvas
  drawer with hamburger and scrim on phones. Tables collapse into labelled cards
  at 640 px via `data-label` cells.
- States: skeleton rows while loading, empty states with a recovery action,
  inline busy spinners on buttons, determinate upload progress, and a stacked
  toast host for success/error/warning/info.
- Dialogs: accessible modal system (focus trap, Esc/backdrop close, focus
  restore) used for create-folder, role change, SHA-256 compare, delete and
  restore confirmations.
- API contract unchanged: same endpoints, same `Authorization: Bearer` header,
  same `localStorage` key `tftp_token`, same `api()` prefixing and optional
  direct-backend fallback.

### Bugs fixed while rewriting
- `toast()` inverted its colors, so success messages rendered red.
- `httpVerify` posted to `/api/api/files/verify` (double prefix).
- PXE payloads and dashboard/packet rows used field names the API does not
  return (`is_directory`, `bootfile`, `time`, `type`, `size`); rows now read
  `is_dir`, `bios_file`/`uefi_file`, `timestamp`, `direction`, `bytes_total`.
- `downloadBlob` ignored HTTP 401; it now clears the session like `api()` does.

### Validation
- JavaScript parsed with Node: no syntax errors; 114 DOM ids, no duplicates.
- HTML tag balance and SVG `<use>`/`<symbol>` references checked.
- Served through `render_server.py` and exercised with headless Chrome:
  login → dashboard → every tab, mobile drawer, modal, toast, logout.
- API smoke test: mkdir, upload, list, hash, verify (`VERIFIED`), PXE profile,
  dashboard and packet endpoints all returned the shapes the new UI reads.
- Checked at 1440 px, 960 px (rail), 620 px and 390 px viewports, plus dark mode,
  with no console errors and no horizontal overflow.

## Validation (project-wide)
- `python -m compileall -q .` passed.
- `python -m unittest discover -s tests -v` passed: 15 tests.
- GUI appearance should still be checked on Windows at the target display scaling; automated tests do not render Tkinter screens.
