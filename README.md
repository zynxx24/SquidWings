# SquidWings

Agent AI multi-model untuk terminal dan web. Agent ini mengerjakan tugas sungguhan: membaca dan mengubah kode, menjalankan perintah di sandbox, membaca web, dan mengingat konteks per chat. Lapisan pengawasnya dibuat supaya model murah bekerja mendekati model mahal.

## Fitur

| Fitur | Keterangan |
|---|---|
| Terminal dan Web UI | Satu orchestrator yang sama, tanpa login |
| Multi-model | Routing, fallback, dan banyak provider lewat `config/providers.yaml` |
| Execution harness | Verifikasi hasil, deteksi loop, perbaikan argumen tool, dan eskalasi ke model lebih kuat |
| Tim agent | Kanvas multi-agent dengan dependensi antar-agent dan reviewer |
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

Panduan kerja ada di `brain/skills/`: `fix-bug`, `add-feature`, `clean-code`, `web-research`, `design-ui`, `build-web`, dan `build-backend`.

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
