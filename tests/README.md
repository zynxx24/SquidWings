# tests/

Test suite offline: tidak ada panggilan ke model atau internet (dijaga oleh `conftest.py`).

```bash
.venv/bin/python -m pytest -q          # semua
.venv/bin/python -m pytest -q tests/test_skill_adapters.py
bash scripts/check_all.sh              # ruff, mypy, compile, pytest, import
```

`conftest.py` menyalakan kedua add-on supaya test-nya ikut jalan, dan mengarahkan `data/`, riwayat gaya, dan statistik model ke folder sementara, jadi test tidak menyentuh data asli. `eval_data/` berisi kasus untuk evaluasi routing.
