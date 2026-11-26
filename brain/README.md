# brain/

Otak dan harness SquidWings dalam satu folder: memahami permintaan, memilih model, menyusun prompt, memanggil tool, memeriksa hasil, mengingat, dan fondasi yang dipakai semuanya.

| Folder | Isi |
|---|---|
| `agent/` | Menjalankan percakapan: `orchestrator.py` (satu giliran dari awal sampai akhir), `agent_loop.py` (model → tool → hasil), sesi per chat, tim agent (`team_flow.py` aturan node dan alur, `team_orchestrator.py` menjalankan alur persis mengikuti sambungannya) |
| `harness/` | Kecerdasan di sekitar model: autopilot Auto, pesan error model, pemilihan skill dan tool, ranking relevansi, tier, verifier, guard. Lihat [harness/README.md](harness/README.md) |
| `classifier/` | Jenis tugas dan tingkat kesulitan. Hasil LLM classifier di-cache, jadi pesan yang sama tidak memanggil model dua kali |
| `memory/` | Riwayat chat, memori proyek, ringkasan, recall antar chat (data di `memory/chat_store/`, `memory/store/`, tidak ikut Git) |
| `model/` | Koneksi provider: API client, registry model, router (rotasi akun, harga, log token), streaming |
| `system_prompt/` | Prompt sistem per jenis tugas dan per tier, cadangan Ponytail |
| `config/` | `settings.py` (`.env`), `paths.py` (lokasi `data/`), `edition.py` (saklar add-on), kredensial, `models_config.yaml`, `providers.yaml` |
| `utils/` | `cache.py` (semua cache), `platform_compat.py` (Windows/macOS/Linux), workspace per chat, logger, CLI, terminal web, OCR, suara, `datastore/` (sumber data, Tabel data bawaan, relasi antar tabel, operasi node Tabel data), `file_history.py` (file yang diubah AI per giliran: diff dan kembalikan), `agent_reach.py` (Agent Reach: YouTube, Reddit, X, GitHub dan lainnya; dipasang, diperbarui, dan diatur dari harness), `gemini_tts.py` dan `voice_script.py` (voice-over: Gemini TTS, naskah bertimestamp jadi trek suara yang pas dengan adegan) |
| `security/` | Sandbox perintah, `policy.yaml`, path jail, audit chain, prompt shield, akses web |

## Dua mode model

| Mode | Perilaku |
|---|---|
| **Pilih sendiri** | Model yang dipilih di UI (`/model` di terminal) selalu dipakai, dioptimalkan sesuai tier-nya. Kalau gagal, chat menjelaskan sebabnya (key ditolak, saldo habis, rate limit, model tidak ada, konteks kepanjangan, timeout, server error) dan **tidak** pindah ke model lain |
| **Auto** | `harness/autopilot.py` memilih model termurah yang andal untuk tier yang dibutuhkan, naik satu tier setelah giliran yang gagal, dan alasannya tampil di chat |

## Cache

Semua cache lewat `utils/cache.py`: punya nama, ukuran maksimal, waktu kedaluwarsa, statistik hit/miss (`GET /api/cache`), dan dibersihkan per chat saat chat dihapus. Cache yang dibangun dari file (skill, adapter, katalog) memakai `FileWatch`, jadi mengedit file skill langsung berlaku tanpa restart.

| Cache | Isi |
|---|---|
| `classifier.llm` | Klasifikasi LLM per teks permintaan (1 jam) |
| `auto.escalation` | Tier tambahan per chat setelah giliran Auto yang gagal |
| `web.pages` | Halaman web yang sudah dibaca, per chat (30 menit) |
| `toolbox.index` | Indeks BM25 katalog tool |
