---
skill: graphify
upstream: guide
fallback: true
---
# graphify di SquidWings

graphify mengubah folder (kode, dokumen, gambar) jadi graf pengetahuan. Di harness ini graf kode tiap chat dibangun otomatis di `graphify-out/` folder proyek, jadi biasanya tidak perlu menjalankan pipeline lengkap.

## Pilih jalur

1. **Pertanyaan tentang kode atau arsitektur** ("fungsi ini dipanggil di mana", "apa yang rusak kalau X diubah", "bagian paling sentral"): `code_graph(action=...)` dengan `hubs`, `query`, `explain`, `affected`, atau `path`. Kalau graf belum ada atau sudah basi, `code_graph(action="build")` dulu.
2. **Laporan graf untuk dibaca orang**: jalankan `graphify` lewat `execute_command` di folder proyek, lalu kirim `graphify-out/GRAPH_REPORT.md` atau `graph.html` dengan `share_file`.
3. **Graf dari dokumen, paper, atau video** (bukan kode), atau ekspor GraphRAG: ikuti panduan resmi graphify dari safishamsi/graphify, `load_skill(name="graphify", file="SKILL.md")`. Panduan itu ditulis untuk Claude Code: subagent di sana dikerjakan berurutan sendiri di sini.

## Aturan

- Kalau `code_graph` tidak tersedia, program graphify belum terpasang: `uv tool install graphifyy`, atau `python main.py tools`.
- Sebut bahwa jawaban berasal dari graf, dan cek baris yang penting dengan `read_file` sebelum mengubah kode.
- `graphify-out/` tidak ikut di-commit kecuali pengguna minta.
