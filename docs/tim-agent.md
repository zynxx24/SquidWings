# Tim agent

Menu ini aktif dengan `WEB_TEAMS_ENABLED=true`. Satu permintaan tim dibatasi 10 menit, dan tiap agent 4 menit serta 120 ribu token. Ubah lewat `TEAM_RUN_SECONDS`, `TEAM_MEMBER_SECONDS`, `TEAM_MEMBER_ROUNDS`, dan `TEAM_MEMBER_TOKENS`.

Centang **Mode video** pada tim yang membangun dan merender video. Tim itu dapat 60 menit per permintaan, dan tiap agent 30 menit, 40 putaran, serta 400 ribu token. Skill `animate` juga langsung aktif di sesinya. Ubah lewat `TEAM_VIDEO_RUN_SECONDS`, `TEAM_VIDEO_MEMBER_SECONDS`, `TEAM_VIDEO_MEMBER_ROUNDS`, dan `TEAM_VIDEO_MEMBER_TOKENS`. Template **Studio video animasi** sudah memakai mode ini: penulis naskah, animator, lalu editor yang meninjau dan mengirim videonya ke hasil tim.

Agar model murah juga sampai ke video:

- **Batas yang cukup.** Agent mode video tidak dipotong batas tier murah: 40 putaran, panduan skill sampai 7.000 karakter, dan file dibaca per 16.000 karakter. Sebelumnya model flash hanya dapat 20 putaran, panduan 1.500 karakter, dan bacaan 6.000 karakter, sehingga habis waktu membaca ulang kit.
- **Peringatan lebih awal.** Saat 70% putaran, token, atau waktu terpakai, setiap agent tim diminta berhenti membaca dan menulis hasilnya ke file, selagi tool masih tersedia.
- **Jalur pendek.** Penulis naskah menulis `brief.md` di tiga langkah pertama, dengan durasi 15-30 detik. Animator mulai dari demo gaya yang sudah jalan (`styles/<gaya>/demo/`), langsung build, lalu render. Editor merender sendiri bila animator belum sempat. Tim lama yang masih memakai instruksi template lama diperbarui otomatis; instruksi yang sudah kamu ubah tidak disentuh.
- **Laporan sebagai file.** Laporan tiap agent juga disimpan di `team/<id>.md` di folder run, jadi agent berikutnya bisa membuka versi lengkapnya.
- **Render pengaman.** Bila semua agent selesai tanpa video tapi piece (`piece.json` dan `src/`) sudah ditulis, server menjalankan `build.mjs` dan `export.mjs --share` sendiri di sandbox yang sama, lalu membagikan hasilnya.
- **Alasan yang jelas.** Bila tetap tidak ada video, hasil tim menyebut per agent apa yang terjadi, plus hasil render pengamannya, dan laporan lead tetap tampil di bawahnya.

## Alur

Kanvas tim bekerja seperti alur di n8n. Urutannya ditandai 1, 2, 3 mengikuti sambungan antar-node, dan node pertama diberi tanda **Mulai**. Jalur keluar dari gate dan switch diberi huruf, misalnya 5A dan 5B, dan hanya jalur yang terpilih yang berjalan.

| Node | Fungsi |
|---|---|
| Agent | Mengerjakan tugas dengan model dan instruksinya sendiri |
| Gate IF | Sampai 5 syarat digabung dengan AND atau OR. Terpenuhi ke jalur A, tidak ke jalur B |
| Switch | Sampai 3 aturan. Aturan pertama yang cocok memilih jalur A, B, atau C, sisanya ke D |
| Merge | Menggabungkan beberapa jalur, lanjut saat salah satu tiba atau menunggu semuanya |
| Delay | Menunggu 1 sampai 3600 detik, tanpa memakan batas waktu tim |
| Teks | Menyusun teks dengan `{{input}}`, `{{task}}`, `{{date}}`, `{{time}}`, `{{day}}`, `{{team}}`, dan `{{node.ID}}` |
| HTTP | GET atau POST ke alamat publik, balasannya diteruskan ke node berikutnya |
| Telegram | Mengirim hasil ke chat Telegram |
| Database | Menyimpan hasil ke SQLite, PostgreSQL, MySQL, atau MongoDB |

Syarat di gate dan switch bisa berupa: berisi atau tidak berisi kata kunci, sama persis, regex, kosong, rentang jam dan hari, atau pertanyaan ya/tidak ke AI. Syarat tanpa AI dicek lebih dulu, jadi AND yang sudah gagal atau OR yang sudah terpenuhi tidak memanggil model.

Tombol **Jadwal** mengatur pemicunya:

| Pemicu | Contoh |
|---|---|
| Manual | Tombol Jalankan |
| Interval | Tiap 30 menit (minimal 5) |
| Jam tertentu | `05:00, 12:00, 17:00` pada hari yang dipilih |
| Cron | `0 7 * * 1-5` untuk 07:00 Senin sampai Jumat |
| Sekali | 2026-11-02 09:00 |
| Pesan Telegram | Semua pesan, atau hanya yang diawali kata seperti `/jadwal` |

Pemicu berjalan selama server menyala. Jadwal yang terlewat saat server mati tidak dijalankan belakangan. Pada pemicu Telegram, isi pesan menjadi tugasnya, dan node Telegram tanpa Chat ID membalas ke chat yang mengirim. Hanya pesan dari `TELEGRAM_CHAT_ID` yang diterima, karena siapa pun bisa mengirim pesan ke bot.

Template yang tersedia:

- **Alarm bangun dan pengingat jadwal**: berjalan jam 05:00, 12:00, 17:00, dan 21:00. Pagi hari, gate AND (jam 04:00 sampai 10:00 dan catatan tidak berisi "libur") membangunkan lewat Telegram, lalu alarm kedua 10 menit kemudian. Di jam lain, agent mengingatkan kegiatan 4 jam ke depan. Ubah daftar kegiatan di node **Agenda**.
- **Bot Telegram serba bisa**: berjalan saat kamu mengirim pesan ke bot. Switch memilah `/jadwal`, `/cuaca`, dan `/catat`, pesan lain dijawab agent umum, lalu balasan dikirim ke chat yang sama.
- **Studio video animasi**: mode video, dari naskah sampai video yang bisa diputar di hasil tim.
- **Asisten sehari-hari**: mencari fakta nyata, jeda, gate, menulis jawaban, lalu mengirim ke Telegram dan menyimpan ke database.

Telegram butuh `TELEGRAM_BOT_TOKEN` dan `TELEGRAM_CHAT_ID` di `.env`. Untuk tahu chat ID-mu, kirim pesan ke bot lalu buka `https://api.telegram.org/bot<TOKEN>/getUpdates`. Database selain SQLite butuh alamatnya di `.env` dan drivernya:

```bash
pip install "psycopg[binary]"   # TEAM_DB_POSTGRES_URL
pip install pymysql             # TEAM_DB_MYSQL_URL
pip install pymongo             # TEAM_DB_MONGODB_URL
```

Token dan alamat database hanya disimpan di `.env`, tidak di pengaturan tim.
