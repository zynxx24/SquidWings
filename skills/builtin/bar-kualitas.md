---
name: bar-kualitas
description: Bar kualitas wajib untuk setiap aplikasi, web, API, atau proyek - keamanan, kualitas kode, keandalan, performa, maintainability, dan usability semuanya harus lolos, dibuktikan PASS atau FAIL sebelum bilang selesai.
tasks: code, activity
triggers: aplikasi, app, web, website, api, backend, frontend, project, proyek, sistem, dashboard, admin panel, laravel, react, next, vue, express, fastapi, django, keamanan, security, celah, performa, performance, kualitas kode, code quality
scope: contextual
context: aplikasi, web, website, api, backend, frontend, proyek, project, sistem, deploy, produksi, production
companion: build-web, build-backend, add-feature, fix-bug, laravel
---
<!-- Generated from .claude/skills/quality-gate/SKILL.md by scripts/sync_agent_config.py. Edit that file. -->
# Quality gate

Keenam aspek harus PASS. Satu FAIL berarti belum selesai. Perbaiki akar masalahnya, lalu jalankan ulang pemeriksaannya.

## 1. Keamanan

- Semua input divalidasi di server. Query berparameter. Output di-escape sesuai konteks (HTML, URL, shell, SQL).
- Autentikasi dan otorisasi dicek per aksi, bukan hanya per halaman. IDOR dicoba dengan akun lain.
- Rahasia hanya di env atau secret store; tidak ada di kode, log, URL, atau pesan error. Pindai: `git grep -nE "(api[_-]?key|secret|password|token)\s*[:=]"`.
- Header keamanan (CSP, HSTS, X-Content-Type-Options, frame-ancestors). Cookie `HttpOnly`, `Secure`, `SameSite`. Ada proteksi CSRF.
- Rate limit di login, reset password, dan form publik. Upload dicek tipe dan ukurannya.
- Dependensi diaudit (`npm audit`, `pip-audit`, `composer audit`).
- Untuk aplikasi web: jalankan skill `redhat` (serang sendiri, perbaiki, ulangi sampai bersih).

## 2. Kualitas kode

- Linter dan type checker proyek bersih (ruff/mypy, eslint/tsc, phpstan/pint).
- Ikuti konvensi yang sudah ada. Nama menjelaskan maksud. Fungsi kecil dengan satu tanggung jawab.
- Tidak ada kode mati, debug print, except atau catch kosong, fallback yang menyembunyikan error, atau import yang tidak dipakai.
- `aislop_scan` (di harness) atau review manual dengan aturan yang sama.

## 3. Keandalan

- Setiap panggilan jaringan, disk, atau proses punya timeout dan penanganan error yang dilaporkan jujur.
- Tulisan ganda dibungkus transaksi. Aksi yang bisa diulang dibuat idempoten.
- UI punya state kosong, loading, error, dan sukses.
- Tes otomatis untuk logika baru, kasus pinggir, dan bug yang pernah terjadi. Semua tes lulus.

## 4. Performa

- Database: tanpa N+1, indeks di kolom filter dan join, pagination untuk daftar.
- Web: gambar dikompres dan diberi ukuran, JS dan CSS dipangkas, lazy-load di bawah lipatan; ukur Core Web Vitals (LCP < 2.5 s, CLS < 0.1, INP < 200 ms).
- API: ukur waktu respons endpoint utama; cache yang punya TTL dan aturan invalidasi.
- Angka dari pengukuran, bukan perkiraan.

## 5. Maintainability

- Struktur folder yang menjelaskan dirinya. Konfigurasi terpisah dari kode. Satu sumber kebenaran untuk data yang dipakai di banyak tempat.
- README: cara menjalankan, mengetes, dan men-deploy. Orang lain bisa menjalankannya dari README saja.
- Perubahan kecil dan bisa direview. Commit menjelaskan alasannya.

## 6. Usability

- Bekerja di 360 px sampai desktop tanpa scroll horizontal. Target sentuh minimal 44 px.
- Kontras teks minimal 4.5:1 (teks besar 3:1). Bisa dipakai dengan keyboard, fokus terlihat, label pada setiap input.
- Pesan error menjelaskan apa yang salah dan cara memperbaikinya, dalam bahasa pengguna.
- Buka dan klik sendiri alur utamanya. Tampilan mengikuti skill `aislop`.

## Laporan

```
Keamanan        PASS  <bukti: perintah/hasil>
Kualitas kode   PASS  <bukti>
Keandalan       PASS  <bukti>
Performa        PASS  <bukti>
Maintainability PASS  <bukti>
Usability       PASS  <bukti>
Tidak dicek     <apa dan kenapa>
```
