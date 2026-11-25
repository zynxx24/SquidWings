<div align="center">

# 🦑 SquidWings

### Agent AI open-source untuk terminal dan web: coding, riset, dokumen, video, dan trading

**Open-source AI agent for the terminal and web browser. Works with any OpenAI-compatible model: Claude, GPT, Gemini, DeepSeek, Qwen, Llama, or a local model in Ollama.**

![Python](https://img.shields.io/badge/python-3.12%20%7C%203.14-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-555)
![API](https://img.shields.io/badge/API-OpenAI--compatible-10a37f)
![Sandbox](https://img.shields.io/badge/sandbox-bubblewrap%20%7C%20Docker-orange)
![License](https://img.shields.io/badge/license-AGPL--3.0-blue)
![Stars](https://img.shields.io/github/stars/zynxx24/SquidWings?style=flat&logo=github)

[Mulai cepat](#-mulai-cepat) · [Fitur](#-fitur) · [Add-on](#-add-on-trading-dan-studio) · [Struktur file](#-struktur-file) · [Pengaturan](#%EF%B8%8F-pengaturan) · [Pengembangan](#-pengembangan) · [Lisensi](#-lisensi) · [English](#-in-english)

</div>

---

SquidWings adalah **AI agent open-source berbahasa Indonesia** yang berjalan di laptopmu sendiri, lewat terminal (CLI) atau Web UI. Agent ini bisa ngobrol, mengerjakan tugas, membaca PDF dan dokumen Office, meriset web dengan sumber, membuat video, dan mengerjakan kode sungguhan: mengubah file, menjalankan perintah di sandbox, lalu memeriksa hasilnya sendiri.

Semua fitur gratis di bawah lisensi AGPL-3.0, termasuk panel trading MT4/MT5 dan editor video. Cocok untuk siapa saja yang ingin asisten AI pribadi seperti Claude Code, Cursor, atau Open Interpreter, tapi dengan model pilihan sendiri. Bisa juga pakai model yang murah atau jalan lokal.

**Kenapa SquidWings?**

- **Model murah terasa pintar.** Execution harness memeriksa hasil, memutus loop, memperbaiki argumen tool, dan naik ke model yang lebih kuat hanya saat perlu.
- **Provider bebas.** Cukup URL dan API key dari OpenRouter, OpenAI, Groq, 9Router, Ollama, LM Studio, atau endpoint lain yang kompatibel dengan OpenAI.
- **Aman dipakai.** Perintah agent berjalan di sandbox bubblewrap atau Docker, dengan policy engine, audit log, dan prompt shield.
- **Lengkap dalam satu aplikasi.** Chat, tim agent seperti n8n, 144 tool, router model, terminal web, suara, trading, dan editor video.

## 🚀 Mulai cepat

Satu bahasa (Python 3.12+), satu perintah, jalan di **Windows, macOS, dan Linux**:

```bash
git clone https://github.com/zynxx24/SquidWings.git
cd SquidWings
python scripts/setup.py     # bikin .venv dan pasang requirements (pakai uv kalau ada)
```

Lalu aktifkan venv dan jalankan:

| OS | Aktifkan | Jalankan |
|---|---|---|
| Linux, macOS | `source .venv/bin/activate` | `python main.py web` |
| Windows | `.venv\Scripts\activate` | `python main.py web` |

Buka http://127.0.0.1:8080. Provider dan API key bisa diatur di halaman **Router**, atau salin `.env.example` ke `.env`.

| Langkah opsional | Perintah |
|---|---|
| Sandbox perintah agent | Linux: `sudo apt install bubblewrap`. macOS dan Windows: Docker Desktop, atau `SANDBOX_MODE=off` |
| Cek sandbox benar-benar mengisolasi | `python main.py doctor` |
| Program media (ffmpeg, Node.js, ...) | `python main.py tools`, lalu `python main.py tools install` |
| Add-on trading atau Studio | `python main.py addon install trading` (lihat [Add-on](#-add-on-trading-dan-studio)) |

Di Windows, panel Terminal memakai PowerShell lewat `pywinpty`, dan perintah agent memakai Git Bash kalau ada (kalau tidak, PowerShell). Diuji di Python 3.14 (direkomendasikan) dan 3.12.

**Sudah terpasang dengan Python lama?** Hentikan SquidWings, lalu jalankan `bash scripts/upgrade_python.sh`. Skrip ini memasang Python 3.14 lewat uv tanpa mengubah python3 bawaan sistem, menyimpan venv lama sebagai `.venv-py3.12`, membuat venv baru, lalu memeriksanya. Kalau ada langkah yang gagal, venv lama dikembalikan otomatis.

### 🔄 Pembaruan otomatis

Tidak perlu mengunduh ulang. Setiap kali dijalankan, SquidWings mengambil versi terbaru dari GitHub lalu memulai ulang dengan kode baru. Selama `chat` atau `web` berjalan, pengecekan diulang tiap 30 menit, dan web UI memberi tahu bila server perlu dijalankan ulang.

- **Hasil `git clone`:** langsung berjalan.
- **Hasil *Download ZIP*:** pada start pertama folder itu otomatis disambungkan ke repo ini (perlu `git` terpasang). Setelah itu pembaruannya sama seperti clone.
- **Perubahan Anda aman.** Hanya fast-forward. File yang Anda ubah tetap, dan bila pembaruan menyentuh file yang sama, pembaruan ditunda. `.env`, `workspace/`, dan data lain yang di-ignore tidak pernah disentuh.
- Perbarui sekarang: `python main.py update`. Matikan: `AUTO_UPDATE=false` di `.env`, atau `python main.py --no-update web`.

**Dari HP:** jawaban tetap dikerjakan di server walau layar mati atau pindah aplikasi; buka lagi halamannya dan jawabannya menyusul sendiri. Hanya tombol stop yang menghentikan.

> [!IMPORTANT]
> **Tidak ada login.** Web UI hanya menerima akses dari mesin ini secara bawaan. Dengan `--tunnel`, siapa pun yang punya URL-nya bisa memakai agent dan membaca semua chat.

## ✨ Fitur

| Fitur | Keterangan |
|---|---|
| 💬 Terminal dan Web UI | Satu orchestrator yang sama untuk CLI dan browser |
| 🧑‍💻 AI coding agent | Membaca dan mengubah file, menjalankan perintah dan tes di sandbox, lalu memeriksa hasilnya |
| 🫶 Teman ngobrol dan tugas | Mendengarkan saat curhat, menjawab tugas dengan bahasa wajar, dan ingat isi chat sebelumnya |
| 📄 Baca dokumen | Teks dan gambar dari PDF, Word, PowerPoint, Excel, EPUB, ZIP, dan scan (OCR lokal) |
| 🔌 Provider bebas | Endpoint apa pun yang kompatibel dengan OpenAI. Daftar model diambil otomatis |
| 🧠 Execution harness | Verifikasi hasil, deteksi loop, perbaikan argumen tool, dan eskalasi ke model lebih kuat |
| 🧰 Toolbox | 144 tool (hitung, tanggal, satuan, tabel, uang, peta, cuaca, kurs, kalkulator risiko, Office, PDF, voice-over, video dan audio) yang tersembunyi sampai dibutuhkan |
| 🎬 Video dan audio | Unduh video, audio, atau subtitle dari YouTube dan ±1800 situs (yt-dlp), transkripsi lokal (faster-whisper), dan video dari kode yang dirender ke MP4 (HyperFrames atau Remotion) |
| 🎞️ Studio video | Editor motion di browser: klip, keyframe, catatan, dan chat AI untuk revisi |
| 📈 Trading | Panel MT4/MT5, model kuant dan neural, LLM analyst, simulasi, dan auto trade dengan stop loss |
| 📑 Buat dokumen | Word, PowerPoint, Excel, CSV, dan PDF yang rapi, langsung muncul di chat untuk diunduh |
| 🗺️ Google Maps | Impor hasil scrape, saring dan rangking tempat, jarak, dan ekspor ke Excel atau GeoJSON |
| 👥 Tim agent | Kanvas alur seperti n8n: delay, gate OR, jadwal, bot Telegram, dan database |
| 🔎 Riset web | Mencari (SearXNG, Brave), membuka beberapa halaman, membaca isinya, dan mencantumkan sumber |
| 🖥️ Terminal web | Terminal per chat di browser; agent bisa memakai sudo bila kamu izinkan |
| 🎙️ Suara dan brief | Bicara ke agent, jawaban dibacakan, dan ringkasan harian otomatis |
| 🗂️ Satu chat, satu set | Folder, memori, vault Obsidian, graph kode, skor aislop, dan mode Ponytail sendiri |
| 🛡️ Sandbox dan keamanan | Perintah agent terisolasi, policy engine, path jail, audit log, prompt shield, penyamaran rahasia |
| 🔀 Router model | Provider, URL, dan API key dari web; rotasi pintar, model default, dan pemakaian token |
| 📚 Skill dari GitHub | Repo skill (superpowers, anthropics/skills, GSAP, Remotion, humanizer, dan lainnya) di-clone dan diperbarui otomatis. Hanya skill penting yang aktif, sisanya tersimpan, dan skill pilihan ikut tersedia di Claude Code |

## 🧠 Dua mode model

| Mode | Perilaku |
|---|---|
| **Pilih sendiri** | Model yang kamu pilih di selector (atau `/model` di terminal) selalu dipakai. Kalau gagal, chat menjelaskan sebabnya (key ditolak, saldo habis, rate limit, model tidak ada, konteks kepanjangan, timeout, server error) dan **tidak** diam-diam pindah ke model lain |
| **Auto** | Autopilot (`ROUTING_QUALITY=smart`) memilih model termurah yang andal untuk tingkat kesulitan permintaan: easy ke model ringan, hard ke model terkuat. Kerja dengan tool tidak pernah ke model ringan, dan setelah giliran yang gagal giliran berikutnya naik satu tier. Alasannya tampil di chat |

`ROUTING_QUALITY=max` mengirim semua ke model terkuat, `balanced` memakai tabel di `brain/config/models_config.yaml`.

Supaya tetap pintar dan hemat saat skill dan tool makin banyak: indeks skill diurutkan dari yang paling relevan (tanpa batas buatan; konteks sampai 1 juta token), trigger dibobot menurut kelangkaannya, tool toolbox baru dikirim saat diminta, dan semua cache (klasifikasi, halaman web, indeks) dikelola di satu tempat dengan statistik di `/api/cache`. Detail di [brain/harness/README.md](brain/harness/README.md).

## 🧩 Add-on: trading dan Studio

Keduanya ada di repo ini (folder `addons/`), gratis, dan **mati bawaan**. Nyalakan dengan setup manual:

```bash
python main.py addon list              # apa saja, aktif atau mati, program yang belum ada
python main.py addon install trading   # pasang paket (torch, MetaTrader5 di Windows), nyalakan, tampilkan langkah manual OS ini
python main.py addon install studio    # cek dan pasang ffmpeg, nyalakan
python main.py addon remove trading    # matikan lagi; file dan data tetap
```

Tiap add-on punya `addon.json` (file yang dibutuhkan, paket, program, langkah manual per OS) dan `README.md` sendiri.

- **Trading:** panel `/trading` dengan chart dan tiket order, jembatan EA MT4 dan adapter MT5, model kuant, otak neural, LLM analyst, simulasi live, auto trade dengan stop loss dan trailing stop, tool `trading_snapshot`, dan skill `analis-trading`. Pengaturannya ada di `addons/trading/env.enterprise.example`, dan panduannya di [docs/trading.md](docs/trading.md).
- **Studio video:** editor motion dengan keyframe, catatan, dan chat revisi.

Saklarnya disimpan di `addons/enabled.json` (tidak ikut Git). `ADDON_TRADING=1` atau `ADDON_STUDIO=0` di `.env` mengalahkan saklar itu. Add-on yang mati atau filenya tidak lengkap disembunyikan, dan bagian lain tetap berjalan. Data trading (`addons/trading/data/`: jurnal, histori candle, bobot model) tidak ikut di repo.

> [!WARNING]
> Fitur trading tidak menjamin keuntungan. Uji di akun demo dulu; seluruh keputusan dan risiko trading ada pada pemakainya.

## 📁 Struktur file

Kode dibagi per segmen. Setiap folder punya `README.md` sendiri yang menjelaskan isinya.

```text
SquidWings/
├── main.py          entry point: chat, ask, web, addon, skills, tools, doctor, trading
├── CLAUDE.md        konteks proyek untuk Claude Code (fork lalu buka claude di folder ini)
├── brain/           otak + harness: agent/ (orchestrator, loop, tim), harness/ (autopilot, skill & tool),
│                    classifier, memori, model, prompt, config, utils (cache, platform), security
├── input/           apa yang dikirim ke model: konteks, batas token, cara berpikir, gaya desain
├── skills/          panduan kerja: builtin/, skill GitHub (sources.json), adapters/
├── tools/           semua tool AI, satu folder per segmen, plus toolbox tersembunyi
├── web/             server FastAPI dan halaman chat
├── addons/          fitur tambahan, mati bawaan: trading/, studio/
├── scripts/         setup.py, check_all.sh, tunnel.sh, upgrade_python.sh
├── tests/           test suite offline
└── docs/            panduan lengkap
```

| Segmen | Baca |
|---|---|
| Otak, agent, model, cache | [brain/README.md](brain/README.md) |
| Harness: autopilot, skill & tool | [brain/harness/README.md](brain/harness/README.md) |
| Input ke model | [input/README.md](input/README.md) |
| Skill dan adapter | [skills/README.md](skills/README.md) |
| Tool | [tools/README.md](tools/README.md) |
| Web | [web/README.md](web/README.md) |
| Add-on | [addons/README.md](addons/README.md) |
| Skrip | [scripts/README.md](scripts/README.md) |
| Test | [tests/README.md](tests/README.md) |

Folder yang dibuat saat jalan dan **tidak** ikut Git:

| Folder | Isi |
|---|---|
| `data/` | Log (`data/logs/`), audit chain (`data/audit.jsonl`), laporan (`data/reports/`). `SQUIDWINGS_DATA_DIR` memindahkannya |
| `workspace/` | Folder kerja tiap chat (`sessions/`) dan proyek dari `python main.py chat` (`projects/`) |
| `vault/` | Profil pengguna dan playbook, berlaku di semua chat |
| `web/data/` | Provider router, kredensial dari web, run tim |
| `brain/memory/chat_store/` | Riwayat chat |
| `.agent_memory/` | Statistik model, brief harian, riwayat gaya |
| `skills/.sources/` | Clone skill dari GitHub |
| `addons/enabled.json`, `addons/trading/data/` | Saklar add-on, data trading |

`workspace/` dan `vault/` sengaja tetap di root, karena chat yang tersimpan menunjuk ke sana dengan path lengkap. File lama yang dulu ditulis ke root (`*.log`, `audit.jsonl`, `logs/`, `reports/`) dipindah otomatis ke `data/` saat SquidWings start.

## ⚙️ Pengaturan

Pengaturan ada di tiga tempat. Yang di web langsung berlaku tanpa restart.

| Tempat | Untuk apa |
|---|---|
| `.env` | Semua pengaturan dasar. Contoh lengkap di [.env.example](.env.example). Tulis komentar di baris sendiri |
| Web → **Router & token** | Provider, URL, API key, model default, dan rotasi |
| Web → **Tim agent → Kredensial** | Token Telegram, Brave/SearXNG, dan URL database |
| `brain/config/models_config.yaml` | Batas konteks per model dan aturan routing tetap |
| `brain/config/providers.yaml` | Beberapa endpoint sekaligus |
| `brain/security/policy.yaml` | Aturan apa yang boleh dan tidak boleh dilakukan agent |

<details>
<summary><b>Variabel <code>.env</code> utama</b></summary>

| Kelompok | Variabel |
|---|---|
| API | `API_BASE_URL`, `API_KEY`, `TIMEOUT_SECONDS`, `MAX_RETRIES` |
| Model | `DEFAULT_MODEL` (boleh kosong), `DEFAULT_TEMPERATURE`, `DEFAULT_MAX_TOKENS`, `MODEL_SYNC_EVERY_S` |
| Web UI | `WEB_HOST`, `WEB_PORT`, `WEB_ALLOWED_HOSTS`, `WEB_ALLOWED_ORIGINS`, `WEB_TEAMS_ENABLED` |
| Agent | `AGENT_TOOLS_ENABLED`, `AGENT_DISABLED_TOOLS`, `HARNESS_ADVISOR`, `SKILLS_DIR`, `SKILLS_DISABLED` |
| Sandbox | `SANDBOX_MODE`, `SANDBOX_NETWORK`, `SANDBOX_MEMORY_MB`, `SANDBOX_RO_PATHS` |
| Riset web | `SEARXNG_URL`, `BRAVE_SEARCH_API_KEY`, `WEB_BROWSER_ENABLED` |
| Memori | `HISTORY_SUMMARY_CHARS`, `HISTORY_MAX_TOKENS`, `OBSIDIAN_VAULT_PATH` |
| Ponytail | `PONYTAIL_MODE`, `PONYTAIL_TASKS` |
| Tim agent | `WEB_TEAMS_ENABLED`. Selebihnya (batas tim, Telegram, database, Google Sheets, SQLite) diatur di UI: Tim agent > Kredensial. Nama env lama tetap berlaku sebagai cadangan |
| Upload | `MAX_UPLOAD_MB`, `MAX_UPLOAD_CHARS`, `UPLOAD_INLINE_CHARS`, `DOCUMENT_OCR`, `OCR_MAX_IMAGES`, `OCR_TIER`, `OCR_IMAGE_S`, `OCR_TOTAL_S`, `SEND_IMAGES` |
| Alat lokal | `GRAPHIFY_AUTO`, `AISLOP_AUTO`, `TOOLS_AUTO_UPDATE` |
| Suara dan brief | `VOICE_LANG`, `VOICE_STT_MODEL`, `ELEVENLABS_API_KEY`, `DAILY_BRIEF`, `BRIEF_TIME` |
| Add-on | `ADDON_TRADING`, `ADDON_STUDIO` (`1` nyala, `0` mati; mengalahkan `python main.py addon …`). `ENTERPRISE_*=0` lama tetap mematikan |

</details>

<details>
<summary><b>Menghubungkan provider</b></summary>

SquidWings memakai endpoint apa pun yang kompatibel dengan OpenAI Chat Completions. Isi dua baris ini di `.env`, atau tambahkan lewat halaman Router:

```bash
API_BASE_URL=https://openrouter.ai/api/v1
API_KEY=kunci-kamu
```

Lalu jalankan `python main.py models`. Tidak ada nama model yang tertanam di kode: daftarnya diambil dari `/models`, dan mode Auto memilih di antaranya.

| Provider | `API_BASE_URL` |
|---|---|
| OpenAI | `https://api.openai.com/v1` |
| OpenRouter | `https://openrouter.ai/api/v1` |
| Groq | `https://api.groq.com/openai/v1` |
| 9Router | `http://localhost:20128/v1` |
| Ollama | `http://localhost:11434/v1` |
| LM Studio | `http://localhost:1234/v1` |

Ollama dan LM Studio tetap butuh `API_KEY`, tetapi isinya boleh apa saja.

Nama model yang berisi `flash`, `mini`, atau `lite` dianggap murah dan dipakai untuk tugas mudah. Nama seperti `opus` dipakai untuk tugas sulit. Untuk memaksa satu model, isi `DEFAULT_MODEL`, atau tulis di `routing` pada `brain/config/models_config.yaml`. Bila endpoint tidak melaporkan batasnya, agent memakai perkiraan aman: konteks 32 ribu token dan output 4 ribu token.

</details>

<details>
<summary><b>Pilihan model pada mode Auto</b></summary>

- Kandidat diambil dari `brain/config/models_config.yaml` dan dari daftar model yang sedang dikembalikan API. Model baru dari provider ikut dicoba tanpa mengubah konfigurasi.
- Tugas mudah memakai model murah. Tugas sedang memakai semua model kecuali tier frontier. Tugas sulit memakai model yang dikonfigurasi.
- Tiap pilihan melihat catatan sukses, gagal, dan latensi model, ditambah sedikit acak supaya model baru tetap kebagian giliran.

Router menilai keandalan dan kecepatan, bukan mutu jawaban. Catatannya ada di `.agent_memory/model_stats.json`, hanya berisi angka per model, tanpa isi chat. Daftar model diambil ulang tiap 30 menit lewat `MODEL_SYNC_EVERY_S`.

</details>

<details>
<summary><b>Hasil riset web yang lebih baik</b></summary>

Tanpa API pencarian, agent memakai halaman hasil publik yang sering lemah. Pakai salah satu:

- **SearXNG:** gratis dan berjalan di mesinmu. Aktifkan `json` di `search.formats` pada `settings.yml`, lalu isi `SEARXNG_URL`.
- **Brave Search API:** isi `BRAVE_SEARCH_API_KEY`. Query dikirim ke Brave.

Agent tidak menembus CAPTCHA atau blokir bot. Situs yang menolak dilaporkan apa adanya.

</details>

## ▶️ Menjalankan

```bash
python main.py chat                     # terminal, dengan pilihan folder proyek
python main.py chat --project nama      # langsung ke workspace/projects/nama
python main.py ask "pertanyaan"         # satu pertanyaan
python main.py web                      # Web UI di http://127.0.0.1:8080
python main.py web --tunnel             # Web UI lewat Cloudflare Tunnel
python main.py web --replace            # port dipakai SquidWings lama: matikan yang lama dulu
python main.py models                   # daftar model
python main.py health                   # cek koneksi API
python main.py doctor                   # uji isolasi sandbox
python main.py tools                    # program media yang terpasang (ffmpeg, yt-dlp, Node, ...)
python main.py tools install            # pasang yang belum ada, tanpa sudo
python main.py update                   # ambil versi terbaru dari GitHub sekarang
python main.py skills                   # skill dari GitHub: status (sync, update, link, search)
python main.py addon list               # add-on trading dan Studio: status, install, enable, remove
python main.py trading                  # server trading port 80 untuk EA MT4 (add-on trading)
```

Perintah di dalam `chat`: `/model` (pilih dan kunci model), `/model auto` (balik ke Auto), `/model <id>`, `/cache` (statistik cache, `/cache clear`), `/stack`, `/ponytail`, `/memory`, `/chats`, `/brief`, `/voice`, `/sandbox`, `/status`, dan `/help`. Setiap jawaban diawali satu baris: jenis tugas, tingkat kesulitan, dan model (🔒 kalau dipilih sendiri, atau alasan pilihan Auto).

**Suara.** `/voice` merekam lewat mikrofon (perlu `arecord` atau `parecord`), mengubahnya jadi teks, lalu menampilkan hasilnya: Enter untuk kirim, atau ketik perbaikan. Pengenal suara memakai `faster-whisper` (lokal, sudah termasuk di `requirements.txt`; model diunduh sekali saat pertama dipakai). Bila tidak terpasang, `SpeechRecognition` dipakai, yang mengirim audio ke layanan online Google. Transkripsi audio dan video yang diunggah ke chat memakai mesin yang sama. `/voice on` membacakan jawaban dengan ElevenLabs (butuh API key dan `ffplay` atau `mpv`); `/voice status` menunjukkan apa yang sudah siap. Di Web UI ada tombol mikrofon di kotak pesan dan tombol *Bacakan* di bawah jawaban; keduanya memakai fitur suara browser, jadi tidak butuh key, dan pesan tidak pernah terkirim sendiri.

**Video dan audio.** Minta saja dalam chat, misalnya "ringkas video YouTube ini", "unduh audionya", "buat subtitle dari rekaman rapat ini", atau "bikin video intro 15 detik vertikal". Skill `video-audio` memilih tool-nya:

| Tool | Untuk |
|---|---|
| `media_info`, `media_download` | Info, video (MP4), audio (MP3), atau hanya subtitle dari satu link lewat yt-dlp. Hanya alamat publik, tidak pernah satu playlist sekaligus |
| `transcribe` | Suara ke `.txt`, `.srt`, `.vtt`, dan waktu per kata, dihitung di mesin ini |
| `video_new`, `video_docs`, `video_check`, `video_render` | Proyek **HyperFrames** (HTML + GSAP) atau **Remotion** (React), diperiksa, lalu dirender ke MP4 dan tampil di chat |
| `media_tools_status` | Apa yang terpasang dan perintah pasang untuk sisanya |

Program di luar Python (ffmpeg, Node.js 22+ untuk HyperFrames, Deno opsional untuk YouTube) dicek dengan `python main.py tools` dan dipasang dengan `python main.py tools install`, lewat **Homebrew** bila ada. Homebrew sendiri dipasang sekali dengan perintah resminya, yang ditampilkan oleh `python main.py tools`. yt-dlp ikut diperbarui otomatis setiap hari (`TOOLS_AUTO_UPDATE`), karena versi yang tertinggal beberapa bulan biasanya sudah tidak bisa mengunduh dari YouTube.

**Brief harian.** Sekali sehari agent merangkum chat sejak kemarin: yang dikerjakan, file yang dibuat atau diubah, dan yang masih menggantung (pesan belum dijawab, pertanyaan untukmu, langkah gagal). Terminal menampilkannya saat start pertama hari itu, Web UI sebagai kartu di halaman kosong, dan `/brief` kapan saja. Dibuat dengan aturan biasa tanpa memanggil model, disimpan di `.agent_memory/briefs/`.

## 📎 Mengunggah dokumen

Tekan ikon klip di kotak chat, pilih file, lalu kirim. Isi file diekstrak apa adanya, lalu agent sendiri yang membaca maksudnya.

| File | Teks | Gambar |
|---|---|---|
| PDF | Baris per baris, dengan tabel di tempatnya | Gambar di tiap halaman. Halaman scan jadi satu gambar utuh |
| Word, PowerPoint, Excel, EPUB, ZIP | Lewat MarkItDown | Semua gambar di dalam file |
| OpenDocument (`.odt`, `.odp`, `.ods`) | Dari isi dokumennya | Semua gambar di dalam file |
| Teks dan kode | Apa adanya | Tidak ada |

Teks lengkap masuk ke `uploads/` di folder chat, jadi agent bisa membukanya lagi tanpa unggah ulang. Gambar dan halaman scan dibaca OCR lokal (RapidOCR di CPU, tanpa internet, sekitar 3 detik per halaman). Gambar tidak dikirim ke model, kecuali `SEND_IMAGES=true`.

## 🗄️ Data dan memori

Setiap folder kerja punya memorinya sendiri:

| Komponen | Isi | Lokasi |
|---|---|---|
| Memori proyek | Keputusan, fakta, error, file, dan catatan per giliran | `.agent_memory/` |
| Vault | Catatan chat, bisa dibuka di Obsidian | `.vault/` |
| Graphify | Graph kode | `graphify-out/` |
| aislop | Skor kualitas kode | `.agent_memory/aislop.json` |
| Ponytail | Mode `off`, `lite`, `full`, atau `ultra` | `.agent_memory/session.json` |

Semua data ini diabaikan Git. Menghapus chat di web juga menghapus foldernya.

## 🛡️ Sandbox

`execute_command`, `run_checks`, dan tes dari verifikasi berjalan terisolasi lewat bubblewrap atau Docker. Agent hanya bisa menulis ke folder kerjanya, dan internet mati secara bawaan. `SANDBOX_NETWORK=true` mengizinkan `pip install` dan `npm install`. `python main.py doctor` mencoba keluar dari sandbox dan melaporkan hasilnya.

Alat opsional yang aktif otomatis setelah terpasang:

```bash
npm install -g aislop        # skor kualitas kode
uv tool install graphifyy    # graph kode
```

## 📚 Dokumentasi

| Panduan | Isi |
|---|---|
| [Cara kerja agent](docs/cara-kerja-agent.md) | Harness, tier model, dan skill |
| [Router model](docs/router.md) | Provider, URL, dan API key dari web; rotasi, model default, dan pemakaian token |
| [Tim agent](docs/tim-agent.md) | Kanvas alur, jadwal, Telegram, dan database |
| [Trading](docs/trading.md) | Panel, EA MT4, adapter MT5, model kuant, dan batas risiko |
| README per segmen | Lihat tabel di [Struktur file](#-struktur-file) |
| [Catatan perubahan](docs/CHANGELOG.md) | Apa yang berubah dan kapan |

## 🧪 Pengembangan

```bash
bash scripts/check_all.sh        # ruff, mypy, compile, pytest, import
python -m pytest -q              # test suite offline
python -m tests.routing_eval     # akurasi routing: jenis tugas, skill, dan tool per prompt
```

Kasus routing ada di `tests/eval_data/*.json`; menambah prompt tidak perlu menyentuh kode. Kasus bertanda `"gap": true` adalah kekurangan yang sudah diketahui: tesnya gagal-yang-diharapkan, jadi saat diperbaiki tes memintamu menghapus tandanya.

Test menyalakan kedua add-on sendiri dan memakai folder `data/` sementara, jadi tidak menyentuh data asli. Jangan pernah meng-commit `.env`: key disimpan di sana, dan `.env.example` hanya berisi contoh.

## 🩺 Troubleshooting

| Gejala | Solusi |
|---|---|
| Perintah agent ditolak | Linux: pasang bubblewrap. macOS/Windows: jalankan Docker Desktop, atau `SANDBOX_MODE=off`. Lalu `python main.py doctor` |
| Panel Terminal tidak jalan di Windows | `pip install pywinpty` (sudah ada di requirements.txt untuk Windows) |
| Koneksi ditolak | Periksa `API_BASE_URL` dan `API_KEY`, lalu jalankan `python main.py health` |
| Hasil riset kurang lengkap | Pasang SearXNG atau isi `BRAVE_SEARCH_API_KEY` |
| Gambar atau PDF scan tidak terbaca | Periksa `DOCUMENT_OCR=true` dan `pip install -r requirements.txt` (rapidocr). `OCR_TIER=max` untuk scan sangat sulit |
| Nilai env tidak terbaca | Pindahkan komentar ke baris sendiri |
| Menu tim tidak muncul | Atur `WEB_TEAMS_ENABLED=true` |
| Menu Trading atau Studio tidak ada | Add-on mati bawaan: `python main.py addon install trading` (atau `studio`), lalu restart |
| Port 8080 sudah terpakai | `web` pindah sendiri ke port kosong berikutnya. Untuk SquidWings lama, pakai `--replace` |

## 📜 Lisensi

SquidWings gratis dan untuk dibagikan, dirilis di bawah **GNU Affero General Public License v3.0** (AGPL-3.0-only), termasuk modul trading dan Studio. Teks lengkapnya ada di [LICENSE](LICENSE), sedangkan atribusi dan komponen pihak ketiga ada di [NOTICE](NOTICE).

Ringkasan singkat (bukan pengganti teks lisensi):

- **Bebas dipakai, diubah, dibagikan, dan di-fork**, termasuk untuk keperluan komersial.
- **Kode sumber tetap terbuka.** Jika kamu membagikan SquidWings atau turunannya, atau menjalankannya sebagai layanan yang diakses orang lain lewat jaringan (misalnya Web UI atau tunnel), kamu wajib menyediakan seluruh kode sumber versimu dengan lisensi yang sama.
- **Atribusi dipertahankan.** Pemberitahuan hak cipta, LICENSE, NOTICE, dan tautan ke repositori asli tidak boleh dihapus.
- Disediakan **tanpa jaminan apa pun**. Fitur trading tidak menjamin keuntungan. Komponen pihak ketiga (antara lain `animate` dan `humanizer-indonesia`, MIT) memakai lisensinya sendiri.

Dengan mengirim pull request, kamu setuju kontribusimu dilisensikan di bawah AGPL-3.0-only yang sama. Kalau membuat fork, kabari lewat issue di repositori ini. Itu hanya permintaan, bukan syarat lisensi.

## ⭐ Dukung proyek ini

Kalau SquidWings berguna, beri **bintang** di GitHub supaya lebih mudah ditemukan orang lain. Laporan bug, ide fitur, dan pull request sangat diterima lewat [Issues](https://github.com/zynxx24/SquidWings/issues).

## 🌏 In English

**SquidWings is a free, open-source AI agent (AGPL-3.0) that runs on your own machine, in the terminal or the browser.** It is an alternative to Claude Code, Cursor, Aider, and Open Interpreter that works with any OpenAI-compatible API, including OpenRouter, OpenAI, Groq, Ollama, and LM Studio. That covers Claude, GPT, Gemini, DeepSeek, Qwen, Llama, and local LLMs.

- **AI coding agent:** edits files, runs commands and tests in a bubblewrap or Docker sandbox, and checks its own work.
- **Execution harness:** result verification, loop detection, tool-argument repair, and escalation to a stronger model, so cheap models do more.
- **Multi-agent teams:** an n8n-style visual workflow canvas with schedules, Telegram bots, and databases.
- **Research and documents:** web search with sources, PDF and Office reading with local OCR, Word, Excel, PowerPoint, and PDF output.
- **Media:** YouTube and yt-dlp downloads, local Whisper transcription, video from code (HyperFrames, Remotion), and a browser motion editor.
- **Trading:** MT4/MT5 bridge, quant and neural models, LLM analyst, paper trading, and auto trade with stop loss.
- **Security:** sandboxed commands, policy engine, path jail, audit log, prompt-injection shield, and secret masking.

Runs on Windows, macOS, and Linux (`python scripts/setup.py`). Trading and the video Studio are optional add-ons, off until `python main.py addon install <name>`. Every top-level folder has its own README. The interface and docs are in Indonesian. Quick start: see the commands [above](#-mulai-cepat).

---

<sub>**Kata kunci:** AI agent open source, asisten AI Indonesia, AI agent bahasa Indonesia, alternatif Claude Code, alternatif Cursor, AI coding agent, terminal AI, CLI AI agent, chatbot AI lokal, LLM lokal, Ollama, OpenRouter, multi-agent, workflow otomatis seperti n8n, bot Telegram AI, riset web AI, baca PDF dengan AI, OCR, transkripsi Whisper, unduh YouTube, video AI, Remotion, trading bot MT4, MT5, XAUUSD, Python, FastAPI.</sub>
