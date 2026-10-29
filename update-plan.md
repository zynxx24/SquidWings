# SquidWings — Update Plan Tahap 1

**Tanggal scan:** 2025-02-14  
**Scope:** static scan ringan terhadap source lokal; tidak melakukan perubahan kode dan tidak menjalankan test suite.  
**Tujuan tahap 1:** mengurangi dead code dan noise lint dengan risiko perubahan perilaku serendah mungkin.

## Ringkasan hasil scan

- `main.py` berukuran sekitar 89 KB dan memuat 23 fungsi utama. Ini adalah titik kompleksitas terbesar.
- Ruff menemukan **134 issue**:
  - **78** dapat diperbaiki otomatis dengan safe fix.
  - **27** memerlukan unsafe fix dan **tidak** boleh dijalankan massal.
  - Sisanya perlu keputusan manual.
- Kandidat dead code paling jelas:
  - import tidak terpakai di `agent/orchestrator.py`, `agent/agent_loop.py`, `health_check.py`, `response_handler.py`, beberapa modul `model`, `utils`, dan `web`;
  - variabel lokal tidak terpakai: `session` di `agent/orchestrator.py`, `text_len` di classifier, serta loop variable `model_id` di `session_manager.py`;
  - `typing.Any`/`Annotated` dan import lama lain yang hanya menambah noise.
- Ada beberapa `except Exception: pass`. Sebagian tampak disengaja sebagai fallback/cleanup, tetapi sebagian menyembunyikan error tanpa logging:
  - `main.py`, `utils/cli_formatter.py`, `utils/tunnel.py`;
  - `brain/memory/*`, `brain/generate_efficient_token/token_counter.py`;
  - `model/connection/graphify_client.py`.
- Temuan yang berpotensi memengaruhi perilaku, bukan sekadar style:
  - `web/team_api.py:128` memiliki `return` di dalam `finally` (`B012`), yang dapat menelan exception.
  - `graphify_client.py` memiliki raise di dalam `except` tanpa chaining (`B904`), sehingga root cause dapat hilang.
- Duplikasi desain yang layak dievaluasi setelah hygiene selesai:
  - `TokenBucket` di `security/rate_guard.py` dan `model/settings/rate_limiter.py`;
  - beberapa kelas `ValidationResult`/validator di lapisan berbeda;
  - enum string berulang dengan pola `class X(str, Enum)`.
  Duplikasi ini **belum dihapus** pada tahap 1 karena berisiko mengubah kontrak antar-modul.
- Repository ini tidak memiliki direktori `.git`, jadi scan tidak dapat membedakan baseline code dan perubahan lokal sebelumnya.
- `python -m compileall` berhasil untuk source yang discan, tetapi ini hanya validasi sintaks, bukan bukti seluruh runtime flow bekerja.

## Prioritas

| Prioritas | Area | Alasan |
|---|---|---|
| P0 | `web/team_api.py:128` | Potensi exception tertelan; bug correctness yang konkret |
| P0 | Raise chaining di `graphify_client.py` | Mempertahankan root cause saat koneksi/API gagal |
| P1 | Unused imports dan unused locals | Dead code paling aman dihapus, mengurangi noise dan import overhead kecil |
| P1 | `B007`, `F401`, `F841`, `I001`, `UP015`, `W293` | Perubahan mekanis dengan risiko rendah |
| P2 | `except Exception: pass` | Perlu dibedakan antara fallback yang memang silent dan error yang harus dicatat |
| P2 | Pemecahan `main.py` | Dampak besar; jangan dilakukan bersamaan dengan cleanup kecil |
| P3 | Konsolidasi `TokenBucket`/validator/enum | Refactor arsitektur, lakukan hanya setelah kontrak dipetakan |

## Tahap 1 — Hygiene aman dan perbaikan correctness kecil

### 1. Baseline dan aturan perubahan

- Simpan hasil lint baseline dari command berikut ke artefak lokal, tanpa mengubah source:

  ```bash
  ./.venv/bin/ruff check . --exclude .venv --exclude .venv_ruff --exclude .ruff_cache
  ```

- Jangan menjalankan `ruff --unsafe-fixes`.
- Jangan menghapus fungsi hanya karena tidak ditemukan pemanggil secara tekstual; entrypoint, callback, reflection, dan API route dapat dipanggil dinamis.
- Jangan menyentuh `.env`, `audit.jsonl`, `tunnel.log`, atau data session pada tahap ini.

### 2. Cleanup dead code yang jelas

Target awal:

- hapus import yang dilaporkan `F401` dan tidak dipakai;
- hapus atau gunakan variabel yang dilaporkan `F841`;
- ubah loop variable yang tidak digunakan menjadi `_` bila memang tidak diperlukan;
- hapus whitespace-only blank lines (`W293`);
- rapikan import order (`I001`) setelah penghapusan import;
- terapkan safe fixes untuk `UP015` dan import dari `collections.abc` bila tidak mengubah public API.

File awal yang perlu ditangani:

- `agent/agent_loop.py`
- `agent/orchestrator.py`
- `agent/session_manager.py`
- `agent/team_orchestrator.py`
- `brain/classifier/*`
- `brain/context/*`
- `brain/memory/conversation_store.py`
- `brain/memory/workspace_memory.py`
- `brain/min_token/*`
- `model/connection/health_check.py`
- `model/connection/response_handler.py`
- `model/registry/model_registry.py`
- `model/settings/*`
- `utils/logger.py`, `utils/retry.py`, `utils/timer.py`, `utils/event_bus.py`
- `web/team_api.py`

### 3. Perbaikan manual berisiko rendah

- Pindahkan atau hilangkan `return` di `finally` pada `web/team_api.py` agar exception tidak tertelan. Gunakan alur `try/except` yang eksplisit dan pertahankan response contract yang ada.
- Tambahkan exception chaining (`raise ... from err` atau `from None`) di `graphify_client.py` sesuai maksud error.
- Untuk `except Exception: pass`, jangan mengganti secara massal. Klasifikasikan dulu:
  - **suppress valid:** cleanup, best-effort terminal flush, cancellation;
  - **harus dilaporkan:** persistence, network, parsing, dan security path.
- Pada kategori kedua, gunakan logger yang sudah tersedia dan pertahankan fallback yang ada.

### 4. Guardrail terhadap regresi

- Setelah setiap kelompok perubahan, jalankan hanya lint pada file yang disentuh.
- Jangan melakukan refactor `main.py` pada commit/perubahan yang sama dengan cleanup import.
- Pastikan API team tetap memiliki endpoint dan status code yang sama.
- Pastikan error network tetap mempertahankan exception asli melalui chaining.
- Hindari perubahan pada format persistence JSON/SQLite dan audit chain.

## Out of scope Tahap 1

- Memecah `main.py` menjadi banyak modul.
- Menggabungkan dua implementasi `TokenBucket`.
- Menghapus modul yang tampak tidak dipanggil.
- Mengubah enum `str, Enum` secara massal.
- Mengubah retry, timeout, security policy, sandbox, atau routing behavior.
- Menambah dependency baru.
- Mengaktifkan unsafe Ruff fixes.

## Tahap berikutnya setelah Tahap 1

1. Pemetaan import graph dan entrypoint dinamis untuk membuktikan kandidat dead module.
2. Refactor bertahap `main.py` berdasarkan boundary yang sudah ada: CLI, workspace, rendering, dan request pipeline.
3. Konsolidasi abstraksi rate limiter/validator hanya jika kontrak dan pemilik state sudah jelas.
4. Audit exception policy: error mana yang perlu user-facing message, logger, metrics, atau re-raise.
5. Baru kemudian lakukan validasi runtime yang terarah.

## Definition of Done Tahap 1

- Tidak ada `F401`, `F841`, `B007`, `W293`, dan `B012` yang tersisa pada file target, kecuali ada alasan tertulis.
- Tidak ada unsafe Ruff fix yang digunakan.
- Semua `except Exception: pass` yang tersisa memiliki alasan yang jelas atau komentar singkat.
- Tidak ada perubahan pada kontrak API, format data, security policy, atau konfigurasi environment.
- Laporan perubahan mencantumkan file yang disentuh dan issue Ruff baseline yang tersisa.
