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

## Editor alur

Tim disusun di kanvas yang dibaca dari kiri ke kanan, seperti editor workflow n8n. Bagian atas punya tiga tab: **Editor**, **Eksekusi**, dan **Kredensial**.

**Di kanvas**

- Node pertama selalu **Pemicu**. Klik dua kali untuk memilih kapan alur berjalan.
- **Menyambung:** seret titik di sisi kanan node ke node lain. Lepas di tempat kosong untuk langsung menambah node di situ.
- **Memutus:** klik garisnya, lalu tekan Delete atau tombol ×.
- Garis putus-putus dibuat otomatis: agent tanpa sambungan keluar mengirim hasilnya ke Lead, Lead ke Reviewer, dan jawaban akhir ke node Telegram atau Database yang belum disambung.
- **Menambah node:** tombol **+** di kanan atas atau tombol Tab membuka daftar node, lengkap dengan kotak pencarian. Tombol + kecil di ujung keluaran node menambah node yang langsung tersambung.
- **Menggeser kanvas:** seret latarnya, atau scroll. Ctrl+scroll untuk zoom.
- **Merapikan:** tombol di pojok kiri bawah memuat semua node ke layar (tombol 1), mengatur zoom, dan merapikan tata letak.
- Ctrl+S menyimpan. Selama ada perubahan yang belum disimpan, label **Belum disimpan** muncul di samping nama alur.

**Detail node.** Klik dua kali sebuah node (atau tekan Enter) untuk membuka tiga kolom:

| Kolom | Isi |
|---|---|
| Input | Isi yang masuk dari node sebelumnya pada eksekusi terakhir |
| Parameter | Pengaturan node. Agent: peran, model, instruksi, dan posisi Agent, Lead, atau Reviewer |
| Output | Isi yang dikirim node, status, waktu, dan token |

Di layar kecil, ketiga kolom itu menjadi tab.

**Menjalankan.** Isi tugas di bar bawah kanvas, lalu tekan **Jalankan alur**. Setiap node menampilkan statusnya sambil berjalan, dan hasil akhirnya muncul di atas bar.

**Eksekusi.** Tab ini berisi riwayat run: status, waktu mulai, durasi, token, dan sumber pemicunya. Pilih satu run untuk melihat tugas, tabel per node, dan hasilnya. Tombol **Lihat di kanvas** memuat status run itu ke editor.

**Menu ⋯** berisi Alur baru, Mulai dari template, Rapikan tata letak, Mode video, dan Hapus alur.

| Node | Fungsi |
|---|---|
| AI Agent | Mengerjakan tugas dengan model dan instruksinya sendiri |
| IF | Sampai 5 syarat digabung dengan AND atau OR. Terpenuhi lewat jalur **benar**, tidak terpenuhi lewat jalur **salah** |
| Switch | Sampai 3 aturan. Aturan pertama yang cocok memilih jalur A, B, atau C, sisanya lewat jalur **lainnya** |
| Merge | Menggabungkan beberapa jalur, lanjut saat salah satu tiba atau menunggu semuanya |
| Wait | Menunggu 1 sampai 3600 detik, tanpa memakan batas waktu tim |
| Teks / template | Menyusun teks dengan `{{input}}`, `{{task}}`, `{{date}}`, `{{time}}`, `{{day}}`, `{{team}}`, dan `{{node.ID}}` (klik chip untuk menyisipkan) |
| HTTP Request | GET atau POST ke alamat publik, balasannya diteruskan ke node berikutnya |
| Telegram | Mengirim hasil ke chat Telegram |
| Database | Menyimpan hasil ke SQLite, PostgreSQL, MySQL, atau MongoDB |

Syarat di gate dan switch bisa berupa: berisi atau tidak berisi kata kunci, sama persis, regex, kosong, rentang jam dan hari, atau pertanyaan ya/tidak ke AI. Syarat tanpa AI dicek lebih dulu, jadi AND yang sudah gagal atau OR yang sudah terpenuhi tidak memanggil model.

Node **Pemicu** mengatur kapan alur berjalan:

| Pemicu | Contoh |
|---|---|
| Manual | Tombol Jalankan |
| Interval | Tiap 30 menit (minimal 5) |
| Jam tertentu | `05:00, 12:00, 17:00` pada hari yang dipilih |
| Cron | `0 7 * * 1-5` untuk 07:00 Senin sampai Jumat |
| Sekali | 2026-11-02 09:00 |
| Pesan Telegram | Semua pesan, atau hanya yang diawali kata seperti `/jadwal` |

Pemicu berjalan selama server menyala. Jadwal yang terlewat saat server mati tidak dijalankan belakangan. Pada pemicu Telegram, isi pesan menjadi tugasnya, dan node Telegram tanpa Chat ID membalas ke chat yang mengirim. Hanya pesan dari Chat ID di Kredensial yang diterima, karena siapa pun bisa mengirim pesan ke bot.

Template yang tersedia:

- **Alarm bangun dan pengingat jadwal**: berjalan jam 05:00, 12:00, 17:00, dan 21:00. Pagi hari, gate AND (jam 04:00 sampai 10:00 dan catatan tidak berisi "libur") membangunkan lewat Telegram, lalu alarm kedua 10 menit kemudian. Di jam lain, agent mengingatkan kegiatan 4 jam ke depan. Ubah daftar kegiatan di node **Agenda**.
- **Bot Telegram serba bisa**: berjalan saat kamu mengirim pesan ke bot. Switch memilah `/jadwal`, `/cuaca`, dan `/catat`, pesan lain dijawab agent umum, lalu balasan dikirim ke chat yang sama.
- **Studio video animasi**: mode video, dari naskah sampai video yang bisa diputar di hasil tim.
- **Asisten sehari-hari**: mencari fakta nyata, jeda, gate, menulis jawaban, lalu mengirim ke Telegram dan menyimpan ke database.

## Kredensial

Tab **Kredensial** mengatur key dan ID tanpa membuka `.env` di server:

| Bagian | Isi | Tombol Tes |
|---|---|---|
| Telegram | Bot token (dari @BotFather) dan Chat ID | Mengirim pesan tes ke chat itu |
| Web search | Brave Search API key dan URL SearXNG, dipakai tool web search agent | Satu pencarian berisi satu hasil |
| Database | URL PostgreSQL, MySQL, dan MongoDB | Membuka koneksi lalu menutupnya, tanpa menulis apa pun |

Untuk tahu Chat ID-mu, kirim pesan ke bot lalu buka `https://api.telegram.org/bot<TOKEN>/getUpdates`.

Nilai yang diisi di sini menang atas `.env` dan langsung berlaku, tanpa restart. Itu termasuk polling Telegram dan tool web search. Label di tiap isian menunjukkan sumber nilainya: **Dari web**, **Dari .env**, atau **Belum diisi**. **Hapus** membuang nilai dari web, sehingga nilai dari `.env` dipakai lagi. Halaman hanya menerima versi tersamar dari setiap secret, misalnya `…mmnn`. Semuanya disimpan di `web/data/credentials.json` dengan izin 0600, dan tidak ikut git. Pindahkan dengan `CREDENTIALS_FILE`.

Database selain SQLite butuh drivernya:

```bash
pip install "psycopg[binary]"   # PostgreSQL
pip install pymysql             # MySQL / MariaDB
pip install pymongo             # MongoDB
```

API-nya: `GET /api/teams/credentials`, `PUT /api/teams/credentials` dengan `{"values": {"telegram_bot_token": "..."}}` (nilai kosong berarti hapus), dan `POST /api/teams/credentials/test` dengan `{"group": "telegram" | "search" | "database", "database": "postgres"}`.

## Mengirim file ke chat

Agent mengirim file ke chat dengan tool `share_file`: video, gambar, dan audio diputar langsung di chat, sedangkan file lain (PDF, spreadsheet, dokumen, arsip) jadi tombol unduh. Tool ini otomatis ditawarkan setiap kali agent juga mendapat tool untuk membuat file (`file_write`, `execute_command`, `media_convert`, `archive`, atau `file_patch`). Jadi agent bisa langsung mengirim laporan atau spreadsheet yang baru dibuatnya, tanpa kamu harus menulis "kirim file".
