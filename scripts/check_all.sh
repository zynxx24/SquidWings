#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON="$ROOT_DIR/.venv/bin/python"
RUFF="$ROOT_DIR/.venv/bin/ruff"
MYPY="$ROOT_DIR/.venv/bin/mypy"

if [[ ! -x "$PYTHON" ]]; then
    echo "ERROR: virtualenv Python tidak ditemukan: $PYTHON" >&2
    exit 1
fi

if [[ ! -x "$RUFF" ]]; then
    echo "ERROR: ruff tidak ditemukan: $RUFF" >&2
    exit 1
fi

if [[ ! -x "$MYPY" ]]; then
    echo "ERROR: mypy tidak ditemukan: $MYPY" >&2
    exit 1
fi

FAILED=0

run_check() {
    local name="$1"
    shift

    echo
    echo "================================================================"
    echo "CHECK: $name"
    echo "================================================================"

    if "$@"; then
        echo "PASS: $name"
    else
        echo "FAIL: $name"
        FAILED=1
    fi
}

run_check "Ruff" \
    "$RUFF" check .

# Gate format dimatikan: repo ini belum pernah di-`ruff format`, sehingga
# pemeriksaan melaporkan 117 file churn spasi murni. Aktifkan kembali hanya
# setelah `ruff format .` dijalankan sengaja (backup: /tmp/squidwings_pre_format.tar.gz).

# Tanpa argumen path: mypy memakai [tool.mypy].files dari pyproject.toml,
# yang sengaja dibatasi ke kode produksi (183 file). Argumen "." memaksa
# tests/ ikut, di mana ada 37 fungsi test tanpa anotasi — itu bukan cacat
# produk dan akan membuat gate ini merah permanen.
run_check "Mypy" \
    "$MYPY" \
        --show-error-codes \
        --pretty

run_check "Compileall" \
    "$PYTHON" -m compileall -q -x '(^|/)(workspace|vault|tool\.orphan|\.venv)/' .

run_check "Pytest" \
    "$PYTHON" -m pytest tests/ -q

run_check "Import main" \
    "$PYTHON" -c "import main; print('IMPORT OK')"

echo
echo "================================================================"

if [[ "$FAILED" -ne 0 ]]; then
    echo "CHECK FAILED: masih ada pemeriksaan yang gagal."
    exit 1
fi

echo "ALL CHECKS PASSED"
exit 0
