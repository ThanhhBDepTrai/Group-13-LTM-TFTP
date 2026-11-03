#!/usr/bin/env bash
set -euo pipefail
python3 -m venv .venv && . .venv/bin/activate && pip install -e .
python -m enterprise.cli setup --name "${1:?tên công ty}" --host "${2:-127.0.0.1}"
