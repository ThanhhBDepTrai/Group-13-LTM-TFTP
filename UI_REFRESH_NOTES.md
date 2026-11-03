# TFTP Pro UI refresh

This update refreshes the existing Tkinter interface without replacing the application framework or removing existing tabs and commands.

## Visual changes
- Updated the main window size and minimum size for a more spacious desktop layout.
- Added a compact navy brand header to the main TFTP Pro workspace.
- Applied a consistent light enterprise palette and Segoe UI typography.
- Refreshed buttons, input fields, notebook tabs, Treeview tables, and progress bars.
- Improved spacing around the connection panel and main workspace.

## Preserved
- Existing Welcome / Create Workspace / Join Server / Login flows.
- Existing TFTP transfer, File Manager, Dashboard, Packet Analyzer, Users, and PXE tabs.
- Existing event handlers, API calls, transfer logic, and background queue behavior.

## Validation
- `python -m compileall -q .` passed.
- `python -m unittest discover -s tests -v` passed: 15 tests.
- GUI appearance should still be checked on Windows at the target display scaling; automated tests do not render Tkinter screens.
