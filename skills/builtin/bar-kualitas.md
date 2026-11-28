---
name: bar-kualitas
description: Bar kualitas wajib untuk setiap aplikasi, web, API, atau proyek - keamanan, kualitas kode, keandalan, performa, maintainability, dan usability semuanya harus lolos, dibuktikan PASS atau FAIL sebelum bilang selesai.
tasks: code, activity
triggers: aplikasi, app, web, website, api, backend, frontend, project, proyek, sistem, dashboard, admin panel, laravel, react, next, vue, express, fastapi, django, keamanan, security, celah, performa, performance, kualitas kode, code quality
scope: contextual
context: aplikasi, web, website, api, backend, frontend, proyek, project, sistem, deploy, produksi, production
companion: build-web, build-backend, add-feature, fix-bug, laravel
---
# Bar kualitas (keenamnya wajib)

Kode yang jalan tapi bercelah, lambat, rapuh, sulit dirawat, atau sulit dipakai **belum selesai**. Rencanakan keenam aspek ini sebelum menulis kode, dan buktikan setelahnya.

| Aspek | Yang harus ada | Cara membuktikan |
|---|---|---|
| **Keamanan** | Validasi input di server; auth dan otorisasi per aksi (cek IDOR dengan akun lain); query berparameter; output di-escape; rahasia hanya di `.env`; header keamanan; cookie `HttpOnly`/`Secure`/`SameSite`; CSRF; rate limit di login dan form publik; upload dicek tipe dan ukurannya | `load_skill("redhat")`: serang aplikasinya sendiri (OWASP Top 10), perbaiki akar masalahnya, ulangi sampai bersih; `npm audit` / `pip-audit` / `composer audit` |
| **Kualitas kode** | Ikuti konvensi proyek; nama jelas; fungsi kecil; tanpa kode mati, debug print, catch kosong, fallback yang menyembunyikan error | `run_checks` (lint, type), `aislop_scan` tanpa error |
| **Keandalan** | Error dilaporkan jujur; timeout dan retry di panggilan jaringan; transaksi untuk tulisan ganda; aksi yang bisa diulang dibuat idempoten; state kosong, loading, error di UI | Tes otomatis untuk logika baru dan kasus pinggir, semuanya lulus |
| **Performa** | Tanpa N+1; indeks di kolom yang dicari; pagination; gambar dikompres; JS/CSS dipangkas; cache dengan TTL | Ukur: waktu respons endpoint, ukuran bundle, LCP < 2.5 s, CLS < 0.1 |
| **Maintainability** | Struktur folder jelas; satu tanggung jawab per modul; konfigurasi terpisah dari kode; README cara menjalankan dan mengetes | Jalankan ulang dari README di folder bersih |
| **Usability** | 360 px sampai desktop tanpa scroll horizontal; target sentuh 44 px; kontras 4.5:1; keyboard dan fokus terlihat; label di setiap input; pesan error yang menjelaskan cara memperbaiki | `preview_url`, buka dan klik alur utamanya; `contrast_check`; skill `aislop` |

## Laporan sebelum bilang selesai

```
Keamanan        PASS  <bukti>
Kualitas kode   PASS  <bukti>
Keandalan       PASS  <bukti>
Performa        PASS  <bukti>
Maintainability PASS  <bukti>
Usability       PASS  <bukti>
Tidak dicek     <apa dan kenapa>
```

Satu FAIL berarti diperbaiki dulu, lalu dicek ulang. Jangan menurunkan standar untuk menyembunyikan FAIL. Yang tidak bisa dicek (misalnya tanpa internet), sebut terus terang.
