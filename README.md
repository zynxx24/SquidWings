<div align="center">

# 🦑 SquidWings

**Agent AI multi-model untuk terminal dan web, dengan harness yang membuat model murah bekerja mendekati model mahal.**

Ngobrol, mengerjakan tugas, membaca dokumen, meriset web, dan mengerjakan kode sungguhan:
mengubah file, menjalankan perintah di sandbox, dan memeriksa hasilnya sendiri.

![Python](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS-555)
![API](https://img.shields.io/badge/API-OpenAI--compatible-10a37f)
![Sandbox](https://img.shields.io/badge/sandbox-bubblewrap%20%7C%20Docker-orange)
![License](https://img.shields.io/badge/license-Commercial-critical)

[Mulai cepat](#-mulai-cepat) · [Edisi](#-edisi) · [Fitur](#-fitur) · [Struktur file](#-struktur-file) · [Pengaturan](#%EF%B8%8F-pengaturan) · [Pengembangan](#-pengembangan) · [Lisensi](#-lisensi-dan-pembelian)

</div>

---

## 🚀 Mulai cepat

```bash
git clone https://github.com/zynxx24/SquidWings.git
cd SquidWings
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # opsional: provider dan API key bisa diatur di http://127.0.0.1:8080/router
sudo apt install bubblewrap # sandbox untuk perintah agent
python main.py doctor       # memastikan sandbox benar-benar mengisolasi
python main.py web          # buka http://127.0.0.1:8080
```

> [!IMPORTANT]
> **Tidak ada login.** Web UI hanya menerima akses dari mesin ini secara bawaan. Dengan `--tunnel`, siapa pun yang punya URL-nya bisa memakai agent dan membaca semua chat.

## 🧩 Edisi

Repositori ini berisi **SquidWings Community**: harness, chat, tim agent, toolbox, router model, dan keamanan, lengkap. Modul trading adalah bagian **SquidWings Enterprise** dan tidak ada di repositori ini, termasuk di riwayat commit-nya.

| | Community (repo ini) | Enterprise |
|---|:---:|:---:|
| Chat terminal dan web, tim agent, toolbox 127 tool | ✅ | ✅ |
| Execution harness, router model, sandbox, keamanan | ✅ | ✅ |
| Panel trading `/trading`, chart, dan tiket order | — | ✅ |
| Jembatan EA MT4 dan adapter MT5 | — | ✅ |
| Model kuant, otak neural, LLM analyst, simulasi live | — | ✅ |
| Auto trade dengan stop loss dan trailing stop | — | ✅ |
| Tool `trading_snapshot` dan skill `analis-trading` | — | ✅ |

Cara kerjanya ada di `config/edition.py`. Bila file modul Enterprise terpasang di tempatnya, server, CLI, registry tool, dan skill menyalakannya sendiri. Bila tidak ada, menu Trading tersembunyi, `/trading` menjelaskan edisinya, dan `python main.py trading` berhenti dengan pesan yang jelas, bukan error import. `ENTERPRISE_TRADING=0` mematikan modul itu walau terpasang.

Lisensi Enterprise: lihat [Lisensi dan pembelian](#-lisensi-dan-pembelian).

## ✨ Fitur

| Fitur | Keterangan |
|---|---|
| 💬 Terminal dan Web UI | Satu orchestrator yang sama untuk keduanya |
| 🫶 Teman ngobrol dan tugas | Mendengarkan saat curhat, menjawab tugas dengan bahasa wajar, dan ingat isi chat |
| 📄 Baca dokumen | Teks dan gambar dari PDF, Office, EPUB, ZIP, dan scan, lalu agent menentukan langkahnya |
| 🔌 Provider bebas | Cukup URL dan API key dari endpoint yang kompatibel dengan OpenAI. Daftar model diambil otomatis |
| 🧠 Execution harness | Verifikasi hasil, deteksi loop, perbaikan argumen tool, dan eskalasi ke model lebih kuat |
| 🧰 Toolbox | 127 tool tepat (hitung, tanggal, satuan, tabel, uang, peta, cuaca, kurs, kalkulator risiko, Office, PDF, voice-over ElevenLabs) yang tersembunyi sampai dibutuhkan |
| 📑 Buat dokumen | Word, PowerPoint, Excel, CSV, dan PDF yang rapi, langsung muncul di chat untuk diunduh |
| 🗺️ Google Maps | Impor hasil scrape, saring dan rangking tempat, jarak, dan ekspor ke Excel atau GeoJSON |
| 👥 Tim agent | Kanvas alur seperti n8n: delay, gate OR, jadwal, Telegram, dan database |
| 🔎 Riset web | Mencari, membuka beberapa halaman, membaca isinya, dan mencantumkan sumber |
| 🗂️ Satu chat, satu set | Folder, memori, vault Obsidian, graph kode, skor aislop, dan mode Ponytail sendiri |
| 🛡️ Sandbox dan keamanan | Perintah agent terisolasi, policy engine, path jail, audit log, prompt shield, penyamaran rahasia |
| 🔀 Router model | Provider, URL, dan API key dari web; rotasi pintar, model default, dan pemakaian token |

## 📁 Struktur file

```text
SquidWings/
├── main.py                  entry point: chat, ask, web, models, health, doctor
├── cli.py                   antarmuka terminal
├── .env.example             contoh semua pengaturan (salin ke .env)
├── pyproject.toml           konfigurasi ruff, mypy, dan pytest
├── requirements.txt         dependensi (requirements.lock untuk versi tepat)
│
├── agent/                   otak kerja agent
│   ├── orchestrator.py      menerima pesan, memilih model, menjalankan giliran
│   ├── agent_loop.py        loop model → tool → hasil
│   ├── harness/             verifier, guard loop, pemilih tool, tier model, advisor
│   └── team_*.py            tim agent: alur, eksekusi, dan output
│
├── brain/                   pemahaman dan memori
│   ├── classifier/          jenis tugas dan lanjutan percakapan
│   ├── memory/              riwayat chat, memori proyek, ringkasan
│   ├── system_prompt/       prompt sistem per jenis tugas
│   └── skills/              panduan kerja (fix-bug, build-web, google-maps, …)
│
├── model/                   koneksi ke model
│   ├── connection/          API client dan retry
│   ├── registry/            daftar model dan batasnya
│   ├── router/              provider, rotasi, sinkron daftar model, harga, pemakaian
│   └── stream/              streaming jawaban
│
├── tool/                    semua tool agent
│   ├── register_tools.py    tool inti yang selalu tersedia
│   ├── registry/            skema dan registry tool
│   └── execute/             implementasi: file, perintah, web, memori, kode, toolbox
│
├── security/                sandbox, policy.yaml, path jail, audit chain, prompt shield
├── config/                  settings, edition, kredensial, model dan provider
├── web/                     server FastAPI, API tim dan router, file statis Web UI
├── knowledge/               impor hasil Google Maps ke vault
├── utils/                   logger, tampilan CLI, workspace, port, tunnel, OCR
├── docs/                    panduan lengkap
├── scripts/check_all.sh     ruff, mypy, compile, pytest, dan import sekaligus
└── tests/                   test suite offline
```

Folder yang dibuat saat jalan dan **tidak** ikut Git:

| Folder | Isi |
|---|---|
| `workspace/sessions/<id>/` | Folder kerja tiap chat web dan team run |
| `workspace/projects/<nama>/` | Proyek dari `python main.py chat` |
| `vault/` | Profil pengguna dan playbook, berlaku di semua chat |
| `web/data/` | Provider router, kredensial dari web, dan log token |
| `.agent_memory/` | Statistik model untuk router |

## ⚙️ Pengaturan

Pengaturan ada di tiga tempat. Yang di web langsung berlaku tanpa restart.

| Tempat | Untuk apa |
|---|---|
| `.env` | Semua pengaturan dasar. Contoh lengkap di [.env.example](.env.example). Tulis komentar di baris sendiri |
| Web → **Router & token** | Provider, URL, API key, model default, dan rotasi |
| Web → **Tim agent → Kredensial** | Token Telegram, Brave/SearXNG, dan URL database |
| `config/models_config.yaml` | Batas konteks per model dan aturan routing tetap |
| `config/providers.yaml` | Beberapa endpoint sekaligus |
| `security/policy.yaml` | Aturan apa yang boleh dan tidak boleh dilakukan agent |

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
| Edisi | `ENTERPRISE_TRADING` (`0` mematikan modul Enterprise) |

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

Nama model yang berisi `flash`, `mini`, atau `lite` dianggap murah dan dipakai untuk tugas mudah. Nama seperti `opus` dipakai untuk tugas sulit. Untuk memaksa satu model, isi `DEFAULT_MODEL`, atau tulis di `routing` pada `config/models_config.yaml`. Bila endpoint tidak melaporkan batasnya, agent memakai perkiraan aman: konteks 32 ribu token dan output 4 ribu token.

</details>

<details>
<summary><b>Pilihan model pada mode Auto</b></summary>

- Kandidat diambil dari `config/models_config.yaml` dan dari daftar model yang sedang dikembalikan API. Model baru dari provider ikut dicoba tanpa mengubah konfigurasi.
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
```

Perintah di dalam `chat`: `/model`, `/stack`, `/ponytail`, `/memory`, `/chats`, `/brief`, `/voice`, `/sandbox`, `/status`, dan `/help`.

**Suara.** `/voice` merekam lewat mikrofon (perlu `arecord` atau `parecord`), mengubahnya jadi teks, lalu menampilkan hasilnya: Enter untuk kirim, atau ketik perbaikan. Pengenal suara memakai `faster-whisper` bila terpasang (lokal), kalau tidak `SpeechRecognition`, yang mengirim audio ke layanan online Google. `/voice on` membacakan jawaban dengan ElevenLabs (butuh API key dan `ffplay` atau `mpv`); `/voice status` menunjukkan apa yang sudah siap. Di Web UI ada tombol mikrofon di kotak pesan dan tombol *Bacakan* di bawah jawaban; keduanya memakai fitur suara browser, jadi tidak butuh key, dan pesan tidak pernah terkirim sendiri.

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

## 🧪 Pengembangan

```bash
bash scripts/check_all.sh        # ruff, mypy, compile, pytest, import
python -m pytest -m "not live"   # lewati tes yang memanggil API asli
python -m tests.routing_eval     # akurasi routing: jenis tugas, skill, dan tool per prompt
```

Kasus routing ada di `tests/eval_data/*.json`; menambah prompt tidak perlu menyentuh kode. Kasus bertanda `"gap": true` adalah kekurangan yang sudah diketahui: tesnya gagal-yang-diharapkan, jadi saat diperbaiki tes memintamu menghapus tandanya.

Tes bertanda `enterprise` dilewati sendiri bila modul Enterprise tidak terpasang. Jangan pernah meng-commit `.env`: key disimpan di sana, dan `.env.example` hanya berisi contoh.

## 🩺 Troubleshooting

| Gejala | Solusi |
|---|---|
| Perintah agent ditolak | Pasang bubblewrap atau Docker, lalu jalankan `python main.py doctor` |
| Koneksi ditolak | Periksa `API_BASE_URL` dan `API_KEY`, lalu jalankan `python main.py health` |
| Hasil riset kurang lengkap | Pasang SearXNG atau isi `BRAVE_SEARCH_API_KEY` |
| Gambar atau PDF scan tidak terbaca | Periksa `DOCUMENT_OCR=true` dan `pip install -r requirements.txt` (rapidocr). `OCR_TIER=max` untuk scan sangat sulit |
| Nilai env tidak terbaca | Pindahkan komentar ke baris sendiri |
| Menu tim tidak muncul | Atur `WEB_TEAMS_ENABLED=true` |
| Menu Trading tidak ada | Itu fitur Enterprise. Edisi Community memang tidak memuatnya |
| Port 8080 sudah terpakai | `web` pindah sendiri ke port kosong berikutnya. Untuk SquidWings lama, pakai `--replace` |

## 📜 Lisensi dan pembelian

SquidWings adalah perangkat lunak **berbayar (proprietary)** di bawah SquidWings Commercial License. Kode yang terlihat di repositori ini tidak otomatis boleh dipakai, disalin, atau dijual: hak pakai Enterprise diperoleh dengan membeli lisensi. Syarat lengkap ada di [LICENSE](LICENSE), dan komponen pihak ketiga di [NOTICE](NOTICE).

- **Pakai** sesuai jumlah pengguna, instalasi, dan jangka waktu yang dibeli.
- **Jual beli** lisensi hanya oleh pemilik hak cipta atau penjual resmi yang ditunjuk tertulis.
- **Tidak boleh** membagikan kode sumber, menghapus pemberitahuan hak cipta, atau menawarkan SquidWings sebagai layanan tanpa izin.
- **Enterprise** (modul trading) dilisensikan terpisah. Fitur trading tidak menjamin keuntungan.
- Disediakan **tanpa garansi**. Komponen pihak ketiga (antara lain `animate` dan `humanizer-indonesia`, MIT) memakai lisensinya sendiri.

Pembelian lisensi Enterprise, perpanjangan, dan izin khusus: **aguswiraw60@gmail.com**.
