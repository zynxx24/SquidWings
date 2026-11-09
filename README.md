# SquidWings

Agent AI multi-model untuk terminal dan web. Ia bisa diajak ngobrol, membantu tugas sekolah, membaca dokumen, dan mengerjakan kode sungguhan: mengubah file, menjalankan perintah di sandbox, dan membaca web. Lapisan pengawasnya dibuat supaya model murah bekerja mendekati model mahal.

## Mulai cepat

```bash
git clone https://github.com/zynxx24/SquidWings.git
cd SquidWings
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # opsional: provider dan API key bisa diatur di http://127.0.0.1:8080/router
sudo apt install bubblewrap
python main.py doctor       # memastikan sandbox benar-benar mengisolasi
python main.py web          # buka http://127.0.0.1:8080
```

## Dokumentasi

| Panduan | Isi |
|---|---|
| [Menghubungkan provider](#menghubungkan-provider) | Memakai endpoint mana pun dengan URL dan API key |
| [Mengunggah dokumen](#mengunggah-dokumen) | Cara kerja PDF dari guru dan file lain |
| [Tim agent](docs/tim-agent.md) | Kanvas alur, jadwal, Telegram, dan database |
| [Panel trading](docs/trading.md) | MT4, chart, prosedur praktik, dan backtest |
| [Router model](docs/router.md) | Atur provider, URL, dan API key dari web atau tunnel; rotasi pintar, model default, dan pemakaian token |
| [Cara kerja agent](docs/cara-kerja-agent.md) | Harness, tier model, dan skill |

## Fitur

| Fitur | Keterangan |
|---|---|
| Terminal dan Web UI | Satu orchestrator yang sama, tanpa login |
| Teman ngobrol dan tugas | Mendengarkan saat curhat, menjawab tugas dengan bahasa wajar, dan ingat isi chat |
| Baca dokumen | Teks dan gambar dari PDF, Office, EPUB, ZIP, dan scan, lalu agent menentukan langkahnya |
| Provider bebas | Cukup URL dan API key dari endpoint yang kompatibel dengan OpenAI. Daftar model diambil otomatis, dan routing belajar dari hasilnya |
| Execution harness | Verifikasi hasil, deteksi loop, perbaikan argumen tool, dan eskalasi ke model lebih kuat |
| Toolbox | 124 tool tepat (hitung, tanggal, satuan, tabel, uang, terbilang, peta, cuaca, kurs, Word/PPT/Excel/PDF) yang tersembunyi sampai dibutuhkan, lewat kata kunci, skill, atau `find_tools` |
| Buat dokumen | Word, PowerPoint, Excel, CSV, dan PDF yang rapi, langsung muncul di chat untuk diunduh |
| Google Maps | Impor hasil scrape, saring dan rangking tempat, jarak, tempat terdekat dari OpenStreetMap, dan ekspor ke Excel atau GeoJSON |
| Tim agent | Kanvas alur seperti n8n: delay, gate OR, jadwal, output Telegram, dan pilihan database |
| Riset web | Mencari, membuka beberapa halaman, membaca isinya, dan mencantumkan sumber |
| Satu chat, satu set | Folder, memori, vault Obsidian, graph kode, skor aislop, dan mode Ponytail sendiri |
| Sandbox | Perintah agent berjalan terisolasi lewat bubblewrap atau Docker |
| Keamanan | Policy engine, path jail, audit log, prompt shield, dan penyamaran rahasia |
| Panel trading | Chart, tiket order, usulan kuant, dan backtest di `/trading`, lewat MT5, EA MT4, atau pasar simulasi |

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

Keduanya diperbarui otomatis sekali sehari saat server web menyala. Atur `TOOLS_AUTO_UPDATE=false` untuk mematikannya, karena pembaruan ini mengubah instalasi global.

Chrome atau Chromium dipakai untuk membaca situs yang butuh JavaScript. `cloudflared` dipakai untuk `--tunnel`.

## Menjalankan

```bash
python main.py chat                     # terminal, dengan pilihan folder proyek
python main.py chat --project nama      # langsung ke workspace/projects/nama
python main.py ask "pertanyaan"         # satu pertanyaan
python main.py web                      # Web UI di http://127.0.0.1:8080
python main.py trading                  # backend trading saja, di port 80
python main.py models                   # daftar model
python main.py health                   # cek koneksi API
```

Perintah di dalam `chat`: `/model`, `/stack`, `/ponytail`, `/memory`, `/sandbox`, `/status`, dan `/help` untuk daftar lengkap.

> **Tidak ada login.** Web UI hanya menerima akses dari mesin ini secara bawaan. Dengan `--tunnel`, siapa pun yang punya URL-nya bisa memakai agent dan membaca semua chat.

## Menghubungkan provider

SquidWings bisa memakai endpoint apa pun yang kompatibel dengan OpenAI Chat Completions. Isi dua baris ini di `.env`:

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

Nama model yang berisi `flash`, `mini`, atau `lite` dianggap murah dan dipakai untuk tugas mudah. Nama seperti `opus` dipakai untuk tugas sulit. Bila namanya tidak memberi petunjuk, semua model dipakai bergiliran. Untuk memaksa satu model, isi `DEFAULT_MODEL`, atau tulis di `routing` pada `config/models_config.yaml`.

Bila endpoint tidak melaporkan batasnya, agent memakai perkiraan aman (konteks 32 ribu token, output 4 ribu token) dan menganggap model mendukung tool. Batas yang tepat bisa ditulis di bagian `models` pada file yang sama. Untuk memakai beberapa endpoint sekaligus, lihat `config/providers.yaml`.

## Mengunggah dokumen

Tekan ikon klip di kotak chat, pilih file, lalu kirim. Pesan boleh dikosongkan.

Semua isi file diekstrak apa adanya, tanpa ditebak atau disaring, lalu agent sendiri yang membaca maksudnya. Pesanmu dan awal dokumen yang menentukan model dan skill, bukan seluruh isi file.

| File | Teks | Gambar |
|---|---|---|
| PDF | Baris per baris, dengan tabel di tempatnya | Gambar di tiap halaman. Halaman tanpa teks (scan) jadi satu gambar utuh |
| Word, PowerPoint, Excel, EPUB, ZIP | Lewat MarkItDown | Semua gambar di dalam file |
| OpenDocument (`.odt`, `.odp`, `.ods`) | Dari isi dokumennya | Semua gambar di dalam file |
| Teks dan kode | Apa adanya | Tidak ada |

Teks lengkap masuk ke `uploads/` di folder chat, dan gambarnya ke `uploads/<nama>_img/`, jadi agent bisa membukanya lagi tanpa unggah ulang. Di dalam teks, gambar muncul sebagai tautan di tempatnya, dan tampil di chat kalau agent menuliskannya. Gambar di bawah 48 piksel dilewati karena biasanya ikon.

Dokumen di atas `UPLOAD_INLINE_CHARS` dikirim dengan semua judulnya, dan agent membaca sisanya saat perlu.

Gambar dan halaman scan dibaca oleh OCR lokal (`utils/local_ocr.py`), dan teksnya ditaruh tepat setelah tautan gambarnya. OCR ini memakai RapidOCR di CPU, tanpa model AI luar, tanpa internet, dan tanpa `sudo`. Kecepatannya sekitar 3 detik per halaman. Satu file dibaca paling banyak `OCR_MAX_IMAGES` gambar. Gambar yang tidak sempat terbaca ditandai di teks. OCR hanya membaca tulisan: isi foto dan diagram tidak dijelaskan, dan judul huruf besar bisa kehilangan spasi. Gambar tidak dikirim ke model, kecuali `SEND_IMAGES=true`.

Plugin `markitdown-ocr` tidak dipakai, karena ia butuh model vision dan `pymupdf` yang berlisensi AGPL.

Untuk file yang sudah ada di folder, tool `extract_files` mengekstrak satu file atau semua dokumen sekaligus ke folder `extracted/`. File yang tidak berubah tidak diproses ulang. `read_file` juga membaca dokumen dan menyimpan gambarnya di samping file.

Gambar di folder chat bisa tampil di Web UI dengan path relatif, misalnya `![grafik](plot.png)`. Hanya PNG, JPG, GIF, WebP, dan BMP sampai 10 MB, dan hanya dari folder chat itu sendiri. SVG tidak ditampilkan. Saat agent membaca gambar dengan `read_file`, ia mendapat ukuran, format, dan Markdown untuk menampilkannya, bukan isi gambarnya. Gambar di dalam PDF atau file Office tidak diekstrak. Supaya model bisa melihat gambar, unggah gambarnya ke chat dan pilih model yang mendukung vision.

## Konfigurasi

Semua pengaturan ada di `.env`, dengan contoh lengkap di [.env.example](.env.example). Tulis komentar di baris sendiri, jangan di baris yang sama dengan nilai.

| Kelompok | Variabel utama |
|---|---|
| API | `API_BASE_URL`, `API_KEY`, `TIMEOUT_SECONDS`, `MAX_RETRIES` |
| Model | `DEFAULT_MODEL` (boleh kosong), `DEFAULT_TEMPERATURE`, `DEFAULT_MAX_TOKENS`, `MODEL_SYNC_EVERY_S` |
| Web UI | `WEB_HOST`, `WEB_PORT`, `WEB_ALLOWED_HOSTS`, `WEB_ALLOWED_ORIGINS`, `WEB_TEAMS_ENABLED` |
| Sandbox | `SANDBOX_NETWORK`, `SANDBOX_RO_PATHS` |
| Riset web | `SEARXNG_URL`, `BRAVE_SEARCH_API_KEY`, `WEB_BROWSER_ENABLED` |
| Memori | `HISTORY_SUMMARY_CHARS`, `HISTORY_MAX_TOKENS`, `OBSIDIAN_VAULT_PATH` |
| Ponytail | `PONYTAIL_MODE`, `PONYTAIL_TASKS` |
| Tim agent | `TEAM_RUN_SECONDS`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TEAM_DB_POSTGRES_URL`, `TEAM_DB_MYSQL_URL`, `TEAM_DB_MONGODB_URL` |
| Upload | `MAX_UPLOAD_MB`, `MAX_UPLOAD_CHARS`, `UPLOAD_INLINE_CHARS`, `DOCUMENT_OCR`, `OCR_MAX_IMAGES`, `MAX_CHAT_IMAGES`, `IMAGE_MAX_SIDE` |
| Alat lokal | `GRAPHIFY_AUTO`, `AISLOP_AUTO`, `TOOLS_AUTO_UPDATE` |
| Trading | `MT5_TRADE_TOKEN`, `TRADING_REMOTE_NO_TOKEN`, `QUANT_BLOCK_HOURS_UTC`, `QUANT_MIN_SCORE`, `MT5_MAX_VOLUME`, `MT5_REQUIRE_SL`, `MT5_ALLOW_LIVE`, `MT4_BRIDGE_SECRET`, `QUANT_RISK_PER_TRADE`, `TRADE_NEWS_FILE` |

### Pilihan model

Pada mode Auto, router tidak lagi selalu memakai model yang sama:

- Kandidat diambil dari `config/models_config.yaml` dan dari daftar model yang sedang dikembalikan API. Model baru dari provider ikut dicoba tanpa mengubah konfigurasi.
- Tugas mudah memakai model murah seperti DeepSeek Flash dan GLM, dikenali dari namanya. Tugas sedang memakai semua model kecuali tier frontier. Tugas sulit tetap memakai model yang dikonfigurasi.
- Tiap pilihan melihat catatan sukses, gagal, dan latensi model, ditambah sedikit acak dan bonus untuk model yang lama tidak dipakai. Model yang sering gagal jadi jarang terpilih, dan model baru tetap kebagian giliran.

Router menilai keandalan dan kecepatan, bukan mutu jawaban. Catatannya ada di `.agent_memory/model_stats.json` di folder proyek, berlaku untuk semua chat, dan hanya berisi angka per model, tanpa isi chat.

Daftar model diambil ulang tiap 30 menit. Ubah lewat `MODEL_SYNC_EVERY_S`, atau isi `0` untuk mematikannya.

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

Memori proyek juga menyimpan catatan per giliran: apa yang diminta, intinya, tool yang dipakai, dan file yang diubah. Agent menerimanya sebagai blok `[EARLIER IN THIS CHAT]` tanpa memanggil model. Giliran baru yang masih ada di riwayat hanya dicatat aksinya, giliran lama dapat satu baris, dan yang relevan dengan pertanyaan didahulukan. Ukurannya dibatasi sekitar 1.600 karakter, sepanjang apa pun chatnya.

Profil pengguna dan playbook berlaku di semua chat, dan disimpan di `vault/`.

Semua data ini diabaikan Git dan tidak ikut terbit. Menghapus chat di web juga menghapus foldernya.

## Sandbox

`execute_command`, `run_checks`, dan tes dari verifikasi berjalan terisolasi. Agent hanya bisa menulis ke folder kerjanya, dan internet mati secara bawaan. Atur `SANDBOX_NETWORK=true` agar `pip install` dan `npm install` bisa jalan. `python main.py doctor` mencoba keluar dari sandbox dan melaporkan hasilnya.

## Struktur proyek

```text
main.py, cli.py   entry point
agent/            orchestrator, agent loop, harness, tim agent
brain/            classifier, konteks, memori, prompt, skill
model/            API client, provider, registry, stream
tool/             tool lokal, web, memori, dan kode
security/         sandbox, policy, path jail, audit, shield
web/              server FastAPI, Web UI, dan API trading
trading/          adapter MT5, EA MT4, jurnal, dan model kuant
knowledge/        impor hasil Google Maps ke vault dan dataset tempat
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
| Gambar atau PDF scan tidak terbaca | Periksa `DOCUMENT_OCR=true`, lalu kirim scan yang lebih tajam |
| Nilai env tidak terbaca | Pindahkan komentar ke baris sendiri |
| Menu tim tidak muncul | Atur `WEB_TEAMS_ENABLED=true` |
| Backend `mt5` gagal login | Paket `MetaTrader5` hanya jalan di Windows. Di Linux pakai `mt4` atau `sim` |
| EA MT4 tampil offline | Masukkan URL server ke daftar WebRequest di MT4, lalu cek secret di preset EA |
| Perubahan kode trading tidak terasa | Restart server. Hanya file statis yang langsung terbaca |

## Lisensi dan pembelian

SquidWings adalah perangkat lunak **berbayar (proprietary)** di bawah SquidWings Commercial License. Kode yang terlihat di repositori ini tidak otomatis boleh dipakai, disalin, atau dijual: hak pakai diperoleh dengan membeli lisensi. Syarat lengkap ada di [LICENSE](LICENSE), dan komponen pihak ketiga di [NOTICE](NOTICE).

Ringkasan (bukan pengganti teks lisensi):

- **Pakai** sesuai jumlah pengguna, instalasi, dan jangka waktu yang dibeli.
- **Jual beli** lisensi hanya oleh pemilik hak cipta atau penjual resmi yang ditunjuk tertulis. Pembeli tidak boleh menjual kembali, menyewakan, membagikan, atau mensublisensikan tanpa izin tertulis.
- **Tidak boleh** membagikan kode sumber, menghapus pemberitahuan hak cipta, atau menawarkan SquidWings sebagai layanan untuk pihak lain tanpa izin.
- Disediakan **tanpa garansi**. Fitur trading tidak menjamin keuntungan.
- Komponen pihak ketiga (antara lain skill `animate` dan `humanizer-indonesia`, lisensi MIT) tetap memakai lisensinya sendiri.

Pembelian lisensi, perpanjangan, atau izin khusus: **aguswiraw60@gmail.com**.
