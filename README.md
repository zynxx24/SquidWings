# SquidWings

Agent AI multi-model untuk terminal dan web. Agent ini mengerjakan tugas sungguhan: membaca dan mengubah kode, menjalankan perintah di sandbox, membaca web, dan mengingat konteks per chat. Lapisan pengawasnya dibuat supaya model murah bekerja mendekati model mahal.

## Fitur

| Fitur | Keterangan |
|---|---|
| Terminal dan Web UI | Satu orchestrator yang sama, tanpa login |
| Multi-model | Routing, fallback, dan banyak provider lewat `config/providers.yaml` |
| Execution harness | Verifikasi hasil, deteksi loop, perbaikan argumen tool, dan eskalasi ke model lebih kuat |
| Tim agent | Kanvas alur seperti n8n: delay, gate OR, jadwal, output Telegram, dan pilihan database |
| Riset web | Mencari, membuka beberapa halaman, membaca isinya, dan mencantumkan sumber |
| Satu chat, satu set | Folder, memori, vault Obsidian, graph kode, skor aislop, dan mode Ponytail sendiri |
| Sandbox | Perintah agent berjalan terisolasi lewat bubblewrap atau Docker |
| Keamanan | Policy engine, path jail, audit log, prompt shield, dan penyamaran rahasia |

## Kebutuhan

- Python 3.12 atau lebih baru
- Endpoint API yang kompatibel dengan OpenAI Chat Completions
- Linux atau macOS
- `bubblewrap` atau Docker, karena tanpa keduanya perintah agent ditolak

Opsional, dan aktif otomatis setelah terpasang:

```bash
npm install -g aislop        # skor kualitas kode
uv tool install graphifyy    # graph kode
```

Chrome atau Chromium dipakai untuk membaca situs yang butuh JavaScript. `cloudflared` dipakai untuk `--tunnel`.

## Instalasi

```bash
git clone https://github.com/zynxx24/SquidWings.git
cd SquidWings
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
sudo apt install bubblewrap
python main.py doctor        # memastikan sandbox benar-benar mengisolasi
```

Isi `API_BASE_URL` dan `API_KEY` di `.env`.

## Menjalankan

```bash
python main.py chat                     # terminal, dengan pilihan folder proyek
python main.py chat --project nama      # langsung ke workspace/projects/nama
python main.py ask "pertanyaan"         # satu pertanyaan
python main.py web                      # Web UI di http://127.0.0.1:8080
python main.py models                   # daftar model
python main.py health                   # cek koneksi API
```

Perintah di dalam `chat`: `/model`, `/stack`, `/ponytail`, `/memory`, `/sandbox`, `/status`, dan `/help` untuk daftar lengkap.

Menu tim agent di web aktif dengan `WEB_TEAMS_ENABLED=true`.

Satu permintaan tim dibatasi 10 menit, dan tiap agent 4 menit serta 120 ribu token. Agent yang kehabisan batas menulis hasilnya, sedangkan laporan yang sudah selesai tetap dipakai. Ubah lewat `TEAM_RUN_SECONDS`, `TEAM_MEMBER_SECONDS`, `TEAM_MEMBER_ROUNDS`, dan `TEAM_MEMBER_TOKENS`.

### Alur tim

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
- **Asisten sehari-hari**: mencari fakta nyata, jeda, gate, menulis jawaban, lalu mengirim ke Telegram dan menyimpan ke database.

Telegram butuh `TELEGRAM_BOT_TOKEN` dan `TELEGRAM_CHAT_ID` di `.env`. Untuk tahu chat ID-mu, kirim pesan ke bot lalu buka `https://api.telegram.org/bot<TOKEN>/getUpdates`. Database selain SQLite butuh alamatnya di `.env` dan drivernya:

```bash
pip install "psycopg[binary]"   # TEAM_DB_POSTGRES_URL
pip install pymysql             # TEAM_DB_MYSQL_URL
pip install pymongo             # TEAM_DB_MONGODB_URL
```

Token dan alamat database hanya disimpan di `.env`, tidak di pengaturan tim.

> **Tidak ada login.** Web UI hanya menerima akses dari mesin ini secara bawaan. Dengan `--tunnel`, siapa pun yang punya URL-nya bisa memakai agent dan membaca semua chat.

## Konfigurasi

Semua pengaturan ada di `.env`, dengan contoh lengkap di [.env.example](.env.example). Tulis komentar di baris sendiri, jangan di baris yang sama dengan nilai.

| Kelompok | Variabel utama |
|---|---|
| API | `API_BASE_URL`, `API_KEY`, `TIMEOUT_SECONDS`, `MAX_RETRIES` |
| Model | `DEFAULT_MODEL`, `DEFAULT_TEMPERATURE`, `DEFAULT_MAX_TOKENS` |
| Web UI | `WEB_HOST`, `WEB_PORT`, `WEB_ALLOWED_HOSTS`, `WEB_ALLOWED_ORIGINS`, `WEB_TEAMS_ENABLED` |
| Sandbox | `SANDBOX_NETWORK`, `SANDBOX_RO_PATHS` |
| Riset web | `SEARXNG_URL`, `BRAVE_SEARCH_API_KEY`, `WEB_BROWSER_ENABLED` |
| Memori | `HISTORY_SUMMARY_CHARS`, `HISTORY_MAX_TOKENS`, `OBSIDIAN_VAULT_PATH` |
| Ponytail | `PONYTAIL_MODE`, `PONYTAIL_TASKS` |
| Tim agent | `TEAM_RUN_SECONDS`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TEAM_DB_POSTGRES_URL`, `TEAM_DB_MYSQL_URL`, `TEAM_DB_MONGODB_URL` |
| Upload | `MAX_UPLOAD_MB`, `MAX_UPLOAD_CHARS`, `MAX_CHAT_IMAGES`, `IMAGE_MAX_SIDE` |

Provider tambahan didaftarkan di `config/providers.yaml`. Key-nya tetap dibaca dari `.env`.

### Hasil riset web

Tanpa API pencarian, agent memakai halaman hasil publik yang sering lemah atau kosong. Untuk hasil yang lebih baik, pakai salah satu:

- **SearXNG:** gratis dan berjalan di mesinmu. Aktifkan `json` di `search.formats` pada `settings.yml`, lalu isi `SEARXNG_URL`.
- **Brave Search API:** isi `BRAVE_SEARCH_API_KEY`. Query dikirim ke Brave.

Agent tidak menembus CAPTCHA atau blokir bot. Situs yang menolak dilaporkan apa adanya, lalu agent memakai sumber lain.

## Data dan memori

Agent bekerja di `workspace/`, terpisah dari kodenya:

- `workspace/sessions/<id>/` untuk setiap chat web dan team run
- `workspace/projects/<nama>/` untuk proyek CLI

Setiap folder punya memorinya sendiri:

| Komponen | Isi | Lokasi |
|---|---|---|
| Memori proyek | Keputusan, fakta, error, dan file | `.agent_memory/` |
| Vault | Catatan chat, bisa dibuka di Obsidian | `.vault/` |
| Graphify | Graph kode | `graphify-out/` |
| aislop | Skor kualitas kode | `.agent_memory/aislop.json` |
| Ponytail | Mode `off`, `lite`, `full`, atau `ultra` | `.agent_memory/session.json` |

Profil pengguna dan playbook berlaku di semua chat, dan disimpan di `vault/`.

Semua data ini diabaikan Git dan tidak ikut terbit. Menghapus chat di web juga menghapus foldernya.

Saat agent membaca isi workspace, PDF dan file Office diubah ke Markdown lewat MarkItDown, lalu dipadatkan dengan parser markdown-it (`utils/markdown_compact.py`). Blok kode tidak diubah. File Markdown panjang juga dipadatkan, kecuali bila agent meminta rentang baris untuk mengedit.

## Sandbox

`execute_command`, `run_checks`, dan tes dari verifikasi berjalan terisolasi. Agent hanya bisa menulis ke folder kerjanya, dan internet mati secara bawaan. Atur `SANDBOX_NETWORK=true` agar `pip install` dan `npm install` bisa jalan. `python main.py doctor` mencoba keluar dari sandbox dan melaporkan hasilnya.

## Execution harness

`agent/harness/` memindahkan disiplin kerja dari model ke loop:

| Lapisan | Fungsi |
|---|---|
| `tiers.py` | Membagi model ke lite, standard, dan frontier, dengan pengawasan lebih ketat untuk tier murah |
| `tool_select.py` | Menawarkan tool yang relevan saja, sehingga skemanya hemat token |
| `guards.py` | Memperbaiki argumen rusak, memangkas hasil panjang, dan mendeteksi loop |
| `verifier.py` | Memeriksa file dan tes sebelum jawaban diterima |
| `advisor.py` | Meminta saran model lebih kuat saat macet, lalu menyerahkan tugasnya bila perlu |
| `playbooks.py`, `briefing.py` | Menyimpan cara kerja yang berhasil dan memakainya lagi |

Panduan kerja ada di `brain/skills/`: `fix-bug`, `add-feature`, `clean-code`, `web-research`, `design-ui`, `build-web`, `build-backend`, dan `animate`.

Skill besar berupa folder dengan `SKILL.md` dan file pendamping. `animate` (diadaptasi dari [cth9191/animate](https://github.com/cth9191/animate), MIT) membuat video animasi pendek sepenuhnya dari kode, dengan gaya cut paper, crosshatch, riso, sketchbook, math, pixel, atau isometric. Agent memasang kit-nya ke `.skills/<sesi>/animate/`, satu salinan per sesi supaya dua chat di proyek yang sama tidak saling campur, lalu bertanya soal cerita, tampilan, dan storyboard sebelum merender. Butuh Node 18+, ffmpeg, dan Chromium untuk Playwright (`npx playwright install chromium`).

Skill punya dua jenis:

| Jenis | Kapan aktif |
|---|---|
| Utama (`scope: core`, bawaan) | Selalu ada di daftar skill. Panduannya ditambahkan saat permintaan cocok |
| Kontekstual (`scope: contextual`) | Diam, tidak ada di prompt sama sekali, sampai permintaan cocok dengan `triggers` dan `context`-nya. Setelah itu tetap aktif sampai sesi itu selesai, dan hanya di sesi itu |

`animate` termasuk kontekstual. "Buat video animasi 30 detik" membangunkannya, sedangkan "tambah animasi hover di tombol" tidak, karena tidak menyebut video. Memanggil `load_skill` dengan namanya juga membangunkannya.

Skill tambahan bisa ditaruh di folder `SKILLS_DIR`, dan skill yang tidak dipakai bisa dimatikan dengan `SKILLS_DISABLED=animate,build-web`.

## Struktur proyek

```text
main.py, cli.py   entry point
agent/            orchestrator, agent loop, harness, tim agent
brain/            classifier, konteks, memori, prompt, skill
model/            API client, provider, registry, stream
tool/             tool lokal, web, memori, dan kode
security/         sandbox, policy, path jail, audit, shield
web/              server FastAPI dan Web UI
config/           settings, model, provider
utils/            logger, CLI UI, workspace
tests/            test suite
```

## Pengembangan

```bash
bash scripts/check_all.sh        # ruff, mypy, compile, pytest, import
python -m pytest -m "not live"   # lewati tes yang memanggil API asli
```

Jangan pernah meng-commit `.env`. Key disimpan di sana, dan `.env.example` hanya berisi contoh.

## Troubleshooting

| Gejala | Solusi |
|---|---|
| Perintah agent ditolak | Pasang bubblewrap atau Docker, lalu jalankan `python main.py doctor` |
| Koneksi ditolak | Periksa `API_BASE_URL` dan `API_KEY`, lalu jalankan `python main.py health` |
| Hasil riset kurang lengkap | Pasang SearXNG atau isi `BRAVE_SEARCH_API_KEY` |
| Gambar tidak terbaca | Model tidak mendukung vision, jadi pilih model lain |
| Nilai env tidak terbaca | Pindahkan komentar ke baris sendiri |
| Menu tim tidak muncul | Atur `WEB_TEAMS_ENABLED=true` |

## Lisensi

SquidWings adalah perangkat lunak berbayar (proprietary). Penggunaan memerlukan lisensi yang dibeli. Lihat [LICENSE](LICENSE) untuk syarat lengkap.
