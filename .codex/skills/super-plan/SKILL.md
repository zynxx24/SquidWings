---
name: super-plan
description: Rencana menyeluruh dari segala segi sebelum eksekusi. Pakai untuk setiap tugas lebih dari satu langkah - membangun aplikasi atau web, memperbaiki bug, mengubah arsitektur atau pengaturan, membuat video atau dokumen.
---

# Super plan

Rencana ditulis setelah memetakan kondisi nyata dan sebelum file pertama diubah. Setelah rencana selesai, langsung eksekusi. Jangan menunggu persetujuan, kecuali ada keputusan yang hanya bisa diambil pengguna.

## 1. Petakan (baca, jangan tebak)

- File, modul, dan konfigurasi yang akan disentuh, beserta pemanggilnya.
- Tes yang sudah ada dan cara menjalankannya.
- Aturan proyek: `CLAUDE.md` atau `AGENTS.md`, `.claude/rules/`, README per folder.
- Batasan: platform, kuota API, data pengguna, apa yang tidak boleh berubah.

## 2. Tulis rencana dengan templat ini

```
Tujuan        : satu kalimat; kriteria selesai yang bisa dicek (angka, perintah, tampilan)
Pemakai       : siapa, di perangkat apa, alur yang mereka jalani
Desain        : data, arsitektur, file yang dibuat/diubah, kenapa begitu (satu sumber kebenaran)
Kualitas      : keamanan / kualitas kode / keandalan / performa / maintainability / usability:
                apa yang dilakukan dan bagaimana dibuktikan (lihat skill quality-gate)
Risiko        : kasus pinggir, apa yang bisa rusak, penangkalnya
Langkah       : urutan kerja bernomor; tiap langkah punya cek
Verifikasi    : perintah tes/lint/build/snapshot yang dijalankan di akhir
Mundur        : cara membatalkan kalau gagal (git, backup, flag)
```

Untuk perubahan kecil, templat ini cukup beberapa baris. Untuk sistem baru, isi setiap bagiannya.

## 3. Eksekusi

- Kerjakan langkah demi langkah dan jalankan cek setiap langkah.
- Temuan baru yang mengubah rencana: perbarui rencana dalam satu kalimat, lalu lanjut.
- Pekerjaan panjang (render, build besar, batch API) dijalankan di background.

## 4. Tutup

Sebutkan apa yang berubah, cek apa yang dijalankan beserta hasilnya, dan apa yang tidak dicek.
