# Router model

Halaman **Router & token** (`/router`, tautannya di sidebar; terbuka di dalam web utama) adalah router model bawaan: tempat menambahkan endpoint OpenAI-compatible dan Claude-compatible milikmu, menyimpan banyak API key per endpoint, memutar key dengan rotasi pintar, memilih model default, dan melihat pemakaian token. Halaman ini bekerja sama persis di komputer ini maupun lewat tunnel.

URL dan API key model tidak perlu lagi di `.env`. Saat server web pertama kali jalan, `API_BASE_URL` + `API_KEY` disalin ke halaman ini sebagai provider **Default**, dan pasangan lain seperti `BANSOS_API_BASE_URL` + `BANSOS_API_KEY` (+ `BANSOS_MODEL`) menjadi provider dengan prefix `bansos/`. Ini hanya terjadi sekali. Setelah itu, kelola semuanya dari halaman, dan nilai di `.env` boleh dihapus. Tombol **Impor dari .env** muncul bila `.env` masih punya endpoint yang belum disalin.

## Menambahkan provider

1. Buka `http://127.0.0.1:8080/router`, tekan **Tambah provider**.
2. Isi nama, jenis, dan base URL:
   - **OpenAI-compatible**: base URL yang berakhiran `/v1`, misalnya `https://api.openai.com/v1`, `https://openrouter.ai/api/v1`, atau router lokal seperti `http://127.0.0.1:20128/v1`. Request dikirim ke `POST /chat/completions`.
   - **Claude-compatible**: `https://api.anthropic.com` (atau router yang meniru API Claude). Request dikirim ke `POST /v1/messages` dengan header `x-api-key` dan `anthropic-version`.
3. Prefix (otomatis dari nama, misalnya `9r/`) menandai model provider ini di aplikasi: `9r/gpt-4.1`, `claude/claude-sonnet-4-5`. Prefix dibuang sebelum request dikirim.
   Centang **Provider default** untuk provider tanpa prefix: ia melayani semua id model yang tidak diklaim provider lain, seperti `API_BASE_URL` dulu. Hanya ada satu default; default lama otomatis diberi prefix sendiri.
4. Tempel API key, satu per baris. Tiap baris jadi satu akun.
5. Simpan. Backend langsung mengambil daftar model provider itu dan memasukkannya ke registry, jadi modelnya muncul di pemilih model chat, tim agent, dan bagian **Model** tanpa refresh. Hasilnya (jumlah model atau pesan error) tampil di banner. Hal yang sama terjadi saat menambah key, mengubah URL atau prefix, atau menekan **Ambil model**. **Tes koneksi** hanya mengecek satu key tanpa mengubah apa pun. Keduanya tidak memakai token.

Kalau endpoint tidak punya `/models`, isi **Model tambahan** dengan id model yang dipisah koma.

Agent tetap berbicara format OpenAI. Untuk provider Claude, pesan sistem, gambar, tool call, dan hasil tool diterjemahkan ke Messages API, termasuk saat streaming. Prompt sistem dan giliran terakhir diberi `cache_control`, jadi percakapan panjang terbaca sebagai token cached pada request berikutnya.

## Model

Bagian **Model** dan pemilih model di chat mengelompokkan model per provider, lalu per upstream di belakangnya. Router seperti 9router melayani banyak upstream lewat satu URL dan menamai modelnya `kg/claude-haiku-5-5`, `vk/anthropic/claude-sonnet-4.6`, atau `gemini/gemini-3.8-flash`. Segmen pertama id model, setelah prefix provider dibuang, menjadi nama grupnya:

| Provider (prefix) | Id model | Grup |
|---|---|---|
| Default (tanpa prefix) | `kg/claude-haiku-5-5` | Default · kg |
| Default (tanpa prefix) | `vk/anthropic/claude-sonnet-4.6` | Default · vk |
| Bansos (`bansos/`) | `bansos/glm-5-3-flash` | Bansos |
| Router baru, mis. 9router (`9r/`) | `9r/oc/qwen-4` | 9router · oc |
| Router baru, mis. 9router (`9r/`) | `9r/gpt-4.1` | 9router |

Tidak ada nama yang ditulis tetap di kode, jadi router baru dengan penamaan apa pun langsung terkelompok sendiri setelah modelnya diambil. Nama di depan titik tengah adalah nama provider di halaman ini; ganti namanya untuk mengganti label grupnya. Grup yang sama dipakai rotasi akun: kuota habis di `kg/*` hanya mengistirahatkan grup `kg`, bukan seluruh akun.

Semua model chat per provider tampil di sini, termasuk dari provider default. **Model default** menentukan model yang dipakai chat baru; ini menggantikan `DEFAULT_MODEL` di `.env` (yang tetap dipakai bila pilihan di halaman dikosongkan). Model yang tidak lagi didaftar provider dihapus dari registry saat model diambil ulang, dan model provider yang dihapus ikut hilang.

## Rotasi akun

| Strategi | Cara kerja |
|---|---|
| Smart round robin (default) | Lihat di bawah |
| Round robin | Bergiliran sesuai bobot |
| Isi akun pertama dulu | Akun teratas dipakai sampai sibuk, istirahat, atau gagal; baru akun berikutnya. Cocok untuk key berbayar utama dengan key gratis sebagai cadangan |

Smart round robin:

- **Istirahat otomatis.** 429 → istirahat sesuai `Retry-After`, atau 5, 10, 20 detik, dan seterusnya sampai 10 menit. Key ditolak (401/403) → 30 menit. Kuota atau saldo habis → 1 jam. Error server dua kali berturut-turut → sebentar. Request langsung dicoba di akun lain.
- **Bobot ikut kesehatan.** Porsi akun = bobot × tingkat sukses terbaru × kecepatan dibanding akun lain ÷ (1 + request yang sedang jalan). Akun lambat atau sering gagal mendapat lebih sedikit, bukan nol, supaya bisa terlihat kalau sudah pulih.
- **Sesi menempel.** Satu chat tetap memakai akun yang sama selama akun itu sehat (15 menit sejak request terakhir), karena cache prompt tersimpan per akun. Chat lain tetap tersebar.
- **Smooth weighted round robin.** Akun berbobot besar lebih sering dipilih tanpa dipilih beruntun.
- Error 400, 404, 413, dan 422 adalah kesalahan request, jadi tidak menghukum key dan tidak dicoba ulang di akun lain.
- Bila semua akun istirahat lebih dari 8 detik, router mengembalikan rate limit, dan fallback model agent pindah ke model lain.

**Batas paralel** per akun membatasi jumlah request bersamaan. Status ini disimpan di memori: restart server melupakan masa istirahat.

## Pemakaian token

Setiap request ke model dicatat, termasuk lewat `API_BASE_URL` di `.env` dan `config/providers.yaml`: provider, akun, model, input, output, cached, total, latensi, dan berhasil atau gagal. Periode: 1 jam (per 5 menit), 24 jam (per jam), 1 minggu (per 6 jam), 1 bulan (per hari), dan semua. Rincian bisa dikelompokkan per provider, akun, atau model.

- **Input** sudah termasuk token cached. Untuk Claude: input + cache read + cache write.
- **Cached** = token prompt yang dibaca dari cache provider. Hit % = cached ÷ input.
- **Total** = input + output.
- Request stream yang tidak mengirim laporan usage tetap dihitung sebagai request, tanpa token, dan jumlahnya ditampilkan. Provider OpenAI-compatible di halaman ini otomatis diminta mengirim usage saat streaming (`stream_options.include_usage`).

## Penyimpanan dan keamanan

- Provider dan API key: `web/data/router/providers.json`, izin 0600, tidak ikut git. Log token: `web/data/router/usage.db` (SQLite). Pindahkan dengan `ROUTER_STORE_DIR`. Baris lebih tua dari `ROUTER_USAGE_KEEP_DAYS` (default 400) dihapus.
- Halaman tidak pernah menerima API key utuh, hanya versi tersamar seperti `sk-dem…1111`.
- Lewat tunnel, halaman bisa dipakai sama seperti di komputer ini, termasuk menambah dan menghapus provider dan key. Karena tunnel tidak punya login, pasang **PIN** di bagian *Keamanan tunnel*: setelah itu perubahan dari luar komputer ini butuh PIN (halaman memintanya sekali per tab, lalu mengirimnya sebagai header `X-Router-Pin`). Dari komputer ini PIN tidak pernah diminta. PIN disimpan sebagai hash PBKDF2 bersalt, bukan teks aslinya. Kosongkan lalu simpan untuk menghapusnya.
- Tanpa provider di halaman dan tanpa `API_KEY` di `.env`, chat menjawab dengan petunjuk untuk membuka `/router`.

## Navigasi

Chat, Tim agent, Trading, dan Router & token ada di satu web. Tautan di bawah daftar chat (atau Alt+1 sampai Alt+4) berpindah halaman tanpa meninggalkan chat. Trading dan Router dimuat di dalam halaman utama sekali, lalu tetap hidup, jadi bolak-balik terasa instan, dan polling-nya berhenti selama halamannya tidak ditampilkan. Alamat ikut berubah (`/`, `/trading`, `/router`), jadi tombol Back dan bookmark bekerja, dan `http://127.0.0.1:8080/router` langsung membuka halaman router di dalam web utama. Ctrl+klik membuka halamannya di tab sendiri. Tombol putar di bar atas memuat ulang halaman itu saja.

Di komputer ini, panel Trading diambil dari server trading di port 80, karena di sanalah data EA MT4. `python main.py web` menjalankan server itu sendiri bila Linux mengizinkan port 80 (matikan dengan `--no-trading`). Bila belum diizinkan, panel diambil dari server web (cocok untuk backend sim atau MT5, tanpa data EA), dengan pemberitahuan kecil, bukan halaman error. Izinkan port 80 sekali dengan `sudo sysctl -w net.ipv4.ip_unprivileged_port_start=80`, lalu start ulang web.

Saat memuat, bar tipis di atas halaman berjalan, tombol yang memicu request menampilkan spinner, dan halaman yang baru dibuka menampilkan loader logo dengan cincin berputar. Request yang berulang dalam beberapa detik (polling harga dan status) tidak memunculkan bar. Semua ini ada di `web/static/js/page.js` dan `web/static/css/page.css`, dipakai ketiga halaman.

Hanya web utama di server yang sama (dan, untuk server trading, web utama di port loopback lain) yang boleh memuat halaman Trading dan Router di dalam dirinya (`frame-ancestors`); situs lain tetap ditolak.

## Tampilan

Kartu di halaman chat, trading, dan router memakai lapisan `web/static/css/soft.css`: warna tetap dari `tokens.css`, dengan garis lebih tipis, bayangan berlapis yang lembut, sudut lebih bulat, dan transisi tenang. Lapisan ini dibangun dengan Tailwind (prefix `tw-`, preflight mati, jadi CSS halaman tidak tertimpa). Setelah mengubah `web/tailwind/soft.css`:

```bash
npx tailwindcss@3.4.17 -c web/tailwind/tailwind.config.js -i web/tailwind/soft.css -o web/static/css/soft.css --minify
```
