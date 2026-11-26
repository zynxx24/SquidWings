# addons/ide/

IDE di dalam SquidWings untuk semua file dan proyek yang dibuat AI. Mati bawaan, seperti add-on lain.

```bash
python main.py addon install ide   # nyalakan (tidak ada paket atau program tambahan)
python main.py web                 # lalu buka menu IDE, atau tekan Alt+6
```

## Isinya

- **Proyek**: setiap chat punya folder sendiri (`workspace/sessions/[web] Judul chat`), ditambah folder di `workspace/projects`. Semuanya muncul di daftar proyek, terbaru di atas.
- **Editor**: CodeMirror 6 dengan warna sintaks untuk puluhan bahasa (Python, JS/TS, HTML, CSS, PHP, SQL, Go, Rust, Java, C/C++, YAML, Markdown, dan lainnya), cari dan ganti (Ctrl+F), lipat kode, kursor ganda, simpan dengan Ctrl+S. Warnanya mengikuti tema dan palet aplikasi.
- **Perubahan AI**: setiap giliran chat yang mengubah file tercatat (siapa pun alat yang menulisnya: tool file, perintah shell, build). Tiap file punya diff, dan satu file atau satu giliran bisa dikembalikan. Pengembalian juga tercatat.
- **Ikuti AI langsung**: file yang terbuka ikut diperbarui saat AI mengubahnya. Kalau kamu juga sedang mengeditnya, muncul pilihan ambil versi di disk atau simpan punyamu.
- **Pratinjau**: halaman `.html` (berjalan di sandbox tanpa akses ke aplikasi), Markdown, dan server pengembangan yang sedang jalan (port dari `npm run dev`, `php artisan serve`, dan sejenisnya).
- **Tanya AI** (Ctrl+I): pertanyaan tentang file atau bagian yang dipilih dikirim ke chat proyek itu. AI bekerja di folder yang sama, jadi hasilnya langsung muncul di IDE.
- Cari di semua file (Ctrl+Shift+F), buka file cepat (Ctrl+P), ganti proyek (Ctrl+O), unduh proyek sebagai `.zip`.
- File yang dihapus dari IDE masuk ke `.agent_memory/trash` di proyek itu, tidak langsung hilang.

## Cara kerjanya

| Bagian | File |
|---|---|
| Catatan perubahan per giliran (inti, aktif juga tanpa add-on) | `brain/utils/file_history.py` |
| API IDE (`/api/ide/...`) | `addons/ide/web/ide_api.py` |
| Halaman | `addons/ide/web/static/` (`index.html`, `ide.js`, `ide.css`) |
| Editor | `web/static/vendor/codemirror.js`, dibangun dari `build/` |

Riwayat perubahan disimpan di `<folder chat>/.agent_memory/history/`: `index.json` (keadaan terakhir), `turns.jsonl` (satu baris per giliran), dan `objects/` (isi file teks sampai 1 MB, satu salinan per isi). File biner dan yang lebih besar dicatat ukuran dan waktunya saja, jadi tidak bisa dikembalikan dari IDE.

## Membangun ulang editor

`vendor/codemirror.js` sudah ikut di repo. Untuk memperbarui atau menambah bahasa:

```bash
cd addons/ide/build
npm install
npm run build      # menulis ../web/static/vendor/codemirror.js
```
