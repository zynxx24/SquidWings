<!-- Generated from CLAUDE.md and .claude/rules/ by scripts/sync_agent_config.py. Edit those files, not this one. -->

# SquidWings: notes for Claude Code

SquidWings is an AI agent harness (terminal + web) in **one language, Python 3.12+**, that runs on Windows, macOS and Linux. The UI, prompts and docs are in **Indonesian**; keep new user-facing text in Indonesian, code and comments in English.

## How to work (always)

<!-- prompt: Plan first: before any task with more than one step (build, fix, design, video, document, settings), inspect what exists, then write a plan from every angle (goal and done-criteria, users, design and files, the six quality aspects, risks, ordered steps, verification, rollback), then execute it step by step without waiting unless only the user can decide. A one-step task or a plain question needs no plan. -->
# Rencana menyeluruh dulu, baru eksekusi

Berlaku untuk setiap tugas yang lebih dari satu langkah: membangun, memperbaiki, mendesain, membuat video atau dokumen, mengubah pengaturan.

1. **Petakan dulu.** Baca kode, file, dan konfigurasi yang akan disentuh. Jangan merencanakan dari tebakan.
2. **Tulis rencana dari segala segi**, sebelum file pertama diubah:
   - tujuan dan kriteria selesai yang bisa dicek;
   - siapa pemakainya dan alur yang mereka jalani;
   - data, arsitektur, dan file yang berubah;
   - enam aspek kualitas (lihat `quality-bar.md`) dan cara membuktikan masing-masing;
   - risiko dan kasus pinggir, beserta penangkalnya;
   - urutan langkah, verifikasi, dan cara mundur kalau gagal.
3. **Eksekusi sesuai rencana**, langkah demi langkah. Kalau temuan baru mengubah rencana, perbarui rencananya, lalu lanjut.
4. **Tutup dengan bukti:** apa yang dicek dan hasilnya, apa yang tidak dicek.

Pertanyaan sederhana yang tidak butuh tool tetap dijawab langsung, tanpa rencana. Skill `super-plan` berisi templatnya.

<!-- prompt: Every app, website, API or project must meet all six: security (server-side validation, authz per action, parameterized queries, escaped output, secrets only in env, rate limits; attack your own web app and fix root causes), code quality (project conventions, clean lint and types), reliability (honest error handling, timeouts, transactions, tests for new logic and edge cases), performance (no N+1, indexes, pagination, measured numbers), maintainability (clear structure, config apart from code, README), usability (360 px to desktop, 4.5:1 contrast, keyboard, helpful errors). Report each as PASS or FAIL with evidence and fix every FAIL before saying done. -->
# Bar kualitas aplikasi, web, dan proyek

Keenam aspek wajib terpenuhi, bukan dipilih salah satu. Kode yang jalan tapi bercelah, lambat, atau sulit dirawat belum selesai.

| Aspek | Minimal yang harus ada | Bukti |
|---|---|---|
| Keamanan | Validasi semua input di server, auth dan otorisasi per aksi, query berparameter, output di-escape, rahasia di env (tidak di kode atau log), header keamanan, rate limit di login dan form publik, dependensi tanpa CVE yang diketahui | Skill `redhat`: serang aplikasinya sendiri (OWASP Top 10), perbaiki akar masalahnya, ulangi sampai bersih |
| Kualitas kode | Mengikuti konvensi proyek, nama jelas, fungsi kecil, tanpa kode mati, tanpa debug print, tanpa except kosong | Linter dan type checker bersih, `aislop_scan` tanpa error |
| Keandalan | Error ditangani dan dilaporkan jujur (tanpa fallback diam-diam), timeout dan retry di panggilan jaringan, transaksi untuk tulisan ganda, state kosong, loading, dan error | Tes otomatis untuk logika baru dan kasus pinggir, semuanya lulus |
| Performa | Tanpa query N+1, ada indeks untuk kolom yang dicari, pagination, aset dikompres, cache di tempat yang tepat | Ukur, jangan menebak: waktu respons, ukuran bundle, Core Web Vitals |
| Maintainability | Struktur folder yang jelas, satu tanggung jawab per modul, konfigurasi terpisah dari kode, README cara menjalankan dan mengetes | Orang lain bisa menjalankannya dari README saja |
| Usability | Bekerja di layar 360 px sampai desktop, kontras minimal 4.5:1, bisa dipakai dengan keyboard, pesan error yang menjelaskan cara memperbaiki | Buka dan klik sendiri di HP dan desktop; skill `aislop` untuk tampilan |

Laporkan keenamnya sebagai PASS atau FAIL beserta buktinya. FAIL diperbaiki sebelum bilang selesai. Yang tidak bisa dicek, sebut terus terang. Skill `quality-gate` berisi langkah pemeriksaannya.

<!-- prompt: Video: write the final script first (per scene: seconds, on-screen text, narration) and check it fits (about 2.5 words a second spoken). Build the video and the voice from that one script, so the text on screen never differs from what is said; plan the music source in the script. -->
# Video: naskah final dulu, baru video

Berlaku untuk semua video, baik dibuat lewat harness maupun alat lain (HyperFrames, Remotion, Studio, animate).

1. **Tulis naskah final sebelum adegan pertama dibangun.** Satu file (`naskah.md` atau `mic.txt`) berisi per adegan: rentang detik, teks di layar, dan narasi.
2. **Teks di layar dan narasi berasal dari naskah yang sama.** Narasi membacakan teks di layar, atau teks di layar memuat potongan kata demi kata dari narasi. Tidak boleh ada versi kedua yang ditulis ulang di tengah jalan.
3. **Cek muat sebelum membuat suara:** bahasa Indonesia santai kira-kira 2,5 kata per detik. Rentang yang terlalu sempit dilebarkan di naskah, bukan dikejar dengan mempercepat suara.
4. **Bangun video dari naskah:** komposisi membaca teks dan detiknya dari file naskah, bukan dari salinan manual.
5. **Suara dibuat dari naskah yang sama**, lalu dicek dengan transkripsi (whisper) bahwa setiap baris terucap sesuai naskah.
6. Naskah berubah, berarti video dan suara dibangun ulang dari naskah, bukan ditambal satu per satu.

Skill `video-script` berisi format naskahnya. Pelajaran dari proyek sebelumnya ada di `lessons.md`.

<!-- prompt: Lessons: TTS reads every word of its prompt, so style direction goes in Director's notes; check each voice line with whisper; long jobs run in the background; quote laws from the official text. -->
# Pelajaran dari proyek sebelumnya

## Video "Hikayat Antrean" (2026-11-27)

- **Narasi ditulis setelah video jadi, jadi isinya beda dengan teks di layar.** Sejak itu berlaku aturan `video-script-first.md`.
- **Model TTS membacakan semua teks prompt**, termasuk instruksi gaya ("bacakan dengan hangat ..."). Arahan gaya harus di bagian `## Director's notes`, di atas `## Transcript:`. Itu format AI Studio yang dipakai `brain/utils/gemini_tts.py`.
- **Model TTS kadang mengucapkan satu baris dua kali.** Setiap baris dicek dengan whisper sebelum dicampur.
- **Kuota gratis Gemini TTS dihitung per key dan per model** (sekitar 10 permintaan per hari). Pakai router di `gemini_tts.py` dengan beberapa key. Jangan menunggu `retryDelay` harian (bisa belasan jam).
- **Lyria (musik Gemini) tertutup di paket gratis** (limit 0). Katalog musik HeyGen perlu login. Rencanakan sumber musik sejak awal: lagu milik pengguna, katalog, atau sintesis lokal.
- **Jeda antar-kalimat terasa kosong tanpa backsound.** Video bernarasi direncanakan dengan musik yang mengecil (ducking) saat narasi bicara.
- **Pekerjaan panjang (render, batch TTS) dijalankan di background.** Jangan memblokir sesi dengan loop menunggu.
- **Konten hukum:** kutip pasal dari teks resmi (JDIH, peraturan.go.id), tulis apa adanya, dan beri tanda `[...]` untuk bagian yang dipotong.

## Agent settings: `.claude/` and `.codex/`

| Path | For | What |
|---|---|---|
| `CLAUDE.md` + `.claude/rules/*.md` | both | The rules. Edit these; they are the one source. |
| `.claude/settings.json` | Claude Code | Shared permissions: the check commands are allowed, reading `.env`, credentials and router keys is denied. Personal overrides go in `.claude/settings.local.json` (git-ignored). |
| `.claude/skills/` | Claude Code | `super-plan`, `quality-gate`, `video-script` |
| `.claude/agents/` | Claude Code | `quality-reviewer`: independent six-aspect review of a diff |
| `.codex/config.toml` | Codex | Reasoning effort and room for `AGENTS.md` |
| `AGENTS.md`, `.codex/skills/` | Codex | **Generated**: run `.venv/bin/python scripts/sync_agent_config.py` after editing the rules or skills. A test fails when they drift. |

The harness has the same rules for the models it runs: `brain/system_prompt/` (plan first, six-aspect bar) and the built-in skills `bar-kualitas`, `video-audio` and `voice-over`.

## Run it

```bash
python scripts/setup.py                 # .venv + requirements (uses uv when present)
./run.sh                                # web UI, no venv activation (Windows: run.bat); args go to main.py
.venv/bin/python main.py web            # http://127.0.0.1:8080  (Windows: .venv\Scripts\python)
.venv/bin/python main.py chat           # terminal chat
.venv/bin/python -m pytest -q           # offline test suite, ~3 min
bash scripts/check_all.sh               # ruff, mypy, compile, pytest, import
```

Pass `--no-update` to `main.py` while developing, or it fast-forwards from GitHub on start. Do not run long live-model sessions to test a change: the suite is offline on purpose.

## Layout (each folder has its own README.md)

| Folder | What |
|---|---|
| `brain/` | The brain and harness. `agent/` (orchestrator, agent loop, sessions, teams), `harness/` (autopilot, tool and skill selection, tiers, verifier, guards, relevance ranking, model errors), `classifier/`, `memory/`, `model/` (provider client, registry, router), `system_prompt/`, `config/` (settings, paths, add-on switch), `utils/` (cache, platform_compat, workspace, ...), `security/` (sandbox, policy) |
| `input/` | What reaches the model: context assembly, token limits, think prompts, design styles |
| `skills/` | `builtin/` own skills, `sources.json` GitHub skills (cloned to `.sources/`, never edited), `adapters/` our adjustments |
| `tools/` | Every AI tool, one folder per segment; `toolbox/` holds the hidden on-demand tools |
| `web/` | FastAPI server and chat page |
| `addons/` | Optional, **off by default**: `trading/`, `studio/` (`python main.py addon install <name>`) |
| `data/` | Runtime output (logs, audit chain, reports), git-ignored |

## Rules that keep it working

- **Model modes.** A model the user picks (`model_locked=True`) is used as it is; when it fails, `brain/harness/model_errors.py` explains why and the turn ends. Never add a fallback, advisor or recovery model for a locked pick. **Auto** goes through `brain/harness/autopilot.py` (`ROUTING_QUALITY=smart`): cheapest reliable model for the tier, one tier up after a bad turn.
- **Limits on cost, not capability** (2026-11-28, replaces "no limits"). History may fill the provider's window (1M tokens, `DEFAULT_CONTEXT_WINDOW` for unreported models). One tool result is clipped to 120k characters (head and tail; the model re-reads a range), a turn has a token budget (`AGENT_TURN_TOKEN_BUDGET`, default 3M; `off` lifts it), and guides over 12k characters arrive as an outline (`load_skill(name, section=...)`). Do not cap what the agent can do.
- **Scale.** Skills and tools will keep growing. Rank with `brain/harness/relevance.py` (IDF-weighted triggers, BM25) so the most relevant come first; hidden toolbox tools stay on demand (`find_tools`).
- **Caches** go through `brain/utils/cache.py` (named, bounded, TTL, `clear_session` on chat delete, stats at `/api/cache`). Anything built from files uses `FileWatch` so edits apply without a restart. No new unbounded module-level dicts.
- **Per-chat isolation.** State, caches and installed kits are keyed by the session (`get_active_session()`); a chat never sees another chat's data.
- **Security is not optional.** Shell commands go through `brain/security/command_risk.py` (parsed, not regex: recursive deletes outside the work folder, piping fetched text into a shell, key material, root shells are refused; `sudo <one command>` is allowed). `terminal_run` is guarded like `execute_command`; the guard fails closed. Remote web access needs the access PIN (`brain/security/access_gate.py`); this machine never does. Outside content taints the chat (`brain/security/taint.py`): sudo, fetched installers and `git push` then wait for the user's yes.
- **Stay in sync.** The checkout fast-forwards to GitHub by itself (`AUTO_UPDATE`, default `apply`; `notify` only reports) and installs changed packages held to `requirements.lock`. GitHub skills follow upstream daily too, each source pinned to the commit in use in `skills/sources.json`, every move logged (`python main.py skills history`); `SKILL_SOURCES_AUTO_APPROVE=0` holds them for `skills approve`.
- **Runtime data lives outside the checkout** (`brain/config/paths.py` `MOVABLE` → `~/.local/share/squidwings`, links left behind). New runtime folders join `MOVABLE` and `.gitignore`.
- **Uploads and shared files are one path everywhere.** Web, terminal (`/upload`) and Telegram go through `brain/utils/intake.py` (ZIPs checked and scanned by `brain/security/file_scan.py`, skills installed by `skills/installer.py` into the user data folder). HTML the agent shares opens as a sandboxed app (`brain/utils/app_links.py`, `/a/<token>/`). Telegram's default chat is the harness flow, made once by `ensure_telegram_harness()`; the owner's on/off on the Tim agent page is final.
- **Cross-platform.** POSIX-only calls (`termios`, `pty`, `os.killpg`, `preexec_fn`, `os.nice`) go through `brain/utils/platform_compat.py`. Runtime files go through `brain/config/paths.py`, never the repo root.
- **GitHub skills stay original.** Adjust one with `skills/adapters/<name>.md`, not by editing `skills/.sources/`.
- **Add-ons** register their tools, skills and pages only when switched on (`brain/config/edition.py`). New optional features become an `addons/<name>/` with an `addon.json`.
- **Tests** must stay offline (`tests/conftest.py` blocks the network and points data at temp folders). Add a test for new logic.
- **Never commit** `.env`, `data/`, `workspace/`, `vault/`, chat stores, or anything in `.gitignore`.
- **Web turns** run as tasks independent of the WebSocket (`web/live_turns.py`): a dropped page never cancels an answer; only `{"type": "stop"}` does.
- **Changelog:** note every user-visible change in `docs/CHANGELOG.md`.
- Writing: no em dashes in user-facing text; plain words over marketing tone.
