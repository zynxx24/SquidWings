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
