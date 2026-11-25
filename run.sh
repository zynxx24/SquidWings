#!/usr/bin/env bash
# Start SquidWings without activating the venv:
#   ./run.sh                 web UI (http://127.0.0.1:8080)
#   ./run.sh chat            terminal chat
#   ./run.sh addon list      any main.py command and its options
# The first run makes .venv and installs requirements (scripts/setup.py).
set -euo pipefail
cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

PY=.venv/bin/python
if [[ ! -x "$PY" ]]; then
    echo "Belum ada .venv, menyiapkan dulu (sekali saja)..."
    BASE=""
    for cand in python3.14 python3.13 python3.12 python3 python; do
        if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
            BASE="$cand"; break
        fi
    done
    if [[ -z "$BASE" ]]; then
        echo "Butuh Python 3.12 atau lebih baru. Pasang dulu, lalu jalankan ./run.sh lagi." >&2
        exit 1
    fi
    "$BASE" scripts/setup.py
fi

if [[ $# -eq 0 ]]; then
    set -- web
fi
exec "$PY" main.py "$@"
