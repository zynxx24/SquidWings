---
name: voice-over
description: Voice-over untuk video - naskah suara bertimestamp (mic.txt), trek suara yang tiap barisnya jatuh tepat di detik adegannya (Gemini TTS yang bisa diarahkan, ElevenLabs, atau Edge gratis), musik yang mengecil di bawah suara, dan video yang sudah dirender diberi suara tanpa render ulang.
triggers: voice over, voice-over, voiceover, isi suara, suara video, narasi, narator, dubbing, sulih suara, naskah suara, mic.txt, tts, text to speech, gemini tts, suara ai, video tanpa suara, belum ada suara, kasih suara, tambah suara, bacakan naskah
scope: contextual
context: video, reels, tiktok, shorts, promo, iklan, explainer, remotion, hyperframes, studio, render, mp4, naskah, script, narasi
companion: video-audio, animate, aislop
tools: voice_track, video_add_voice, voice_speak, voice_list, transcribe, video_check, video_render, read_file, file_write, file_patch, share_file
---
# Voice-over untuk video

Video promosi, explainer, dan konten sosial **selalu diberi suara**, kecuali pengguna minta musik saja. Video yang sudah jadi tanpa suara tapi punya naskah (misalnya `mic.txt`) diberi suara dengan langkah di bawah, tanpa membangun ulang videonya.

## 1. Naskah: format `mic.txt`

Satu file teks di folder proyek. Kepala berisi gaya; tiap baris naskah punya rentang detik yang sama dengan adegan dan teks di layarnya.

```text
NASKAH SUARA - PROMO <NAMA>
Durasi total : 60 detik (00.00 - 01.00)
Gaya suara   : hangat, santai, kayak ngobrol sama teman. Jangan kayak iklan.
               Senyum pas ngomong.

[00.00 - 00.04] [pelan] Jujur ya... (↓) aku capek bayar langganan tiap bulan.
[00.04 - 00.08] Terus kepikiran, (↑) gimana kalau jalan di laptop sendiri?
[00.08 - 00.12] (...) Ternyata ada. [senyum] (↑) Namanya *SquidWings*.

------------------------------------------------------------------
CATATAN UNTUK GENERATOR SUARA
- "SquidWings" dibaca: skuid-wings.
- "QRIS" dibaca: kris.
```

- Rentang ditulis `[mm.ss - mm.ss]` (atau `[mm:ss - mm:ss]`, seperti di AI Studio). Format AI Studio dengan teks di baris bawah timestamp juga terbaca.
- Tanda cara bicara dibaca sebagai arahan dan **tidak diucapkan**:
  - `(↑)` nada naik, `(↓)` nada turun, `(~)` nada mengayun;
  - `(...)` jeda pendek, `(//)` jeda panjang;
  - `*kata*` untuk penekanan;
  - `[pelan]`, `[senyum]`, `[semangat]`, `[bisik]`, atau kata lain dalam kurung siku, untuk suasana baris itu.
- `Gaya suara :` menjadi arahan untuk semua baris. Baris yang menjorok di bawahnya ikut menjadi bagian gaya.
- `"Nama" dibaca: bunyi.` mengatur pengucapan. Caption tetap memakai ejaan aslinya.
- **Muat di rentangnya.** Bahasa Indonesia santai kira-kira 2,5 kata per detik, jadi rentang 4 detik muat sekitar 10 kata. Angka dan singkatan ditulis seperti diucapkan ("lima persen", "dua belas PPLG").
- Teks di layar dan potongan adegan memakai rentang yang sama dengan naskah. Di Remotion, `script.ts` berisi `lines` yang sama dengan `mic.txt`.

## 2. Trek suara: `voice_track`

```
voice_track(script="mic.txt", output="videos/<proyek>/public/voice.wav", total_seconds=60)
```

- Setiap baris diucapkan, heningnya dipangkas, lalu diletakkan di detik awal rentangnya dalam **satu trek sepanjang video**. Hasilnya tidak melenceng di akhir video.
- Baris yang lebih panjang dari rentangnya dipercepat paling banyak 1,3x.
- Hasil yang ditulis: `voice.wav`, `voice.srt`, dan `voice.words.json`.
- Baca `warnings` di hasilnya: rentang terbalik, tumpang tindih, lewat panjang video, atau baris yang kata-katanya tidak muat. Perbaiki `mic.txt` dulu kalau ada.
- Baca `spills_over` dan `shifted`. Baris yang tetap kepanjangan tidak pernah menimpa baris berikutnya; baris berikutnya mundur sedikit dan tercatat di `shifted`. Pendekkan teksnya atau lebarkan rentangnya, lalu ulangi.
- Trek **didengar ulang otomatis** (`check=true`, whisper lokal). Baris yang tidak terdengar, terdengar lain, atau katanya kepotong ke baris tetangga diucapkan ulang satu per satu, sekali, lalu trek disusun lagi. Hasilnya di `check`, `repaired`, dan `ok_audio`.
- Volume antarbaris disamakan, jadi baris yang diminta terpisah tidak ada yang lebih keras atau lebih pelan.

**Engine** (`engine="auto"` memilih ElevenLabs, lalu Gemini, lalu Edge):

| Engine | Kapan | Catatan |
|---|---|---|
| `gemini` | Suara yang mengikuti arahan gaya dan nada (suara `Kira`, `Kore`, `Puck`, `Charon`...) | Paket gratis: **3 permintaan per menit, 10 per hari**. `mode="whole"` (bawaan bila whisper terpasang) mengucapkan seluruh naskah dalam **1 permintaan**, lalu memotongnya per baris dengan whisper. Rekaman utuhnya disimpan di `voice.take.wav`: `mode="recut"` memotong ulang tanpa permintaan baru. |
| `elevenlabs` | Pengguna punya key ElevenLabs | Berbayar per karakter |
| `edge` | Gratis tanpa batas, tanpa key (`id-ID-GadisNeural`, `id-ID-ArdiNeural`) | Tidak bisa diarahkan gaya; pakai saat kuota Gemini habis |

**Routing Gemini.** Isi Gemini API key di Kredensial boleh beberapa key, dipisah koma. Kuota gratis dihitung per key per model, jadi setiap permintaan dikirim ke pasangan (key, model) yang masih segar:
- Kena batas per menit: key lain di model yang sama dipakai langsung. Kalau semua key sedang kena, tunggu jedanya di model yang sama, supaya satu trek tetap satu warna suara.
- Kena batas harian: key itu diistirahatkan untuk model tersebut. Key lain tetap dipakai, dan baru pindah ke model berikutnya (`gemini-3.8-flash-tts`, lalu `gemini-3.8-flash-lite-tts`, `gemini-3.1-flash-tts-preview`, `gemini-2.5-flash-preview-tts`) setelah semua key habis di model itu.
- Key yang ditolak (401/403) diistirahatkan 30 menit kalau masih ada key lain.
- Arahan gaya ditulis di bagian `## Director's notes` dan tidak ikut diucapkan. Jangan menaruh instruksi di dalam teks yang dibacakan, karena model TTS akan ikut membacanya.

Kalau Gemini menjawab kuota harian habis di semua key dan model, katakan apa adanya ke pengguna. Tawarkan menunggu besok atau memakai `engine="edge"` sekarang. Jangan diam-diam pindah engine.

## 3. Masukkan ke video

**Remotion.** Taruh trek di `public/`, lalu di komposisi utama:

```tsx
{/* Musik mengecil di setiap baris naskah, naik lagi di sela-selanya. */}
<Audio src={staticFile("musik.wav")}
       volume={(f) => (lines.some((l) => f >= l.from * FPS && f < l.to * FPS) ? 0.32 : 0.7)} />
<Audio src={staticFile("voice.wav")} />
```

Caption bisa memakai `voice.words.json` atau rentang di `lines`. Setelah itu jalankan `video_check`, lalu `video_render`.

**HyperFrames.** Trek masuk sebagai audio komposisi; baca `video_docs("audio")`. **Studio**: tambahkan sebagai klip audio.

**Video yang sudah dirender**, supaya cepat dan tanpa render ulang:

```
video_add_voice(video="renders/<nama>.mp4", voice="videos/<proyek>/public/voice.wav")
```

Musik bawaan video mengecil saat ada suara (sidechain). Hasilnya `renders/<nama>_suara.mp4`. Video asli tidak pernah ditimpa. Kalau suaranya masih bicara setelah videonya habis, `warnings` menyebut detiknya.

## 4. Cek sebelum bilang selesai

- `ok_audio: true` artinya setiap baris terdengar di tempatnya. Kalau `false`, baca `note`: nama atau angka yang salah dengar oleh whisper ("App Cache", "5 %") boleh diabaikan; kata yang pindah baris atau baris yang hilang tidak.
- Memeriksa sendiri dengan `transcribe`: sebuah kata milik baris tempat kata itu **berakhir**. Awal kata dari whisper sering terlalu dini (sampai satu detik), masuk ke jeda atau ke baris sebelumnya.
- `repair_failed` berisi alasan kalau pengucapan ulang gagal (misalnya kuota habis). Treknya tetap tersimpan; katakan apa yang belum beres.
- Dengarkan 15 detik pertama hasil gabungan (`transcribe` pada potongan video): suara harus jelas di atas musik.
- Laporkan engine dan suara yang dipakai, baris yang dipercepat, dan apa yang belum sempurna.
