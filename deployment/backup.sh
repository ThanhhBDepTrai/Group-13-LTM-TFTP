#!/usr/bin/env bash
set -euo pipefail
dest="backup/$(date +%Y%m%d-%H%M%S)"; mkdir -p "$dest"
sqlite3 database/tftp_admin.db ".backup '$dest/tftp_admin.db'"
tar -czf "$dest/company_files.tgz" storage/company_files
