# tools/

Semua tool yang bisa dipanggil AI, satu folder per segmen. `register_tools.py` mendaftarkan tool inti (selalu tersedia); `toolbox/` berisi 100+ tool tersembunyi yang baru dibuka lewat `find_tools` atau saat skill membutuhkannya.

| Folder | Tool |
|---|---|
| `registry/` | Skema tool dan registry-nya |
| `command_prompt/` | `execute_command` (di sandbox), `terminal_run` (panel Terminal per chat) |
| `edit_change_file/`, `discard_change_file/` | Menulis, menambal, dan membatalkan perubahan file, dengan backup |
| `upload_file/` | Membaca file dan unggahan (PDF, Office, gambar dengan OCR) |
| `local_ops/` | Operasi file di folder kerja: cari, pindah, salin, backup |
| `local_memory/` | Vault memori (format Obsidian), memori proyek, profil pengguna, ringkasan sesi |
| `code_intel/` | `code_graph` (graphify), cek kualitas, `aislop_scan`, pembaruan alat lokal |
| `web_search/` | `web_search` (SearXNG, Brave, dll.) dan `web_read` |
| `share_file/` | Mengirim file ke pengguna |
| `skills/` | `load_skill`: membuka panduan skill, termasuk panduan resmi lewat `file="SKILL.md"` |
| `knowledge/` | Impor hasil scrape Google Maps ke dataset tempat |
| `auto_generate/` | Pembuatan otomatis berbasis model |
| `toolbox/` | Tool tersembunyi per kelompok: data, matematika, media, Office, peta, gaya desain, suara, keuangan |

Tool milik add-on (trading, Studio) tidak ada di sini: tool itu ada di `addons/<nama>/tools/` dan hanya didaftarkan saat add-on aktif.

## Menambah tool

1. Tool inti: tulis fungsinya di folder segmen yang cocok, lalu daftarkan `ToolSchema` di `register_tools.py`.
2. Tool tersembunyi: tambah ke modul di `toolbox/` dengan `spec(...)` dari `toolbox/catalog.py`. Tool itu otomatis bisa ditemukan lewat `find_tools`.
3. Tulis test di `tests/`.
