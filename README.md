# SquidWings

Asisten AI berbasis Python dengan terminal interaktif, Web GUI, routing multi-model,
memory berlapis, dan tool lokal (baca/tulis file, eksekusi command, web search)
yang dibungkus Security Layer.

---

## Daftar Isi

- [Fitur](#fitur)
- [Kebutuhan](#kebutuhan)
- [Instalasi](#instalasi)
- [Konfigurasi](#konfigurasi)
- [Cara Menjalankan](#cara-menjalankan)
- [Perintah CLI](#perintah-cli)
- [Workspace Sesi](#workspace-sesi)
- [Memory](#memory)
- [Upload dan Gambar](#upload-dan-gambar)
- [Ponytail](#ponytail)
- [Security Layer](#security-layer)
- [Menjalankan Test](#menjalankan-test)
- [Struktur Proyek](#struktur-proyek)
- [Troubleshooting](#troubleshooting)
- [Lisensi](#lisensi)

---

## Fitur

| Fitur | Keterangan |
|---|---|
| Terminal interaktif | Mode `chat` dengan riwayat, render Markdown, dan workspace per sesi |
| Web GUI | FastAPI + WebSocket, opsional diekspos lewat Cloudflare Tunnel |
| Multi-model routing | Registry model, classifier tugas, fallback, rate limiter |
| Tool lokal | Baca/edit file (backup + diff), jalankan command (disanitasi), discard perubahan |
| Web search | Pencarian internet dengan cache, hasil bisa disimpan ke Obsidian |
| Memory berlapis | Riwayat chat, ringkasan riwayat, memory vault Obsidian, Graphify |
| Upload dokumen dan gambar | Dokumen diubah ke Markdown lewat MarkItDown; gambar dikirim ke model vision |
| Ponytail | Gaya jawaban ringkas dengan level yang bisa diatur lewat env |
| Security Layer | Policy engine, path jail, secrets vault, audit hash-chain, prompt shield, redactor |

---

## Kebutuhan

- Python 3.12 atau lebih baru (target `py312`)
- Endpoint API yang kompatibel dengan OpenAI Chat Completions
- Linux atau macOS (Windows via PowerShell belum diuji secara penuh)
- Opsional: `cloudflared` untuk tunnel, `ffmpeg` untuk video draft

Di sebagian sistem perintahnya `python3`, bukan `python`.

---

## Instalasi

```bash
git clone https://github.com/zynxx24/SquidWings.git
cd SquidWings
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Isi `API_BASE_URL` dan `API_KEY` di `.env`.

---

## Konfigurasi

Semua pengaturan ada di `.env`. Contoh lengkap dengan komentar ada di
[.env.example](.env.example). Tulis komentar di baris sendiri, jangan di baris yang sama dengan nilai.

| Kelompok | Variabel utama |
|---|---|
| API | `API_BASE_URL`, `API_KEY`, `TIMEOUT_SECONDS`, `MAX_RETRIES` |
| Model | `DEFAULT_MODEL`, `DEFAULT_TEMPERATURE`, `DEFAULT_MAX_TOKENS` |
| Web | `WEB_HOST`, `WEB_PORT`, `WEB_PUBLIC_ACCESS`, `WEB_AUTH_TOKEN` |
| Workspace | `SQUIDWINGS_WORKSPACE` |
| Ponytail | `PONYTAIL_MODE`, `PONYTAIL_TASKS` |
| Ringkasan riwayat | `HISTORY_SUMMARY_CHARS`, `HISTORY_SUMMARY_SHARE` |
| Obsidian | `WEB_SEARCH_OBSIDIAN_ENABLED`, `OBSIDIAN_VAULT_PATH`, `OBSIDIAN_SEARCH_FOLDER` |
| Graphify | `GRAPHIFY_MEMORY_ENABLED`, `GRAPHIFY_MEMORY_ENDPOINT`, `GRAPHIFY_MEMORY_TIMEOUT_SECONDS` |
| Web search | `WEB_SEARCH_ENABLED`, `WEB_SEARCH_MAX_RESULTS`, `WEB_SEARCH_CACHE_TTL_SECONDS` |
| Upload | `MAX_UPLOAD_MB`, `MAX_UPLOAD_CHARS`, `MAX_CHAT_IMAGES`, `IMAGE_MAX_SIDE`, `VIDEO_UPLOAD_ENABLED` |
| Keamanan | `SQUIDWINGS_AUDIT_LOG`, `SQUIDWINGS_VAULT`, `SQUIDWINGS_VAULT_PASSPHRASE` |

---

## Cara Menjalankan

```bash
python main.py chat             # chat interaktif di terminal
python main.py ask "pertanyaan" # satu pertanyaan
python main.py models           # daftar model
python main.py fetch            # ambil daftar model dari router
python main.py health           # cek kesehatan API
python main.py web              # Web GUI di http://127.0.0.1:8080
python main.py web --port 9000 --host 127.0.0.1
python main.py web --tunnel     # buka akses publik lewat Cloudflare Tunnel
```

`python cli.py <perintah>` setara dengan `python main.py <perintah>`.

Mode tunnel juga tersedia lewat skrip: `./tunnel.sh start [port]`.
Bila akses publik dinyalakan, isi `WEB_AUTH_TOKEN`.

---

## Perintah CLI

Perintah slash di dalam `chat`:

| Perintah | Fungsi |
|---|---|
| `/model` | Pilih model |
| `/tree [nama]`, `/tree all` | Lihat atau pindah workspace, atau lihat root proyek |
| `/ws`, `/workspace` | Daftar, buat, atau tutup workspace (`/ws none`) |
| `/view`, `/create` | Lihat atau buat file |
| `/search` | Cari teks di workspace |
| `/code` | Minimize, expand, atau copy blok kode |
| `/format`, `/theme`, `/md` | Tampilan terminal |
| `/status` | Info sesi |
| `/help` | Daftar perintah lengkap |

---

## Workspace Sesi

CLI dan Web memakai tata letak yang sama: `workspace/<session_id>/`.

- Di web chat, file kode yang diminta user disimpan di `workspace/<session_id>/`.
- Nama folder sesi dibersihkan sebelum dipakai.
- Folder dasar bisa diganti lewat `SQUIDWINGS_WORKSPACE`.
- Catatan: `execute_command` di web chat masih berjalan dari root proyek.

---

## Memory

- Riwayat chat: bila tidak muat anggaran token, bagian lama diringkas secara ekstraktif (tanpa LLM)
  dan ditaruh di awal riwayat. Atur dengan `HISTORY_SUMMARY_CHARS` (0 untuk mematikan)
  dan `HISTORY_SUMMARY_SHARE`.
- Memory vault Obsidian: ringkasan dari folder `vault/` masuk ke system prompt di CLI dan Web.
  Hasilnya di-cache berdasarkan waktu modifikasi vault. Di web, blok ini ditaruh setelah prefix
  yang di-cache agar cache tidak rusak.
- Hasil web search bisa disimpan ke vault (`WEB_SEARCH_OBSIDIAN_ENABLED`, `OBSIDIAN_VAULT_PATH`).
- Graphify: aktifkan dengan `GRAPHIFY_MEMORY_ENABLED=true` dan isi `GRAPHIFY_MEMORY_ENDPOINT`.
  Keluaran graph ada di `graphify-out/` dan `vault/graphify-out/`.

Struktur vault:

```text
vault/
  00-inbox  10-sessions  20-concepts  30-decisions  40-people-projects  90-meta
```

---

## Upload dan Gambar

- Dokumen (PDF, DOCX, PPTX, XLSX, dan sejenisnya) diubah ke Markdown lewat MarkItDown
  di `POST /api/upload`. Batas: `MAX_UPLOAD_MB` dan `MAX_UPLOAD_CHARS`.
- Gambar diperkecil hingga sisi terpanjang `IMAGE_MAX_SIDE` dan dikirim sebagai `image_url`,
  maksimal `MAX_CHAT_IMAGES` per pesan. Bila model tidak mendukung vision, gambar dibuang
  dengan peringatan di log.
- Audio tidak didukung. Upload audio ditolak dengan 415.
- Video masih draft. Aktifkan dengan `VIDEO_UPLOAD_ENABLED=true`. Butuh ffmpeg dan transkripsi.

---

## Ponytail

Gaya jawaban ringkas, diatur lewat env:

- `PONYTAIL_MODE`: `off`, `lite`, `full` (bawaan), atau `ultra`.
- `PONYTAIL_TASKS`: jenis tugas yang kena, misalnya `code,reason`.
- Teks tiap level ada di `brain/system_prompt/ponytail*.txt` dan bisa diedit langsung.

Perintah `/ponytail-review` dan `/ponytail-audit` belum ada.

---

## Security Layer

Kode ada di `security/`, kebijakan di `security/policy.yaml`.

| Komponen | Fungsi |
|---|---|
| `policy_engine.py` | Aturan izin tool |
| `path_jail.py` | Membatasi akses file ke workspace |
| `secrets_vault.py` | Penyimpanan rahasia terenkripsi |
| `audit_chain.py` | Log audit hash-chain (`audit.jsonl`) |
| `prompt_shield.py` | Penyaring prompt injection |
| `redactor.py` | Menyamarkan rahasia di log dan keluaran |
| `rate_guard.py`, `web_access.py`, `headers.py` | Pembatasan laju dan akses web |

---

## Menjalankan Test

```bash
python -m pytest
python -m pytest -m "not live"   # lewati tes yang memanggil API asli
ruff check .
```

Terakhir dijalankan: 208 tes lolos. Alur gambar dan workspace sesi di web belum punya tes ujung ke ujung.

---

## Struktur Proyek

```text
main.py, cli.py     entry point CLI dan Web
agent/              orchestrator, agent loop, session manager
brain/              classifier, context assembler, memory, token budget, system prompt
model/              API client, registry, parser, stream
tool/               tool lokal: edit file, command, upload, web search, memory vault
security/           policy, path jail, vault, audit, shield
web/                server FastAPI dan file statis
config/             settings, konstanta, models_config.yaml
utils/              logger, helper, workspace context, tunnel
vault/              memory Obsidian
workspace/          hasil kerja per sesi
tests/              test suite
```

Dokumen internal (catatan desain, rencana, review) disimpan di `.internal/`.
Folder itu diabaikan Git, jadi hanya `README.md` dan `LICENSE` yang tampil di repo.

---

## Troubleshooting

| Gejala | Penyebab dan solusi |
|---|---|
| `ModuleNotFoundError` saat tes | Jalankan `python -m pytest` dari root proyek ini |
| Koneksi ditolak | Periksa `API_BASE_URL` dan `API_KEY`, lalu `python main.py health` |
| Upload audio gagal (415) | Audio memang tidak didukung |
| Video ditolak | Set `VIDEO_UPLOAD_ENABLED=true` dan pasang ffmpeg |
| Gambar tidak terbaca model | Model tidak mendukung vision. Pilih model vision |
| Nilai env tidak terbaca | Pastikan tidak ada komentar di baris yang sama dengan nilai |
| Web tidak bisa diakses dari luar | Gunakan `--tunnel` dan isi `WEB_AUTH_TOKEN` |

---

## Lisensi

SquidWings adalah perangkat lunak berbayar (proprietary). Penggunaan memerlukan
lisensi yang dibeli. Lihat [LICENSE](LICENSE) untuk syarat lengkap.
