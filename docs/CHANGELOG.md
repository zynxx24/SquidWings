# Catatan perubahan

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
