# scripts/

| Skrip | Untuk apa |
|---|---|
| `setup.py` | Setup satu perintah di Windows, macOS, Linux: `.venv` + requirements, opsional `--addon trading` |
| `check_all.sh` | Ruff, mypy, compile, pytest, dan import sekaligus |
| `tunnel.sh` | Tunnel Cloudflare per port: `scripts/tunnel.sh start` (chat) atau `scripts/tunnel.sh start 80` (trading); log di `data/logs/` |
| `upgrade_python.sh` | Pindah ke Python 3.14 lewat uv tanpa menyentuh Python sistem |
| `build_ui_palettes.py` | Menulis palet warna yang lolos WCAG ke `web/static/css/tokens.css` |
