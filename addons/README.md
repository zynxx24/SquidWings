# addons/

Fitur tambahan yang **mati bawaan** dan butuh setup manual. Bagian lain harness tetap lengkap tanpa add-on.

| Add-on | Isi | Panduan |
|---|---|---|
| `trading/` | Model kuant, EA MT4, MT5, panel trading, tool `trading_snapshot` | [trading/README.md](trading/README.md) |
| `studio/` | Editor video motion dengan chat revisi dan render MP4 | [studio/README.md](studio/README.md) |

```bash
python main.py addon list              # status semua add-on
python main.py addon install trading   # pasang paket, nyalakan, tampilkan langkah manual OS ini
python main.py addon enable studio     # nyalakan saja (paketnya sudah ada)
python main.py addon remove trading    # matikan; file dan data tetap
```

## Isi satu add-on

```text
addons/<nama>/
├── addon.json         file wajib, requirements, program, langkah manual per OS (windows, linux, macos)
├── requirements.txt   paket Python tambahan
├── README.md
├── web/               API dan halaman (dipasang di /static/<nama>/)
├── tools/             tool agent, didaftarkan hanya saat aktif
└── skills/            skill, dimuat hanya saat aktif
```

Saklarnya tersimpan di `addons/enabled.json` (tidak ikut Git). `ADDON_<NAMA>=1` atau `=0` di `.env` mengalahkan saklar itu. `manager.py` berisi logika install dan saklar.
