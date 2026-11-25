---
skill: json-canvas
upstream: inline
---
[Penyesuaian SquidWings untuk JSON Canvas]
- Simpan file `.canvas` di folder proyek dengan `file_write`. ID node dan edge harus unik; cek ulang dengan `read_file` setelah menulis.
- Kalau pengguna ingin peta catatan vault memori, ambil hubungannya dulu dari `vault_neighbours` atau `vault_graph_build`, baru susun canvas-nya.
