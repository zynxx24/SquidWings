# web/

Server web (FastAPI) dan halaman chat. Halaman add-on (trading, Studio, IDE) ada di folder add-on masing-masing dan dipasang di `/static/<add-on>/` saat add-on aktif.

| File / folder | Isi |
|---|---|
| `server.py` | Server utama: chat, upload, terminal, preview, halaman, pemasangan API add-on, dan perubahan file per giliran (`/api/session/{id}/changes`) |
| `team_api.py` | API alur kerja (tim agent): simpan, aktif/nonaktif, jalankan, tes satu node, kredensial |
| `router_api.py` | API halaman Router (provider, model default, harga, token) |
| `data_api.py` | API Tabel data: sumber, tabel, baris, kolom, dan relasi antar tabel |
| `static/` | Halaman chat (`index.html`, `js/`, `css/`), halaman Router (`router/`), font (`vendor/`) |
| `tailwind/` | Konfigurasi Tailwind (prefix `tw-`) |
| `data/` | Kredensial dari web, provider router, run tim (tidak ikut Git) |

Selector model punya dua mode: **Auto (hemat)** atau satu model yang dipilih (dikunci; kalau error, alasannya tampil di chat). Pada Auto, alasan pilihan autopilot tampil di langkah-langkah jawaban. Statistik cache: `GET /api/cache`, kosongkan: `POST /api/cache/clear`.

Jalankan dengan `python main.py web`, lalu buka http://127.0.0.1:8080. Warna tema dibuat oleh `scripts/build_ui_palettes.py` ke `static/css/tokens.css`.
