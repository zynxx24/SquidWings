# Catatan perubahan

## 2026-11-26 (sore): pesan Telegram rapi, jendela web di Telegram, run langsung terlihat di web

- **Run dari Telegram dan jadwal tampil di web.** Editor yang sedang membuka sebuah alur sekarang ikut menampilkan setiap run alur itu, apa pun pemicunya: node menyala satu per satu, hasilnya muncul, dan baris status menyebut asalnya ("dari Telegram (nama pengirim)", "dari jadwal"). Sebelumnya kanvas hanya bergerak untuk run yang dimulai dari tombol Jalankan alur, jadi pesan Telegram terlihat seperti tidak berefek padahal sudah dijalankan dan dibalas.
- **Pesan Telegram yang rapi.** Mode "Rapi otomatis" (bawaan) mengubah Markdown jadi huruf tebal, miring, daftar berpoin, kutipan, dan kode. Tabel dibuat muat di layar HP. Data JSON, misalnya hasil Tabel data, jadi daftar singkat dengan angka yang mudah dibaca (75.000). Kalau Telegram menolak format, pesan dikirim ulang sebagai teks biasa, jadi tidak ada yang hilang. Mode "Teks biasa" tetap ada.
- **Isi pesan sendiri**: node Telegram punya kotak Isi pesan dengan `{{input}}`, `{{json.kolom}}`, `{{date}}` dan lainnya, plus pratinjau berbentuk gelembung chat di editor tanpa mengirim apa pun. Bisa juga dikirim tanpa bunyi notifikasi.
- **Tombol dan jendela web.** Tombol "Buka jendela web" membuka isi lengkap sebagai halaman di dalam Telegram: tabel untuk data, teks rapi untuk jawaban, mengikuti tema terang atau gelap Telegram. Tombol "Tautan" membuka alamat apa pun (boleh pakai `{{json.kode}}`). Pesan yang terlalu panjang otomatis dikirim sebagai ringkasan dengan tombol "Buka lengkap".
- **Alamat publik untuk jendela web**: isi "Alamat web publik" di Kredensial > Telegram bila punya domain sendiri. Kalau kosong, dipakai tunnel web server (`--tunnel`), atau dibuat otomatis lewat Cloudflare Tunnel milik server kecil yang hanya berisi halaman hasil, jadi aplikasi lainnya tidak ikut terbuka ke internet. Halaman memakai tautan acak dan dihapus setelah 60 hari.

## 2026-11-26: Tim agent dirombak seperti n8n

- **Yang tersambung itulah yang jalan.** Tidak ada lagi Lead dan Reviewer tersembunyi atau garis putus-putus otomatis. Pemicu menjalankan node yang ditarik garis darinya, tiap node menerima isi dari garis yang masuk, dan node yang tidak tersambung tidak dijalankan (tampil pudar di kanvas). Hasil akhir adalah output langkah terakhir.
- **Alur lama tetap jalan.** Alur yang dibuat dengan model Lead/Reviewer berjalan seperti biasa sampai dibuka dan disimpan lagi. Saat dibuka, sambungannya ditampilkan sebagai garis biasa: Lead menerima hasil semua agent, Reviewer menjadi agent sesudahnya yang memperbaiki jawaban sendiri.
- **Halaman depan alur kerja**: daftar alur dengan pencarian, saklar Aktif (jadwal dan pemicu Telegram bisa diistirahatkan tanpa kehilangan pengaturan), duplikat, ekspor dan impor JSON, hapus, plus kartu template. Tab Tabel data dan Kredensial pindah ke halaman ini.
- **Node Tabel data** (pengganti "simpan ke database" yang hanya menulis log): pilih sumber dan tabel dari daftar, atau buat tabel baru langsung dari node. Operasi Ambil, Tambah, Ubah, Upsert, Hapus dengan syarat (AND/OR), urutan, batas baris, dan pemetaan kolom. Nilai bisa memakai `{{json.kolom}}` dari input. Hasilnya JSON, jadi node berikutnya bisa membaca kolomnya. Isian yang tidak ada di input ditolak dengan pesan jelas, tidak ditulis mentah ke tabel.
- **Relasi antar tabel**: tandai kolom yang menunjuk ke tabel lain (misalnya `pesanan.kode_produk` ke `produk.kode`), juga lintas sumber. Node Tabel data bisa ikut mengambil baris yang berelasi (`{{json.produk.nama}}`), dan agent melihat relasinya lewat `db_schema`. Diatur dari tombol Relasi di tab Tabel data atau dari node.
- **Tabel data bawaan**: tabel baru tanpa perlu membuat file atau mengisi URL database, disimpan di SQLite milik aplikasi.
- **Tes langkah**: jalankan satu node saja dengan input yang terlihat di panel kiri, tanpa menjalankan seluruh alur. Panel input menampilkan kolom JSON yang bisa diklik untuk dimasukkan ke kotak yang sedang aktif. Output JSON tampil sebagai tabel atau JSON.
- **Kanvas**: node berbentuk kotak dengan nama di bawahnya, garis bisa diklik untuk menyisipkan node atau memutusnya, menghapus node di tengah menyambung ulang garisnya, urungkan dan ulangi (Ctrl+Z, Ctrl+Shift+Z), salin, tempel, duplikat (Ctrl+C, Ctrl+V, Ctrl+D), klik sekali memilih dan klik lagi membuka node. Tanda merah menunjukkan node yang belum lengkap, dan daftar "perlu diisi" di atas membawa langsung ke node itu. Posisi node disimpan di server, jadi sama di HP dan laptop.
- **Batas dihapus**: tidak ada lagi batas 12 node dan 6 agent per alur. Alur boleh tanpa agent, misalnya hanya Tabel data dan Telegram. Syarat IF sampai 20, jam harian sampai 48.
- Garis di kanvas sebelumnya tidak bisa diklik karena tertutup lapisan node; sudah diperbaiki.

## 2026-11-26: video hasil ekspor bisa ditonton, unduh otomatis

- **Perbaikan**: video, audio dan gambar yang dibagikan di chat (termasuk hasil ekspor Studio) gagal diputar saat dibuka di tab sendiri. Header `sandbox` membuat Chrome menolak pemutarnya memuat file itu sendiri. Media sekarang dikirim dengan kebijakan ketat tanpa `sandbox`; file lain (HTML dan sebagainya) tetap `sandbox` dan diunduh.
- **Unduh otomatis**: ekspor yang dimulai dari Studio langsung mengunduh MP4-nya begitu selesai. Dialog ekspor juga memberi tautan Tonton dan Unduh. `?download=1` di tautan file mana pun memaksa unduh.

## 2026-11-26: keyframe sendiri, template gerak, filter

- **Atur keyframe sendiri**: bagian "Keyframe" di panel Properti menampilkan semua keyframe lapisan (waktu, nilai, gerak) dan bisa diedit langsung. Tombol Haluskan semua, Pelan awal-akhir, Linear.
- **Gerak "Halus"**: gerak mengalir melewati semua keyframe tanpa berhenti di setiap titik (kurva Catmull-Rom), sama di pratinjau dan ekspor.
- **Jalur gerak di kanvas**: lapisan yang bergerak menampilkan jalurnya dengan titik per keyframe posisi; titiknya bisa diseret.
- **Template gerak**: Muncul naik, Pop, Geser dari kiri, Jatuh memantul, Ketik / gambar garis, Zoom pelan, Melayang, Denyut, Goyang, Keluar pudar, Hapus semua gerak. Dihitung dari posisi dan waktu lapisan itu, lalu tetap bisa diatur keyframe demi keyframe.
- **Filter** dengan pratinjau kecil dari frame saat ini: Hangat, Dingin, Sinematik, Vintage, Senja, Cerah, Pastel, Pudar, Dramatis, Hitam putih, Noir. Bisa untuk seluruh video (dengan vinyet) atau per lapisan, dengan slider kekuatan; angkanya tetap bisa disetel sendiri. Properti warna baru: Pudar.

## 2026-11-25 (malam): jalankan tanpa venv

- `./run.sh` (Linux, macOS) dan `run.bat` (Windows) menjalankan SquidWings tanpa mengaktifkan venv. Tanpa argumen membuka web; `./run.sh chat`, `./run.sh addon list` dan perintah lain diteruskan ke `main.py`. Kalau `.venv` belum ada, setup dijalankan dulu.

## 2026-11-25 (malam): Studio tahap 2, efek dan editor yang lebih lengkap

- **Transisi** per lapisan, masuk dan keluar: pudar, geser (4 arah), zoom, blur, sapu (4 arah), lingkaran. Durasi dan gerak bisa diatur; batang di timeline menunjukkan panjang transisinya. Silang antar klip = pudar keluar ditumpuk pudar masuk.
- **Warna**: kecerahan, kontras, saturasi, rona, suhu warna. Semuanya bisa diberi keyframe.
- **Chroma key** (green screen) dengan kemiripan, kehalusan tepi, dan pembuang pantulan warna.
- **LUT .cube** (color grading 3D) dengan kekuatan 0 sampai 1. Unggah lewat tombol "Unggah .cube".
- **Mask** kotak atau elips dengan tepi lembut dan opsi balik, ikut bergerak bersama lapisannya.
- **Editor kurva easing**: pilih keyframe, seret dua titik kurva (cubic-bezier). Gerak baru: ambil ancang-ancang, memantul jatuh.
- **Track**: jalur di timeline untuk beberapa klip sekaligus, bisa disembunyikan, dikunci, atau dibisukan bersama. Batang yang digeser menempel ke tepi klip lain di track yang sama dan ke playhead (Alt untuk lepas).
- **Proxy media**: klip video besar (lebih dari 960 px atau 40 MB) otomatis dibuatkan salinan kecil saat diunggah, supaya editor lancar di HP. Tombol Proxy di bawah pratinjau. Ekspor tetap memakai file asli.
- **Sinkron push**: editor tidak lagi polling tiap 2,5 detik. Satu aliran Server-Sent Events (`/api/studio/{sesi}/events`) mengirim simpanan AI, progres render, dan progres proxy begitu terjadi. Setelah HP bangun, browser menyambung lagi sendiri.
- Semua efek dihitung dengan rumus yang sama di pratinjau (`render.js`) dan engine C (`sqengine.c`). Tes membandingkan keduanya lewat node: warna selisih maksimal 2 dari 255, posisi kurang dari 0,6 piksel. Build Windows (dijalankan lewat Wine) menghasilkan piksel yang sama persis dengan Linux.
- Agent tahu format barunya (skill `studio`, protokol Studio, ringkasan efek per lapisan di konteks).

## 2026-11-25 (sore): Studio video dengan engine C

- **Render di server dengan engine C** (`addons/studio/engine/sqengine.c`): rasterizer anti-alias sendiri, teks, path SVG, stroke, blur, gambar, klip video, mixer audio. Aturannya sama dengan pratinjau editor. Ekspor tidak lagi di browser: HP boleh tidur, render tetap jalan, editor yang dibuka lagi menampilkan progresnya; bisa dibatalkan.
- **Kontrol sumber daya**: `STUDIO_THREADS` (thread), `STUDIO_MEM_MB` (batas memori, thread dikurangi supaya muat), `STUDIO_NICE` (prioritas rendah), `STUDIO_GPU` (auto/off/nama encoder). GPU dipilih lewat tes encode sungguhan: NVENC, Quick Sync, VAAPI, VideoToolbox, AMF; gagal di tengah jalan pindah ke CPU.
- **Lintas OS tanpa repot**: engine dikompilasi saat `addon install studio` dengan cc/gcc/clang/MSVC atau `ziglang` dari pip (tanpa admin, juga Windows). Diuji: Linux, build Windows dijalankan lewat Wine, build macOS arm64/x86_64.
- **Caption otomatis** dari voice-over atau klip video (Whisper, waktu per kata): tool `studio_captions`.
- **Agent bisa melihat hasilnya** tanpa Chrome: `studio_still` memakai engine.
- **Potong di playhead** (tombol Potong, tombol S) di editor.
- Batas 300 lapisan / 600 keyframe / 2 MB dokumen dilonggarkan jadi batas pengaman saja.

## 2026-11-25: rombak struktur, otak/harness, model, web di HP

### Struktur folder
- Kode dibagi per segmen dan setiap folder punya `README.md`: `brain/` (otak + harness: `agent/`, `harness/`, `classifier/`, `memory/`, `model/`, `system_prompt/`, `config/`, `utils/`, `security/`), `input/`, `skills/`, `tools/`, `web/`, `addons/`, `scripts/`, `tests/`, `docs/`.
- Dulu terpisah: `agent/`, `core/` (config, utils, security), `model/`, `tool/`, `trading/`, `knowledge/`, `config/`, `utils/`, `security/`. Semua import sudah diganti.
- Data saat jalan pindah ke `data/` (log, `audit.jsonl`, laporan, arsip). File lama di root dipindah otomatis saat start (`brain/config/paths.py`); nama yang bentrok diberi nomor, tidak ditimpa.
- `cli.py` dihapus (cukup `main.py`), `tunnel.sh` ke `scripts/`, config pyright/pyrefly masuk `pyproject.toml`.
- `CLAUDE.md` di-publish: konteks proyek untuk siapa pun yang fork lalu membuka Claude Code.

### Add-on
- Trading dan Studio jadi add-on di `addons/`, **mati bawaan**. `python main.py addon list | install | enable | remove`. Tiap add-on punya `addon.json` (file, paket, program, langkah manual per OS) dan README.

### Skill
- Skill asli dari GitHub tidak diubah; penyesuaian kita di `skills/adapters/<nama>.md`. Baru: Ponytail (DietrichGebert/ponytail), Obsidian (kepano/obsidian-skills), graphify (safishamsi/graphify), Google Maps (googlemaps/agent-skills). Google Maps dan graphify tetap jalan walau repo belum ter-clone.
- Skill desain digilir antar chat (design-ui, ui-ux-pro-max, impeccable, accessibility) supaya tidak itu-itu saja.
- Skor trigger berbobot IDF, frasa tumpang tindih dihitung sekali, pencarian skill dan `find_tools` pakai BM25 (`brain/harness/relevance.py`).
- `skills/__init__.py` dipecah: `loader.py`, `selection.py`, `design.py`, `runtime.py`. File skill yang diedit langsung berlaku tanpa restart.

### Model
- **Pilih sendiri:** model yang dipilih dikunci. Kalau error, alasannya ditampilkan (key, saldo, rate limit, model tidak ada, konteks, timeout, server) dan tidak diganti model lain (`brain/harness/model_errors.py`).
- **Auto:** autopilot hemat token (`brain/harness/autopilot.py`, `ROUTING_QUALITY=smart`): tier dari tingkat kesulitan, model termurah yang andal, naik satu tier setelah giliran gagal. Alasan pilihan tampil di chat dan terminal. `.env`: `ROUTING_QUALITY` dari `max` ke `smart`.

### Konteks dan cache
- Tanpa batas buatan: model yang tidak menyebut ukuran konteks dianggap 1 juta token (`DEFAULT_CONTEXT_WINDOW`), riwayat dan jumlah pesan tidak dipotong harness (`.env`: `HISTORY_MAX_TOKENS=0`), indeks skill memuat semua skill dengan yang paling relevan di atas.
- Cache terpusat `brain/utils/cache.py` (batas ukuran, kedaluwarsa, statistik `GET /api/cache`, bersih per chat). Hasil klasifikasi LLM di-cache supaya pesan yang sama tidak memanggil model dua kali. Prompt `.txt` yang diedit langsung berlaku.

### Web dan terminal
- **Web di HP:** jawaban tidak berhenti lagi saat layar mati atau pindah aplikasi. Jawaban jalan di server sampai selesai dan tersimpan; halaman menyambung lagi sendiri dan melanjutkan tampilan dari awal (`web/live_turns.py`). Hanya tombol stop yang menghentikan.
- Selector model: "Auto (hemat)" atau model terkunci (🔒). Alasan Auto tampil di langkah jawaban.
- Terminal: `/model auto`, `/model <id>`, `/cache [clear]`; baris rute menampilkan 🔒 atau alasan Auto.

### Lintas OS
- Satu bahasa (Python) untuk Windows, macOS, Linux: `brain/utils/platform_compat.py`, panel Terminal Windows lewat `pywinpty`, `python scripts/setup.py`.
