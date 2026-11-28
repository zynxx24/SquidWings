---
name: video-script
description: Naskah final dulu, baru video - satu file naskah berisi detik, teks di layar, dan narasi per adegan, yang dibaca oleh video dan generator suara sehingga keduanya tidak pernah berbeda. Pakai sebelum membuat video apa pun (Reels, explainer, promo, animasi, HyperFrames, Remotion, Studio).
---

# Naskah final dulu

Urutannya tetap: **naskah, cek muat, suara, video, cek**. Video tidak pernah dibangun sebelum naskahnya final.

## 1. Format `naskah.md`

```markdown
# <Judul>
Durasi: 60 dtk · 1080x1920 · Bahasa: Indonesia
Gaya suara: hangat, mengajak, kayak ngobrol sama teman
Musik: <sumber yang PASTI tersedia: lagu pengguna / katalog / sintesis lokal>, mengecil saat narasi

| # | Detik | Teks di layar | Narasi |
|---|---|---|---|
| 1 | 00.0-04.5 | Antrean yang Diserobot | Pernah antre lama, eh malah diselak orang? |
| 2 | 04.5-09.0 | Nomor 27 | Bu Komang datang pagi-pagi. Nomornya dua puluh tujuh. |
```

- **Teks di layar** adalah kata-kata yang muncul di video. **Narasi** adalah yang diucapkan. Narasi membacakan teks di layar, atau teks di layar memuat potongan kata demi kata dari narasi.
- Angka dan singkatan di kolom narasi ditulis seperti diucapkan ("dua puluh tujuh", "seratus enam puluh lima").
- Sumber fakta (pasal, data, harga) dicatat di bawah tabel, lengkap dengan tautan resmi.

## 2. Cek muat (sebelum kuota dipakai)

- Kira-kira 2,5 kata per detik untuk bahasa Indonesia santai. Hitung kata narasi dibanding panjang rentangnya.
- Teks di layar: maksimal sekitar 3 kata per detik untuk dibaca. Teks yang panjang diberi rentang lebih lebar.
- Yang tidak muat diperbaiki **di naskah**. Jangan dipaksakan dengan mempercepat suara.

## 3. Suara dari naskah

- Harness: ubah tabel menjadi `mic.txt` (`[mm.ss - mm.ss] narasi`), lalu `voice_track`. Gemini TTS otomatis dirouting ke key dan model yang masih segar.
- Arahan gaya ditaruh di `## Director's notes`, tidak pernah di dalam teks yang dibacakan.
- Dengarkan ulang dengan whisper: setiap baris harus terucap sesuai naskah, tanpa terucap ganda atau terpotong.

## 4. Video dari naskah

- Komposisi **membaca** `naskah.md` (atau JSON hasil turunannya) untuk teks dan detiknya. Jangan menyalin teks dengan tangan.
- Durasi adegan sama dengan rentang di naskah.
- Musik mengecil saat narasi bicara, supaya jeda antar-kalimat tidak terasa kosong.

## 5. Cek akhir

- Snapshot setiap adegan, lalu bandingkan teksnya dengan kolom teks di layar.
- Transkrip suara dibandingkan dengan kolom narasi.
- Naskah berubah, berarti suara dan video dibangun ulang dari naskah.
