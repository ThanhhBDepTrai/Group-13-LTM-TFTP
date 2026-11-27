#!/usr/bin/env bash
# Sao lưu database và thư mục file của TFTP Pro / Enterprise File Manager.
#
# Mặc định tự dò workspace Enterprise (web_workspace) trước, sau đó mới tới
# bản legacy. Có thể ghi đè bằng biến môi trường:
#
#   EFM_DB=web_workspace/efm.db EFM_FILES=web_workspace/files ./deployment/backup.sh
#
# Ví dụ chạy định kỳ bằng cron (2 giờ sáng mỗi ngày):
#   0 2 * * * cd /opt/efm && EFM_DB=web_workspace/efm.db EFM_FILES=web_workspace/files ./deployment/backup.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

pick_existing() {
    for candidate in "$@"; do
        if [ -e "$candidate" ]; then
            printf '%s' "$candidate"
            return 0
        fi
    done
    return 1
}

DB_PATH="${EFM_DB:-}"
if [ -z "$DB_PATH" ]; then
    DB_PATH="$(pick_existing \
        web_workspace/efm.db \
        database/tftp_admin.db \
        tftp_admin.db)" || {
        echo "Không tìm thấy database. Đặt EFM_DB=<đường dẫn efm.db> rồi chạy lại." >&2
        exit 1
    }
fi

FILES_DIR="${EFM_FILES:-}"
if [ -z "$FILES_DIR" ]; then
    FILES_DIR="$(pick_existing \
        web_workspace/files \
        storage/company_files \
        storage)" || {
        echo "Không tìm thấy thư mục file. Đặt EFM_FILES=<thư mục> rồi chạy lại." >&2
        exit 1
    }
fi

if [ ! -f "$DB_PATH" ]; then
    echo "Database không tồn tại: $DB_PATH" >&2
    exit 1
fi
if [ ! -d "$FILES_DIR" ]; then
    echo "Thư mục file không tồn tại: $FILES_DIR" >&2
    exit 1
fi

dest="backup/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$dest"

echo "Sao lưu database: $DB_PATH -> $dest/"
sqlite3 "$DB_PATH" ".backup '$dest/$(basename "$DB_PATH")'"

echo "Sao lưu file:     $FILES_DIR -> $dest/files.tgz"
tar -czf "$dest/files.tgz" -C "$(dirname "$FILES_DIR")" "$(basename "$FILES_DIR")"

echo "Hoàn tất: $dest"
