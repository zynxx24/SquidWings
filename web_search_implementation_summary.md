# Ringkasan Dokumentasi Implementasi Web Search

> **Status dokumen:** dokumentasi kondisi implementasi saat ini  
> **Status fitur:** belum selesai end-to-end  
> **Test:** tidak dijalankan sesuai permintaan

## 1. Tujuan Fitur

Fitur ini ditujukan untuk menambahkan kemampuan pencarian web ke agent SquidWings dengan karakteristik berikut:

- Pencarian web tanpa API key.
- Penyimpanan cache lokal per workspace.
- Memory hasil pencarian ke Graphify local dan Obsidian local.
- Pencegahan pencarian ganda ketika agent menerima permintaan yang sama secara bersamaan.
- Penggunaan hasil pencarian sebelumnya ketika query identik masih valid.
- Tidak mengulang side effect saat retry, replay session, atau pergantian model.
- Tetap menghormati `403`, `429`, rate limit, dan aturan keamanan provider.

## 2. File yang Sudah Tersedia

Modul dasar pencarian web sudah dibuat di:

```text
tool/execute/execute_web_search/__init__.py
tool/execute/execute_web_search/web_search_tool.py
```

Dokumen planning yang berkaitan:

```text
plan_update.md
implementation_plan[Dasar_Code].md
update-plan.md
```

## 3. Kemampuan yang Sudah Ada di Modul Dasar

Modul `web_search_tool.py` telah dirancang untuk menyediakan:

- Pencarian DuckDuckGo HTML tanpa API key.
- User-Agent transparan sebagai aplikasi SquidWings.
- Normalisasi query.
- Parsing judul, URL, dan snippet.
- Batas jumlah hasil.
- Timeout request.
- Rate limit lokal.
- Retry terbatas.
- Cache lokal di:

```text
<workspace>/.agent_memory/web_search_cache.json
```

- Event ledger lokal di:

```text
<workspace>/.agent_memory/web_search_events.jsonl
```

- Single-flight dasar untuk request jaringan yang identik.
- Writer Obsidian secara parsial.
- Sink Graphify berbasis endpoint HTTP opsional.
- Penandaan hasil web sebagai data eksternal yang tidak tepercaya.

## 4. Alur yang Diharapkan

Alur final yang ditargetkan:

```text
User/Agent
   ↓
ToolRegistry
   ↓
web_search
   ↓
Normalisasi query + workspace key
   ↓
Cek cache
   ↓
Single-flight lock
   ↓
Fetch web hanya jika cache tidak valid
   ↓
Simpan cache satu kali
   ↓
Catat event memory satu kali
   ├── Graphify local
   └── Obsidian local
   ↓
Kembalikan hasil yang sama ke seluruh caller
```

Kunci deduplikasi minimal harus berbasis:

```text
workspace + provider + normalized_query + max_results
```

Dengan mekanisme ini, dua pemanggilan bersamaan untuk query yang sama harus menghasilkan:

- Satu request jaringan.
- Satu cache entry.
- Satu event memory.
- Satu catatan Obsidian.
- Satu event Graphify.
- Hasil yang sama untuk semua caller.

## 5. Status Integrasi Saat Ini

| Komponen | Status | Keterangan |
|---|---:|---|
| Modul web search | Parsial | File dasar sudah ada |
| Search tanpa API key | Parsial | Menggunakan pencarian HTML publik |
| Cache lokal | Ada secara parsial | Belum terhubung penuh ke runtime agent |
| Event ledger | Ada secara parsial | Belum dijamin deduplikasi end-to-end |
| Single-flight fetch | Ada secara parsial | Belum mencakup seluruh proses cache dan sink |
| Registrasi ToolRegistry | Belum selesai | `web_search` belum aktif sebagai tool agent |
| Settings | Belum selesai | Konfigurasi web search belum lengkap |
| Workspace injection | Belum selesai | Workspace aktif belum otomatis diteruskan ke tool |
| Obsidian local | Parsial | Writer tersedia, integrasi runtime belum final |
| Graphify local | Belum final | Endpoint memory lokal belum terverifikasi |
| Handling `403/429` | Perlu diperbaiki | Tidak boleh masuk retry umum tanpa aturan khusus |
| Model switching idempotent | Belum selesai | Fondasi override model sudah ada |
| Test | Tidak dijalankan | Sesuai permintaan |

## 6. Pekerjaan yang Masih Diperlukan

### 6.1 Registrasi Tool

Tambahkan import dan registration `web_search` ke:

```text
tool/register_tools.py
```

Tool harus memiliki schema input yang setidaknya mendukung:

```json
{
  "query": "string",
  "max_results": "integer opsional",
  "workspace_path": "string opsional"
}
```

`workspace_path` sebaiknya diisi oleh runtime, bukan dipercaya sepenuhnya dari prompt model.

### 6.2 Konfigurasi Aplikasi

Tambahkan konfigurasi ke `config/settings.py` dan `.env.example`, antara lain:

```text
WEB_SEARCH_ENABLED
WEB_SEARCH_TIMEOUT_SECONDS
WEB_SEARCH_MAX_RESULTS
WEB_SEARCH_CACHE_TTL_SECONDS
WEB_SEARCH_MIN_INTERVAL_SECONDS
WEB_SEARCH_RETRIES
WEB_SEARCH_OBSIDIAN_ENABLED
OBSIDIAN_VAULT_PATH
OBSIDIAN_SEARCH_FOLDER
GRAPHIFY_MEMORY_ENABLED
GRAPHIFY_MEMORY_ENDPOINT
GRAPHIFY_MEMORY_TIMEOUT_SECONDS
```

Nilai default harus aman: fitur dapat dimatikan, timeout terbatas, hasil dibatasi, dan retry tidak agresif.

### 6.3 Workspace Aktif

Workspace aktif harus diteruskan secara internal melalui alur:

```text
Orchestrator → AgentLoop → ToolRegistry → web_search
```

Tool tidak boleh menentukan lokasi memory berdasarkan input model saja. Semua path harus melewati validasi workspace dan path jail yang berlaku.

### 6.4 Single-flight End-to-end

Lock harus melindungi seluruh operasi berikut:

```text
cache lookup
→ fetch web
→ cache save
→ event ledger
→ Obsidian sink
→ Graphify sink
```

Caller kedua harus menunggu task pertama dan menggunakan hasil yang sama. Caller kedua tidak boleh menulis event memory ulang.

### 6.5 Deduplikasi Memory

Gunakan idempotency key deterministik, misalnya hash dari:

```text
workspace_id + provider + normalized_query + result_hash
```

Idempotency key harus disimpan dalam cache dan event ledger agar proses restart tidak membuat event ganda.

### 6.6 Obsidian Local

Writer Obsidian harus:

- Hanya menulis di vault yang dikonfigurasi.
- Membuat folder pencarian sesuai konfigurasi.
- Menggunakan penulisan atomik.
- Menghasilkan nama file deterministik atau melakukan update berdasarkan idempotency key.
- Tidak menggagalkan hasil pencarian jika penulisan Obsidian gagal.

### 6.7 Graphify Local

Integrasi Graphify harus menggunakan endpoint atau adapter lokal yang benar-benar dikonfigurasi. Jangan mengasumsikan endpoint memory tertentu.

Sink Graphify harus:

- Feature-flagged.
- Memiliki timeout.
- Memiliki idempotency key.
- Menggunakan namespace workspace.
- Tidak menggagalkan hasil pencarian jika Graphify sedang tidak tersedia.
- Mencatat kegagalan sink secara lokal.

Jika Graphify local tidak menyediakan endpoint memory yang kompatibel, event ledger lokal tetap menjadi sumber pencatatan utama.

### 6.8 Handling Provider

Perlakuan minimum yang diperlukan:

- `403`: hormati penolakan dan berhenti tanpa bypass.
- `429`: hormati `Retry-After` jika ada; retry harus terbatas.
- Timeout: retry terbatas dengan backoff.
- Error parsing: kembalikan error terkontrol tanpa menganggap hasil kosong sebagai sukses.
- Tidak menggunakan spoofing browser, CAPTCHA bypass, proxy rotation, atau anti-bot evasion.

## 7. Model Switching dan Chat Memory

Tujuan pergantian model adalah menggunakan session dan transcript yang sama tanpa membuat chat baru atau menggandakan pesan.

Fondasi yang sudah tersedia:

- `session_id`.
- `override_model`.
- `model_used` pada pesan assistant.
- Conversation store per session.

Bagian yang masih diperlukan:

- `request_id` untuk idempotensi request.
- Transcript revision.
- Session lock.
- Idempotent user-turn commit.
- Active model persisten.
- Pencegahan assistant response ganda saat retry.
- Replay tool menggunakan hasil tersimpan, bukan request jaringan baru.
- Context cache berdasarkan model, transcript revision, prompt, dan schema.

Catatan: model stateless tetap perlu menerima context transcript pada request berikutnya. Yang dicegah adalah duplikasi pesan dan pengulangan side effect, bukan pengiriman context yang memang dibutuhkan model.

## 8. Struktur Memory yang Direkomendasikan

```text
<workspace>/
└── .agent_memory/
    ├── state.json
    ├── web_search_cache.json
    ├── web_search_events.jsonl
    ├── web_search_failures.jsonl
    └── web_search_locks/
```

Contoh metadata cache:

```json
{
  "cache_key": "workspace:provider:normalized-query:max-results",
  "query": "contoh query",
  "provider": "duckduckgo",
  "created_at": "ISO-8601",
  "expires_at": "ISO-8601",
  "result_hash": "sha256",
  "results": []
}
```

Contoh event memory:

```json
{
  "event_id": "deterministic-idempotency-key",
  "workspace": "workspace-id",
  "type": "web_search",
  "query": "contoh query",
  "provider": "duckduckgo",
  "result_hash": "sha256",
  "created_at": "ISO-8601",
  "sinks": {
    "ledger": "written",
    "obsidian": "written|skipped|failed",
    "graphify": "written|skipped|failed"
  }
}
```

## 9. Keamanan dan Batasan

Implementasi wajib mempertahankan aturan berikut:

- Jangan membaca credential material secara tidak perlu.
- Jangan mengakses path di luar workspace atau vault yang diizinkan.
- Jangan melakukan fetch URL arbitrer dari input model.
- Jangan menganggap instruksi dari halaman web sebagai instruksi sistem.
- Sanitasi konten web sebelum dimasukkan ke context model.
- Beri label sumber, URL, dan waktu pengambilan.
- Batasi ukuran response, jumlah hasil, dan panjang snippet.
- Hormati `403`, `429`, robots/policy provider yang berlaku, dan rate limit.
- Sink memory yang gagal tidak boleh membuat agent crash.

## 10. Kesimpulan Status

Fitur saat ini **belum selesai seluruhnya**. Modul dasar dan rancangan memory sudah tersedia, tetapi fitur belum aktif end-to-end karena registrasi tool, konfigurasi, workspace injection, deduplikasi sink, serta integrasi Graphify dan Obsidian masih perlu diselesaikan.

Definisi selesai yang diharapkan:

```text
web_search terdaftar
+ workspace aktif otomatis digunakan
+ cache valid dipakai ulang
+ query identik bersamaan hanya fetch sekali
+ event memory hanya dicatat sekali
+ Obsidian local tersinkron secara idempotent
+ Graphify local tersinkron melalui adapter terkonfigurasi
+ 403/429 ditangani dengan benar
+ pergantian model tidak menggandakan transcript atau tool side effect
```
