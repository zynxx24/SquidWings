# Tim agent

Menu ini aktif dengan `WEB_TEAMS_ENABLED=true` (satu-satunya pengaturan tim yang tetap di `.env`, karena menentukan apakah menunya dipasang saat server mulai). Satu permintaan tim dibatasi 10 menit, dan tiap agent 4 menit, 10 putaran, serta 120 ribu token. Ubah di tab **Kredensial** > **Batas tim**; langsung berlaku tanpa restart.

Centang **Mode video** pada tim yang membangun dan merender video. Tim itu dapat 60 menit per permintaan, dan tiap agent 30 menit, 40 putaran, serta 400 ribu token. Skill `animate` juga langsung aktif di sesinya. Angkanya juga diubah di **Batas tim** (isian berawalan "Mode video"). Template **Studio video animasi** sudah memakai mode ini: penulis naskah, animator, lalu editor yang meninjau dan mengirim videonya ke hasil tim.

Agar model murah juga sampai ke video:

- **Batas yang cukup.** Agent mode video tidak dipotong batas tier murah: 40 putaran, panduan skill sampai 7.000 karakter, dan file dibaca per 16.000 karakter. Sebelumnya model flash hanya dapat 20 putaran, panduan 1.500 karakter, dan bacaan 6.000 karakter, sehingga habis waktu membaca ulang kit.
- **Peringatan lebih awal.** Saat 70% putaran, token, atau waktu terpakai, setiap agent tim diminta berhenti membaca dan menulis hasilnya ke file, selagi tool masih tersedia.
- **Jalur pendek.** Penulis naskah menulis `brief.md` di tiga langkah pertama, dengan durasi 15-30 detik. Animator mulai dari demo gaya yang sudah jalan (`styles/<gaya>/demo/`), langsung build, lalu render. Editor merender sendiri bila animator belum sempat. Tim lama yang masih memakai instruksi template lama diperbarui otomatis; instruksi yang sudah kamu ubah tidak disentuh.
- **Laporan sebagai file.** Laporan tiap agent juga disimpan di `team/<id>.md` di folder run, jadi agent berikutnya bisa membuka versi lengkapnya.
- **Render pengaman.** Bila semua agent selesai tanpa video tapi piece (`piece.json` dan `src/`) sudah ditulis, server menjalankan `build.mjs` dan `export.mjs --share` sendiri di sandbox yang sama, lalu membagikan hasilnya.
- **Alasan yang jelas.** Bila tetap tidak ada video, hasil tim menyebut per agent apa yang terjadi, plus hasil render pengamannya, dan laporan lead tetap tampil di bawahnya.

## Editor alur

Tim disusun di kanvas yang dibaca dari kiri ke kanan, seperti editor workflow n8n. Bagian atas punya empat tab: **Editor**, **Eksekusi**, **Data**, dan **Kredensial**.

**Tanpa klik dua kali.** Semua perubahan lewat klik kanan, atau tombol Menu / Shift+F10 pada elemen yang sedang fokus, atau tekan lama di layar sentuh. Node juga punya tombol ⋯ di pojoknya.

**Di kanvas**

- Node pertama selalu **Pemicu**. Klik kanan > **Atur pemicu** untuk memilih kapan alur berjalan.
- **Menyambung:** seret titik di sisi kanan node ke node lain. Lepas di tempat kosong untuk langsung menambah node di situ.
- **Memutus:** klik kanan garisnya > **Putus sambungan**, atau klik garisnya lalu tekan Delete.
- **Klik kanan di node:** edit, ganti nama, duplikat, tambah node setelahnya, atau hapus. **Klik kanan di latar:** tambah node di titik itu, muat semua node, zoom, rapikan, simpan, jalankan.
- Garis putus-putus dibuat otomatis: agent tanpa sambungan keluar mengirim hasilnya ke Lead, Lead ke Reviewer, dan jawaban akhir ke node Telegram atau Database yang belum disambung.
- **Menambah node:** tombol **+** di kanan atas atau tombol Tab membuka daftar node, lengkap dengan kotak pencarian. Tombol + kecil di ujung keluaran node menambah node yang langsung tersambung.
- **Menggeser kanvas:** seret latarnya, atau scroll. Ctrl+scroll untuk zoom.
- **Merapikan:** tombol di pojok kiri bawah memuat semua node ke layar (tombol 1), mengatur zoom, dan merapikan tata letak.
- Ctrl+S menyimpan. Selama ada perubahan yang belum disimpan, label **Belum disimpan** muncul di samping nama alur.

**Detail node.** Klik kanan sebuah node > **Edit node** (atau fokus lalu tekan Enter) untuk membuka tiga kolom:

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
- **Bangun dan verifikasi**, **Riset dan tulis**, **Pendapat kedua**: tiap agent kini punya instruksi kerja sendiri, bukan hanya nama peran.
- **Studio landing page**: brief dengan arah gaya baru dari `style_pick`, copy tanpa kata pengisi, HTML/CSS mobile-first, QA (aislop dan 360px), lead memperbaiki, reviewer menilai. Tiap run tampil beda.
- **Presentasi siap pakai**: riset, alur slide dengan tata letak yang berganti sesuai isi, lalu PowerPoint dengan tema yang belum dipakai di chat lain.
- **Konten media sosial seminggu**: tren, kalender 7 hari, caption, tiga poster satu seri, semuanya dirangkum ke Excel.
- **Buru bug sampai tes lulus**: reproduksi, akar masalah, perbaikan dengan tes regresi; gate membaca baris terakhir laporan, dan bila tes masih gagal model terkuat turun tangan.
- **Keputusan lewat debat**: fakta, lalu pendukung, penentang, dan analis risiko paralel; hakim menulis memo keputusan.
- **Ringkasan berita pagi**: tiap 06:30, berita 24 jam terakhir untuk topikmu, ke Telegram dan database.
- **Laporan dan makalah rapi**: kerangka, riset bersumber, file Word (formal untuk sekolah dan kampus), pemeriksa klaim.
- **Naskah dan voice-over**: naskah per baris, pemilihan suara, MP3 lewat ElevenLabs; tanpa key tetap dapat naskah dan cara menyambungkan.
- **Pantau harga dan beri tanda**: tiap jam harga dari dua sumber dicatat; bila target tercapai, kabar lewat Telegram.

Setiap agent pekerja menutup laporannya dengan **Hasil**, **Bukti**, dan **Belum pasti**. Lead memeriksa file yang disebut laporan sebelum memakainya, dan reviewer ikut mencari tanda AI slop pada karya visual.

## Data: file dan database online

Tab **Data** adalah editor tabel untuk semua sumber data yang bisa dibaca agent. Semua jenis dipakai dengan cara yang sama, jadi boleh campur offline dan online:

| Jenis | Contoh |
|---|---|
| File | `.csv`, `.tsv`, `.json`, `.jsonl`, `.xlsx` |
| Database lokal | SQLite |
| Database online | PostgreSQL, MySQL, MongoDB, Google Sheets |

Tambah sumber lewat tombol **+ Sumber** (di komputer ini: file baru, impor file, contoh toko; online: Google Sheets, atau URL PostgreSQL, MySQL, MongoDB). Sumber yang gagal tersambung langsung dibuang dari daftar. Tidak ada yang berpindah: file tetap di tempatnya, database tetap di server asalnya.

- **Mengedit:** klik kanan sel > **Edit sel**, klik kanan judul kolom (sortir, tambah, ganti nama, hapus kolom), klik kanan nomor baris (tambah atau hapus baris), klik kanan sumber di daftar kiri. Tabel besar dibuka per halaman, dengan kotak cari.
- **Aman:** angka tetap angka (`80.000` jadi 80000). File ditulis atomik dan versi lamanya disalin ke `.backups/`. Bila baris sudah diubah orang lain sejak dibuka, perubahan ditolak (409) supaya tidak menimpa. Perubahan membutuhkan PIN Router bila PIN dipasang.
- **Google Sheets:** buat akun layanan di Google Cloud, tempel kunci JSON-nya di **Kredensial > Google Workspace**, lalu bagikan tiap spreadsheet ke email akun layanan itu sebagai Editor. Tempel URL spreadsheet di **+ Sumber > Google Sheets**.
- **Untuk agent:** di detail node agent, bagian **Akses data** memilih sumber mana yang boleh dipakai dan hak aksesnya: baca, tambah baris saja, baca dan tambah, atau tulis penuh. Kolom bisa disembunyikan per sumber (nama berawalan `_` selalu tersembunyi). Tool-nya: `db_sources`, `db_schema`, `db_read`, `db_search`, `db_append`, `db_update`, `db_delete`, `db_alter`, dan `owner_notify` (memberi tahu Anda lewat Telegram).

## Bot Telegram untuk toko

Dua template siap pakai di daftar template (atau **+ Sumber > Contoh toko** di tab Data; memilih template toko juga otomatis membuat file `katalog` dan `pesanan`):

- **Bot kasir toko** menjawab pelanggan di Telegram: `/start` dan `/bantuan` dijawab langsung, pesan lain ditangani agent kasir yang hanya boleh **membaca katalog** dan **menambah baris pesanan**, lalu semua percakapan dicatat ke tabel `percakapan_toko`.
- **Admin toko** hanya menjawab Anda (kata pemicu `/atur`) dan boleh menulis penuh ke katalog dan pesanan, jadi harga dan stok bisa diubah lewat chat.

Pemicu Telegram punya pilihan **Siapa yang boleh menulis ke bot**: *Hanya saya* (chat ID di Kredensial) atau *Pelanggan (publik)*. Untuk pelanggan publik:

- Hanya chat pribadi yang dilayani. Tiap chat dibatasi 6 pesan per menit, dan ada batas balasan otomatis per hari (bawaan 300, diubah di pemicu). Bila terlampaui, pelanggan diminta menunggu; kejadiannya dicatat di log server.
- Agent publik hanya memakai tool data yang diizinkan plus `time_now` dan `calc`; tidak bisa menulis file, menjalankan perintah, atau browsing. Teks pelanggan dan nama pengirim diperlakukan sebagai data, bukan perintah.
- Riwayat percakapan per pelanggan (bawaan 6 giliran) disimpan 7 hari di `chats.json` di folder tim (izin 0600).
- Error tidak pernah dikirim ke Telegram. Bila alur gagal, pelanggan menerima permintaan maaf singkat, dan kegagalannya terlihat di tab **Eksekusi** serta log server.
- Agent hanya bisa mengabari Anda lewat Telegram (`owner_notify`) bila node-nya dicentang **Boleh mengabari saya lewat Telegram**. Template toko mencentangnya, tim lain tidak.
- Bila beberapa tim berstatus publik, pesan pelanggan dilayani tim pertama yang kata pemicunya cocok. Jadi satu bot sebaiknya satu tim publik.

## Kredensial

Tab **Kredensial** mengatur key dan ID tanpa membuka `.env` di server:

| Bagian | Isi | Tombol Tes |
|---|---|---|
| Telegram | Bot token (dari @BotFather) dan Chat ID | Mengirim pesan tes ke chat itu |
| Web search | Brave Search API key dan URL SearXNG, dipakai tool web search agent | Satu pencarian berisi satu hasil |
| ElevenLabs (suara) | API key dan Voice ID bawaan, dipakai `voice_list` dan `voice_speak` | Mengambil daftar suara dan sisa kuota, tanpa memakai karakter |
| Google Workspace | Kunci akun layanan (JSON) untuk Google Sheets | Meminta token ke Google dan menampilkan email yang harus diberi akses |
| Database | Lokasi file SQLite, URL PostgreSQL, MySQL, dan MongoDB | Membuka koneksi lalu menutupnya, tanpa menulis apa pun |
| Batas tim | Waktu, putaran, dan token per permintaan (biasa dan mode video) | tidak ada |

Untuk tahu Chat ID-mu, kirim pesan ke bot lalu buka `https://api.telegram.org/bot<TOKEN>/getUpdates`.

Nilai yang diisi di sini menang atas `.env` dan langsung berlaku, tanpa restart. Itu termasuk polling Telegram dan tool web search. Label di tiap isian menunjukkan sumber nilainya: **Dari web**, **Dari .env**, **Bawaan**, atau **Belum diisi**. **Hapus** membuang nilai dari web, sehingga nilai dari `.env` dipakai lagi. Halaman hanya menerima versi tersamar dari setiap secret, misalnya `…mmnn`. Semuanya disimpan di `web/data/credentials.json` dengan izin 0600, dan tidak ikut git. Pindahkan dengan `CREDENTIALS_FILE`.

Database selain SQLite butuh drivernya:

```bash
pip install "psycopg[binary]"   # PostgreSQL
pip install pymysql             # MySQL / MariaDB
pip install pymongo             # MongoDB
```

API-nya: `GET /api/teams/credentials`, `PUT /api/teams/credentials` dengan `{"values": {"telegram_bot_token": "..."}}` (nilai kosong berarti hapus), dan `POST /api/teams/credentials/test` dengan `{"group": "telegram" | "search" | "voice" | "google" | "database", "database": "postgres"}`.

## Mengirim file ke chat

Agent mengirim file ke chat dengan tool `share_file`: video, gambar, dan audio diputar langsung di chat, sedangkan file lain (PDF, spreadsheet, dokumen, arsip) jadi tombol unduh. Tool ini otomatis ditawarkan setiap kali agent juga mendapat tool untuk membuat file (`file_write`, `execute_command`, `media_convert`, `archive`, atau `file_patch`). Jadi agent bisa langsung mengirim laporan atau spreadsheet yang baru dibuatnya, tanpa kamu harus menulis "kirim file".
