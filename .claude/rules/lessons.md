# Pelajaran dari proyek sebelumnya

## Video "Hikayat Antrean" (2026-11-27)

- **Narasi ditulis setelah video jadi, jadi isinya beda dengan teks di layar.** Sejak itu berlaku aturan `video-script-first.md`.
- **Model TTS membacakan semua teks prompt**, termasuk instruksi gaya ("bacakan dengan hangat ..."). Arahan gaya harus di bagian `## Director's notes`, di atas `## Transcript:`. Itu format AI Studio yang dipakai `brain/utils/gemini_tts.py`.
- **Model TTS kadang mengucapkan satu baris dua kali.** Setiap baris dicek dengan whisper sebelum dicampur.
- **Kuota gratis Gemini TTS dihitung per key dan per model** (sekitar 10 permintaan per hari). Pakai router di `gemini_tts.py` dengan beberapa key. Jangan menunggu `retryDelay` harian (bisa belasan jam).
- **Lyria (musik Gemini) tertutup di paket gratis** (limit 0). Katalog musik HeyGen perlu login. Rencanakan sumber musik sejak awal: lagu milik pengguna, katalog, atau sintesis lokal.
- **Jeda antar-kalimat terasa kosong tanpa backsound.** Video bernarasi direncanakan dengan musik yang mengecil (ducking) saat narasi bicara.
- **Pekerjaan panjang (render, batch TTS) dijalankan di background.** Jangan memblokir sesi dengan loop menunggu.
- **Konten hukum:** kutip pasal dari teks resmi (JDIH, peraturan.go.id), tulis apa adanya, dan beri tanda `[...]` untuk bagian yang dipotong.
