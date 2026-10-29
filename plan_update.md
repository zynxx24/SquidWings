# SquidWings — Complex Feature Plan

**Status:** Planning only; belum mengubah runtime code.  
**Dokumen terkait:** `update-plan.md` tetap menjadi rencana hygiene/refactor Tahap 1. Dokumen ini memuat rencana fitur berikutnya.

## 0. Tujuan

Rencana ini mencakup:

1. Tool `web_search` tanpa API key, dengan akses provider yang transparan dan patuh.
2. Persistent search memory agar query identik tidak dicari berulang kali.
3. Integrasi ledger WorkspaceMemory, sink Obsidian opsional, dan sink Graphify opsional.
4. Pergantian model di tengah chat tanpa membuat session baru, menggandakan transcript, atau menjalankan ulang side effect tool.

## 1. Kesimpulan desain

### Transcript satu per session, bukan per model

Source saat ini sudah memiliki fondasi yang cocok: `ConversationStore` menyimpan message per `session_id`; `Message` menyimpan `model_used`, `tool_calls`, `tool_call_id`; `Orchestrator.process()` menerima `override_model`; UI memiliki model selection; dan context assembler membaca history session.

Saat model diganti:

- pertahankan `session_id` dan transcript yang sama;
- jangan membuat message baru dari history lama;
- jangan menambahkan user turn lama untuk kedua kalinya;
- request berikutnya memakai model terpilih;
- setiap assistant response tetap menyimpan `model_used` untuk provenance;
- simpan `active_model` terpisah dari model yang menghasilkan response terakhir.

Secara teknis transcript memang perlu dikirim lagi sebagai context karena model stateless. Ini bukan duplikasi chat selama tidak dibuat message/side-effect baru. Context harus dirakit ulang mengikuti system prompt, context window, tool capability, dan format model aktif. “Model saja yang beda” adalah sasaran UX, bukan jaminan bahwa semua model memiliki tokenizer dan perilaku identik.

### Pisahkan cache

| Jenis | Tujuan | Kebijakan |
|---|---|---|
| Transcript cache | Menjaga percakapan | Persisten per session |
| Context assembly cache | Menghindari assembly identik | Invalid saat transcript/model/prompt/schema berubah |
| Search cache | Menghindari request web berulang | Persistent, TTL dan provider-aware |
| Response cache | Mengembalikan jawaban lama | Tidak aktif default; stale/context risk terlalu tinggi |

`web_search` adalah tool, bukan model baru. Reasoning dilakukan model aktif; hasil internet menjadi evidence berlabel sumber/waktu dan selalu dianggap untrusted data.

## 2. Bukti dan asumsi

### Bukti dari source

- `ConversationStore` menyimpan JSON per session di `brain/memory/chat_store/`.
- Message telah memiliki `model_used`, `tool_calls`, dan `tool_call_id`.
- `web/server.py` memiliki `_override_models`, endpoint model selection, dan endpoint load history.
- `Orchestrator.process()` menerima model override per request.
- `WorkspaceMemory` menyimpan `.agent_memory/state.json` dan memiliki `model_history`.
- `register_all_tools()` menjadi boundary registrasi tool.
- `GraphifyClient` membuktikan endpoint chat completion saja; endpoint graph/memory belum terverifikasi.
- Tests membatasi akses jaringan; search provider harus diuji memakai mock.

### Asumsi yang perlu diverifikasi

- Provider publik tanpa API key dapat mengubah format atau menolak automated access.
- Graphify memory endpoint belum diketahui; jangan mengasumsikan URL/API.
- Obsidian vault bisa tidak tersedia atau tidak writable.
- Model berbeda dapat memiliki context window dan tool calling yang berbeda.
- Session lama harus tetap bisa dibaca setelah penambahan metadata.
- Model dapat diganti saat request berjalan, sehingga concurrency perlu aturan eksplisit.

### Prinsip anti-bias

Internet bukan kebenaran otomatis. Cache hit bukan jaminan relevansi. Obsidian bukan cache database. Sink eksternal yang gagal tidak boleh menggagalkan chat. Tidak boleh digunakan browser spoofing, rotasi IP/proxy untuk menghindari blokir, CAPTCHA bypass, atau anti-bot evasion.

## 3. Arsitektur target

```text
UI: model selection + WebSocket session_id/request_id
  -> server: persist active model, accept idempotent turn
  -> Orchestrator: load one transcript, resolve model, assemble context
  -> AgentLoop -> ToolRegistry -> web_search
       -> provider adapter
       -> rate limit/backoff + single-flight
       -> persistent search cache
       -> WorkspaceMemory event ledger
       -> optional Obsidian sink
       -> optional Graphify sink
```

| State | Pemilik | Persistensi |
|---|---|---|
| Transcript | `ConversationStore` | JSON session |
| Active model | session metadata | JSON session; memory mirror opsional |
| Search cache | `WebSearchMemory` | workspace `.agent_memory/web_search_cache.json` |
| Event ledger | `WebSearchMemory` | `.agent_memory/web_search_events.jsonl` |
| Project facts | `WorkspaceMemory` | `.agent_memory/state.json` |
| Human-readable search notes | `ObsidianSink` | Markdown vault |
| Remote graph event | `GraphifyMemorySink` | Graphify, jika endpoint dikonfigurasi |
| In-flight guard | coordinator | process-local |

## 4. Model switch tanpa pengulangan

### Session metadata

Target metadata baru:

```json
{
  "session_id": "sess-...",
  "active_model": "kg/model-b",
  "last_response_model": "ag/model-a",
  "transcript_revision": 12,
  "last_request_id": "req-...",
  "updated_at": 0
}
```

`model_used` lama tetap dibaca sebagai fallback/backward compatibility (maknanya setara last response model). `active_model` menjadi sumber pilihan model baru.

### API dan persistence

Perbarui `POST /api/model/select` agar memvalidasi model dengan registry/allowlist, menyimpan active model pada metadata session, serta mengembalikan previous/active model dan revision. Endpoint ini tidak boleh menambah chat message atau memanggil model. `_override_models` boleh dipakai sebagai fast cache, tetapi bukan sumber kebenaran tunggal; restart harus memulihkan pilihan dari session file.

Operasi yang direncanakan pada ConversationStore: update/get active model, transcript revision, request record, dan atomic `commit_turn`. Field baru optional ketika load file lama.

### Idempotensi per turn

Setiap UI turn menerima `request_id`. Status: `accepted`, `running`, `committed`, `failed`, `cancelled`.

- Duplicate request yang sedang/selesai diproses tidak boleh menambahkan user turn kedua.
- Request committed tidak boleh memanggil model lagi; jika hasil tersedia, kembalikan hasil tersimpan.
- Retry setelah kegagalan harus memiliki aturan eksplisit dan tidak boleh menggandakan user message.
- Assistant final hanya di-commit sekali.

### Context assembly cache

Key sekurangnya mencakup:

```text
session_id + transcript_revision + active_model_id + context_window
+ system_prompt_version + tool_schema_version + workspace_memory_revision
```

Cache hanya menyimpan snapshot internal immutable, dibatasi ukuran, dan di-invalidate ketika salah satu komponen berubah. Untuk model dengan context window lebih kecil, lakukan truncation ulang; untuk model lebih besar, bangun ulang dari transcript persisten agar history yang dahulu dipangkas tidak hilang.

### Pergantian ketika request berlangsung

Aturan tahap awal: model selection saat `RUNNING` menjadi pending untuk turn berikutnya. Request yang sedang berjalan tidak dibatalkan. Gunakan lock/state per session agar request paralel tidak menulis transcript secara race. Cancellation dapat direncanakan setelah protokol WebSocket memiliki pesan cancel yang konsisten.

### Fallback model

Fallback A→B dalam satu request merupakan satu logical turn: satu user message, satu assistant final; attempt dan usage tiap model dicatat terpisah. Jangan simpan assistant error intermediate sebagai jawaban final. `last_response_model` berisi model yang sukses.

## 5. Tool `web_search`

### Modul yang direncanakan

```text
tool/execute/execute_web_search/
  __init__.py
  web_search_tool.py
  provider_base.py
  public_provider.py
  result_parser.py
  search_cache.py
  search_models.py
brain/memory/web_search_memory.py
brain/memory/obsidian_sink.py
brain/memory/graphify_memory_sink.py
tests/test_web_search.py
tests/test_model_switch_context.py
```

Nama file boleh disesuaikan, tetapi boundary provider, cache, memory, dan sink harus terpisah.

### Kontrak input/output

Input:

```json
{"query":"...", "max_results":5, "freshness":"auto|recent|any", "force_refresh":false}
```

Validasi query nonempty, panjang maksimum, whitespace, enum freshness, dan batas hasil. `force_refresh` tidak pernah mengabaikan rate limit. Query berisi secret tidak boleh ditulis mentah ke log/memory.

Output terstruktur dengan `success`, `status`, `query`, `normalized_query`, `cache_hit`, `provider`, timestamp, `results[{title,url,snippet,source_domain}]`, dan `warnings`. Error seperti `timeout`, `rate_limited`, `provider_rejected`, `parse_error`, `empty_results` jangan disamarkan menjadi hasil kosong sukses.

### Provider policy

- User-Agent transparan sebagai SquidWings.
- Timeout, rate limit lokal, bounded retry dan exponential backoff.
- Hormati 403, 429, Retry-After, dan ketentuan provider; hentikan akses bila ditolak.
- Adapter provider dapat diganti jika format berubah.
- Tidak ada proxy/IP rotation, spoofing browser, CAPTCHA bypass, anti-bot evasion, login scraping, atau crawling massal.
- Versi awal hanya memakai endpoint pencarian yang mengizinkan automated access; status provider perlu diverifikasi saat implementasi.

### Cache dan single-flight

Normalisasi query: trim, collapse whitespace, key case-insensitive, panjang dibatasi. Cache key:

```text
sha256(provider + normalized_query + freshness + max_results)
```

Cache entry memiliki schema version, query/key, created/expiry time, provider, status, results, warnings, hit count, last-used. URL hasil dideduplikasi setelah canonicalization yang aman.

Single-flight menggunakan map process-local `cache_key -> asyncio.Future`: query sama yang berjalan bersamaan menunggu hasil future yang sama. Cache persisten mencegah pencarian ulang lintas request/restart. Future harus dibersihkan saat success maupun exception.

Default awal: TTL umum 6 jam; freshness recent TTL lebih singkat/provider-specific; force refresh melewati hasil cache sukses tetapi tetap terkena rate limit; error 403/429 tidak disimpan sebagai hasil sukses; cache diberi batas ukuran dan pemangkasan TTL/LRU.

## 6. WorkspaceMemory, Obsidian, Graphify

### Ledger lokal

Catat event ringkas ke:

```text
<workspace>/.agent_memory/web_search_cache.json
<workspace>/.agent_memory/web_search_events.jsonl
```

Contoh event: event type, query hash/normalized query yang aman, provider, cache hit, result count, timestamp, status. Cache menyimpan detail hasil; ledger menghindari snippet panjang dan pertumbuhan state tidak terbatas.

### Sink abstraction

Definisikan interface/protocol `MemorySink.record_search(event)` dengan implementasi:

- `LocalWorkspaceSink`: wajib.
- `ObsidianSink`: opsional.
- `GraphifyMemorySink`: opsional, default disabled.
- `NoopSink`: fallback.

Sink eksternal best-effort. Gagal sink tidak menggagalkan pencarian/chat; catat warning tanpa credential.

### Graphify

Jangan meng-hardcode endpoint memory yang belum terbukti. Graphify sink aktif hanya saat endpoint dan flag eksplisit tersedia. Payload memiliki idempotency key berbasis workspace, query hash, dan bucket waktu. Retry sink (jika kelak ada) bounded dan tidak boleh menjalankan ulang provider search.

### Obsidian

Konfigurasi opsional `OBSIDIAN_VAULT_PATH`, folder `SquidWings/Web Search`, mode note per query. Note Markdown berisi query, waktu, provider, cache hit, daftar sumber, judul, URL, snippet. Filename aman dan stabil berdasarkan hash/slug. Tulis atomic via temporary file + replace, jangan menghapus edit manual, dan tetap sukseskan hasil tool bila vault tidak tersedia. Cache utama tetap file memory, bukan Obsidian.

## 7. Integrasi AgentLoop

1. Daftarkan tool di `register_all_tools()`.
2. Deskripsi/schema tool menjelaskan batas penggunaan dan untrusted web content.
3. Kembalikan hasil terstruktur ke model dan simpan tool call/result dengan `tool_call_id`.
4. Replay history saat reload/model switch menggunakan tool result tersimpan, tidak mengeksekusi jaringan kembali.
5. System prompt menyarankan search untuk informasi terbaru, melarang mengikuti instruksi web, meminta sumber/tanggal, dan menjelaskan kegagalan provider.
6. Expose tool hanya bila capability model/tool parser mendukungnya.
7. Batasi ukuran snippet dan total tool result agar tidak memenuhi context.
8. Retry/fallback model memakai hasil tool yang telah didapat; tool baru dipanggil ulang hanya atas tool call baru yang valid atau force refresh eksplisit.

## 8. Konfigurasi

Tambahkan ke `Settings` dan `.env.example`:

```env
WEB_SEARCH_ENABLED=true
WEB_SEARCH_PROVIDER=public_search
WEB_SEARCH_TIMEOUT_SECONDS=15
WEB_SEARCH_MAX_RESULTS=5
WEB_SEARCH_CACHE_TTL_SECONDS=21600
WEB_SEARCH_RECENT_TTL_SECONDS=1800
WEB_SEARCH_MIN_INTERVAL_SECONDS=2
WEB_SEARCH_MAX_QUERY_LENGTH=500
WEB_SEARCH_MAX_CACHE_ENTRIES=500
WEB_SEARCH_USER_AGENT=SquidWings-WebSearch/1.0
OBSIDIAN_SEARCH_ENABLED=false
OBSIDIAN_VAULT_PATH=
OBSIDIAN_SEARCH_FOLDER=SquidWings/Web Search
OBSIDIAN_SEARCH_NOTE_MODE=per_query
GRAPHIFY_MEMORY_ENABLED=false
GRAPHIFY_MEMORY_ENDPOINT=
GRAPHIFY_MEMORY_TIMEOUT_SECONDS=10
```

Defaults aman: Graphify/Obsidian disabled, tidak perlu API credential baru, search dapat dimatikan.

## 9. Fase implementasi

### Phase 0 — Baseline

Catat test/compile baseline, schema session, rollback plan, dan jangan migrasi destruktif. Tetapkan schema version. **Exit:** baseline diketahui.

### Phase 1 — Model session metadata

Persist `active_model`, kompatibilitas `model_used`, request id dan transcript revision. Unit test A→B menggunakan satu transcript. **Exit:** tidak ada duplicate user/assistant message.

### Phase 2 — Web search core

Buat provider protocol, result model, adapter patuh, timeout/rate limit/backoff/parser/dedup, persistent cache dan single-flight. Test seluruh network dengan mock. **Exit:** parallel query sama menghasilkan satu network call.

### Phase 3 — Tool registration

Integrasikan ke registry/AgentLoop, tool result persistence, prompt safety dan replay tanpa side effect. **Exit:** model dapat memanggil tool dan merangkum sumber.

### Phase 4 — Memory sinks

Local ledger, Obsidian atomic writer, Graphify sink feature-flagged dengan endpoint eksplisit. **Exit:** kegagalan sink tidak merusak cache lokal/chat.

### Phase 5 — Concurrency/context cache

Session lock, request idempotency, context cache model-aware, race tests, fallback tests. **Exit:** concurrent turns/model switch tidak menggandakan turn/tool call.

### Phase 6 — UI/observability

Tampilkan model aktif, status “berlaku pada turn berikutnya” saat request berjalan, progress/status search, cache hit, sumber dan timestamp. Log metrics tanpa query sensitif. **Exit:** UX menjelaskan model/sumber/cache dengan transparan.

### Phase 7 — Rollout

Feature flag, default disabled untuk Graphify/Obsidian, canary lokal, pantau 403/429/parse failures/cache hit/duplicate prevention. Dokumentasikan provider yang diizinkan dan fallback manual. **Exit:** tidak ada bypass policy atau regression session.

## 10. Test matrix

### Web search

- query kosong, terlalu panjang, whitespace normalization, secret redaction;
- result parser, malformed HTML/JSON, URL dedup;
- cache hit/miss/expired/force refresh;
- single-flight bersamaan;
- timeout, 403, 429 + Retry-After, 5xx, empty results;
- rate limit dan bounded retry;
- cache file corrupt/migration/size pruning;
- local ledger write;
- Obsidian missing/permission error/atomic write;
- Graphify disabled, timeout, idempotent retry;
- web prompt injection tetap diperlakukan data.

### Model switch/chat memory

- pilih model baru tidak menambah message;
- model baru melanjutkan `session_id` dan menerima transcript yang sama;
- setiap assistant message memiliki model provenance;
- reload session memulihkan active model;
- old session schema tetap terbaca;
- duplicate request id hanya commit sekali;
- model switch saat request aktif berlaku pada turn selanjutnya;
- fallback A→B hanya satu user + satu assistant final;
- context cache invalid pada revision/model/prompt/tool schema change;
- tool result replay tidak memanggil web provider lagi.

## 11. Definition of Done

- Model switch dalam session yang sama tidak membuat transcript baru atau duplicate turn.
- Active model persisten setelah restart dan tiap response mempertahankan provenance.
- Request ID mencegah submit/retry ganda.
- Cache + single-flight mencegah query identik yang valid diulang pada waktu sama.
- Search menghormati 403/429 dan tidak melakukan anti-bot evasion.
- Semua network test memakai mock; test suite tidak memerlukan internet.
- Workspace ledger selalu bekerja; Obsidian/Graphify bersifat opsional dan non-fatal.
- Replay/fallback tidak mengulang side effect tool tanpa instruksi eksplisit.
- Data session/cache lama tetap backward-compatible.
- Config, dokumentasi, failure mode dan batas privasi dijelaskan.

## 12. Out of scope

- Menyamarkan aplikasi sebagai pengguna manusia untuk menghindari blokir.
- Rotasi proxy/IP atau CAPTCHA/anti-bot bypass.
- Web crawling umum atau fetch arbitrary URL.
- Menganggap endpoint Graphify memory tanpa dokumentasi.
- Response cache otomatis untuk semua chat.
- Mengubah routing/model policy besar-besaran bersamaan dengan fitur ini.
- Menghapus atau mengubah format transcript lama secara destruktif.
