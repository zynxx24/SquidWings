#!/usr/bin/env bash
# Move SquidWings to a newer Python (default 3.14) without touching the system Python.
#
#   bash scripts/upgrade_python.sh            # Python 3.14
#   bash scripts/upgrade_python.sh 3.14 --test   # and run the test suite on it
#
# The system's python3 (used by the OS) is left alone: uv puts the new Python in ~/.local/share/uv.
# The old .venv is kept as .venv-py<version> until you delete it. If anything fails, it is put back.
# Undo by hand:  rm -rf .venv && mv .venv-py3.12 .venv
set -euo pipefail

VERSION="${1:-3.14}"
RUN_TESTS="${2:-}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m✘ %s\033[0m\n' "$*" >&2; exit 1; }

# 1. Nothing may run from the environment that is about to move.
running=""
for pid in $(pgrep -f "python.*main\.py" || true); do
  if [ "$(readlink -f "/proc/$pid/cwd" 2>/dev/null)" = "$ROOT" ]; then
    running+="  $pid: $(tr '\0' ' ' < "/proc/$pid/cmdline")"$'\n'
  fi
done
if [ -n "$running" ]; then
  printf 'SquidWings masih berjalan dari folder ini:\n%sHentikan dulu (Ctrl+C di terminalnya, atau: kill <pid>), lalu jalankan lagi.\n' "$running" >&2
  exit 1
fi

# 2. uv installs Python itself, without sudo.
if ! command -v uv >/dev/null 2>&1; then
  fail "uv belum terpasang. Pasang dulu:  curl -LsSf https://astral.sh/uv/install.sh | sh   lalu buka terminal baru."
fi
say "Memasang Python $VERSION lewat uv (tidak mengubah python3 sistem)"
uv python install "$VERSION"

OLD=""
if [ -x .venv/bin/python ]; then
  OLD_VERSION="$(.venv/bin/python -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
  if [ "$OLD_VERSION" = "$VERSION" ]; then
    say ".venv sudah memakai Python $VERSION; hanya memperbarui paketnya"
  else
    OLD=".venv-py$OLD_VERSION"
    [ -e "$OLD" ] && fail "$OLD sudah ada. Hapus atau pindahkan dulu, lalu jalankan lagi."
    say "Menyimpan venv lama sebagai $OLD"
    mv .venv "$OLD"
  fi
fi

restore() {
  if [ -n "$OLD" ] && [ -d "$OLD" ]; then
    printf '\033[1;31m✘ Gagal. Venv lama dikembalikan.\033[0m\n' >&2
    rm -rf .venv
    mv "$OLD" .venv
  fi
}
trap restore ERR

if [ ! -x .venv/bin/python ]; then
  say "Membuat .venv baru dengan Python $VERSION"
  uv venv --seed --python "$VERSION" .venv      # --seed: pip ada di dalamnya, dipakai auto-update
fi
say "Memasang requirements.txt"
uv pip install --python .venv/bin/python -r requirements.txt

say "Memeriksa"
.venv/bin/python - <<'PY'
import sys
import faster_whisper, fastapi, markitdown, rapidocr, yt_dlp  # noqa: F401
import web.server  # noqa: F401  (the whole app imports)
print(f"Python {sys.version.split()[0]}: semua modul terbaca")
PY
if [ "$RUN_TESTS" = "--test" ]; then
  .venv/bin/python -m pytest -q -m "not live and not slow"
fi
trap - ERR

echo "$VERSION" > .python-version
.venv/bin/python main.py --no-update tools || true
say "Selesai: SquidWings sekarang memakai $(.venv/bin/python --version)."
if [ -n "$OLD" ]; then
  echo "    Venv lama ada di $OLD. Bila semuanya lancar beberapa hari, hapus dengan: rm -rf $OLD"
  echo "    Kembali ke yang lama: rm -rf .venv && mv $OLD .venv"
fi
