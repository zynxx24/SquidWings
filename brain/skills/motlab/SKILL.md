---
name: motlab
description: Video sebagai kode dengan engine motlab - iklan produk/app, animasi logo, explainer, tutorial, edit footage dan screen recording, klip sosial - dibangun dari referensi pengguna lalu direview di viewer motlab, tanpa AI generator gambar/video.
triggers: motlab, video iklan, iklan video, video promo, promo video, video produk, product video, motion ad, ad video, iklan produk, animasi logo, logo animation, edit footage, screen recording, video tutorial app, video explainer produk
scope: contextual
context: video, mp4, iklan, promo, footage, rekaman, reel, reels, shorts, tiktok, render, produk, brand, logo, aplikasi, app, explainer, durasi, detik
requires: node, ffmpeg, motlab
tools: execute_command, load_skill, share_file, read_file, file_write, file_patch, style_pick, voice_list, voice_speak
---
# motlab (video sebagai kode)

Diadaptasi dari staryone/motlab 0.4.1 (MIT OR Apache-2.0, dipakai di bawah MIT, lihat LICENSE; commit fd2bfda). Panduan, referensi, template dan skripnya milik penulis asli dan tidak diubah. Halaman ini cara menjalankannya di SquidWings.

Setiap scene adalah halaman canvas dengan satu fungsi `renderAt(t)`. motlab memutarnya di viewer web (frame demi frame, catatan langsung di frame) dan merender ke MP4 di Chrome headless. Tidak ada model AI yang membuat gambar atau video.

## Kapan motlab, kapan animate

* **motlab**: ada referensi pengguna (logo, screenshot app, foto produk, footage, musik), iklan/promo/brand, edit footage, atau pengguna menyebut motlab. Pengguna mereview di viewer dan menekan Export sendiri.
* **animate**: animasi cerita atau explainer murni dari teks, dalam gaya bawaan, yang kamu render sendiri ke MP4.
* Kalau keduanya termuat, pilih satu menurut aturan di atas dan katakan dalam satu baris.

## Langkah di SquidWings

1. Baca panduan lengkap sekali: `load_skill(name="motlab", file="panduan.md")`. Ikuti alurnya (brief, rencana shot, bangun semua scene, cek stills, buka viewer, revisi dari catatan). Referensi teknis ada di `references/*.md`, ambil yang perlu saja (`craft.md` selalu, `style-rules.md` untuk iklan, `footage.md` untuk footage, `sound-and-music.md` untuk audio).
2. Pasang salinan template dan skrip ke chat ini: `load_skill(name="motlab", install_files=true)`. Jalankan skrip dari `installed_at`.
3. Cek mesin: `execute_command("motlab --version")`. Kalau keluar versi, jalankan `motlab --help` sekali. Kalau `motlab` tidak ditemukan, katakan ke pengguna bahwa skill ini butuh engine-nya (Node 24 atau lebih baru, Google Chrome atau Chromium, dan ffmpeg), lalu minta dia menjalankan dua perintah ini di terminalnya sendiri: `npm install -g @motlab/cli` (tanpa sudo; npm di nvm), lalu `npx @motlab/cli setup --skill none` (skill-nya sudah ada di sini, jadi setup cukup memasang perintahnya). Setelah itu cek `motlab doctor` bersama dia. Kalau `motlab --version` malah mencetak error atau bantuan, engine-nya lebih tua dari skill ini: minta dia menjalankan `motlab update`.
4. Skrip Python butuh numpy, scipy, Pillow (`requirements.txt`).
5. **Saat mengerjakan catatan**, tandai dulu dengan `motlab working <folder> --note 3,5 "<apa yang dikerjakan>"`, lalu `motlab working <folder> --done` setelah selesai. Viewer memuat ulang scene yang disimpan sendiri, jadi pengguna tidak perlu me-reload.
6. **Jangan render sendiri.** `motlab stills` untuk mengecek hasil kerjamu boleh; `motlab render`/`motlab video` hanya kalau pengguna memintanya dengan jelas di chat. Buka viewer dengan `motlab edit <folder>` dan berikan alamatnya.
7. Desain: ikuti `style_pick` dan aturan `aislop` kecuali referensi/brand pengguna sudah menentukan tampilannya.
