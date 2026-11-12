---
name: video-audio
description: Video dan audio - unduh video, audio, atau subtitle dari link (yt-dlp), transkripsi suara ke teks secara lokal (faster-whisper), dan bikin video dari kode lalu render ke MP4 (HyperFrames untuk HTML, Remotion untuk React).
triggers: youtube, unduh video, download video, download audio, unduh lagu, mp3, subtitle, subtitel, takarir, transkrip, transkripsi, transcribe, transcript, speech to text, suara ke teks, whisper, yt-dlp, hyperframes, remotion, render video, bikin video, buat video, video intro, video explainer, video promosi, caption video, video vertikal, reels, shorts
scope: contextual
context: video, audio, mp4, mp3, link, youtube, youtu.be, tiktok, instagram, rekaman, podcast, wawancara, rapat, kuliah, intro, explainer, caption, subtitle, render, klip, clip, reels, shorts, durasi, detik, menit
tools: media_tools_status, media_info, media_download, transcribe, video_new, video_docs, video_check, video_render, share_file, read_file, file_write, file_patch, style_pick, voice_speak, aislop_scan
---
# Video dan audio

Semua kerja video dan audio lewat tool di bawah. Jangan bilang sesuatu tidak bisa sebelum memanggil `media_tools_status`: tool itu menunjukkan apa yang terpasang dan perintah pasang untuk yang belum ada. Sampaikan perintah itu apa adanya, lalu berhenti. Jangan berpura-pura sudah mengunduh, mentranskripsi, atau merender.

## Dari link (YouTube dan ±1800 situs lain)

1. `media_info(url)` dulu: judul, durasi, kualitas, dan bahasa subtitle yang tersedia. Durasinya menentukan berapa lama langkah berikutnya.
2. Butuh **isi pembicaraannya** (ringkas, kutip, terjemahkan): kalau `subtitles` atau `auto_subtitles` punya bahasa yang dibutuhkan, `media_download(url, kind="subtitles")` lalu baca file `.srt`. Itu jauh lebih cepat daripada transkripsi. Kalau tidak ada subtitle: `media_download(url, kind="audio")` lalu `transcribe`.
3. Butuh **filenya**: `kind="video"` (MP4, `max_height` 720 cukup untuk kebanyakan keperluan) atau `kind="audio"` (MP3). `share=true` untuk menampilkannya di chat.
4. Hanya satu video per panggilan, tidak pernah satu playlist. Hanya untuk konten yang boleh diunduh pengguna: miliknya sendiri, berlisensi bebas, domain publik, atau diizinkan situsnya. Kalau jelas melanggar, tolak dengan sopan dan tawarkan ringkasan dari subtitle.

## Transkripsi (lokal, tidak ada yang dikirim keluar)

- `transcribe(path)` untuk file audio atau video di workspace. Hasilnya `.txt` dan `.srt` di sebelah file, plus teks, bahasa, dan durasi.
- Bahasa Indonesia: isi `language="id"`. Kalau hasilnya kurang akurat, ulangi dengan `model="small"` (lebih lambat, lebih tepat).
- `words=true` memberi waktu tiap kata (`.words.json`) untuk caption yang mengikuti suara.
- Di CPU, audio panjang butuh waktu, dan model yang lebih besar lebih lambat. Untuk file di atas 10 menit, bilang dulu ke pengguna.

## Bikin video dari kode

Pilih engine:

| Butuh | Engine |
|---|---|
| Judul, intro, explainer, caption, chart, klip sosial (vertikal), video dari naskah atau audio | **HyperFrames** (bawaan) |
| Proyeknya sudah React, komponen yang dipakai ulang, scene dari data, atau pengguna menyebut Remotion | **Remotion** |
| Animasi gaya ilustrasi dengan musik yang disintesis | skill `animate` |

Alur:

1. `video_new(name="videos/<nama>", engine=...)`. HyperFrames: `resolution="portrait"` untuk Reels/Shorts/TikTok, `audio="<file>"` untuk membangun video dari narasi (ditranskripsi untuk caption).
2. **HyperFrames:** baca `video_docs("data-attributes")` dan `video_docs("compositions")`, lalu `AGENTS.md` di folder proyek, sebelum menulis `index.html`. Animasi memakai GSAP (`video_docs("gsap")`), audio lihat `video_docs("audio")`. **Remotion:** komposisi didaftarkan di `src/Root.tsx`, komponennya di `src/`. Kalau perlu referensi, `web_read` ke https://www.remotion.dev/docs.
3. Gaya visual: `style_pick(medium="video")` supaya tiap video terlihat berbeda. Narasi: `voice_speak` (ElevenLabs) lalu masukkan MP3-nya sebagai audio.
4. `video_check(project)`: perbaiki setiap error. Untuk Remotion, ini juga mendaftar id komposisinya.
5. Bilang ke pengguna bahwa render dimulai, lalu `video_render(project)`. Untuk Remotion dengan beberapa komposisi, isi `composition`. Hasilnya `renders/<nama>.mp4` dan langsung tampil di chat.
6. Jalankan `aislop_scan` untuk kode yang kamu tulis, dan perbaiki temuannya.

Kalau render gagal, baca `log_tail`, perbaiki penyebabnya, dan render lagi. Jangan menurunkan kualitas hanya untuk menyembunyikan error.
