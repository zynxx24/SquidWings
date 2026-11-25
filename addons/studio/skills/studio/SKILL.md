---
name: studio
description: Video motion yang bisa diedit di Studio web - layer teks, bentuk, path, gambar dengan keyframe, musik dengan fade dan volume, catatan revisi dari pengguna di frame, lalu ekspor MP4 dari editor.
triggers: studio, studio video, editor video, video editor, keyframe, keyframes, timeline video, motion.json, catatan video, revisi video, video motion, motion video, edit video, bisa diedit, intro video, lower third, animasi teks, kinetic typography
scope: contextual
context: video, motion, animasi, animation, studio, keyframe, timeline, reels, tiktok, shorts, youtube, iklan, promo, intro, mp4, catatan, revisi, detik, durasi, musik
tools: studio_create, studio_get, studio_save, studio_notes, studio_note_done, studio_list, style_pick, load_skill, share_file
---
# Studio: video motion sebagai data

Video di Studio adalah satu file `studio/<nama>/motion.json`. Kamu menulisnya, pengguna memutar dan mengeditnya di halaman Studio (menu "Studio video" di web), menggeser keyframe, mengatur musik, dan menaruh **catatan bernomor** di frame. Ekspor MP4 dilakukan pengguna dari Studio (tombol Ekspor MP4). Kamu dan editor menulis file yang sama, jadi selalu pakai `stamp`.

## Kapan Studio, kapan skill lain
* **Studio**: pengguna ingin bisa mengedit sendiri, memberi catatan di frame, atau video teks/bentuk/gambar dengan gerak (promo, intro, lower third, kinetic type, explainer sederhana).
* **motlab**: iklan dari footage dan referensi yang berat, render engine kode. **animate**: film animasi bercerita yang kamu render sendiri.

## Alur
1. Arah desain: `style_pick("video", subjek)`, lalu pakai palet dan font-nya (font Google ke `fonts`).
2. `studio_create(nama, width, height, fps, duration)` (9:16 = 1080x1920, 16:9 = 1920x1080, 1:1 = 1080x1080).
3. Tulis video lengkap dengan `studio_save(nama, doc, stamp)`. Kalau ada error validasi, perbaiki bagian yang disebut lalu simpan lagi.
4. Beri pengguna link `open` (mis. `/studio?project=promo-kopi`) dan rangkum adegannya dalam beberapa baris. Kamu tidak bisa menonton hasilnya: katakan itu, dan minta catatan di frame.
5. Saat pengguna mengirim catatan: `studio_notes(nama)`, lalu `studio_get` (untuk stamp), ubah dokumen, `studio_save`, lalu `studio_note_done(nama, n, jawaban)` untuk tiap catatan. Jawaban satu-dua kalimat: apa yang diubah.
6. Musik: file harus ada di `studio/<nama>/assets/` (pengguna mengunggah lewat Studio, atau buat dengan tool suara/musik lalu pindahkan ke sana). Gambar juga di `assets/`.

## Format motion.json
```json
{"width":1080,"height":1920,"fps":30,"duration":12,"background":"#F4EBDD","fonts":["Fraunces","Plus Jakarta Sans"],
 "layers":[
  {"id":"judul","type":"text","name":"Judul","start":0,"end":4,"text":"Kopi Senja","font":"Fraunces","weight":700,"size":130,
   "fill":"#2B1B12","align":"center","props":{"x":540,"y":820,"scale":1,"rotation":0,"opacity":1},
   "keys":{"opacity":[{"t":0,"v":0,"ease":"out"},{"t":0.6,"v":1}],"y":[{"t":0,"v":880,"ease":"outBack"},{"t":0.6,"v":820}]}},
  {"id":"garis","type":"path","d":"M0 20 C 60 0, 120 40, 180 20","w":180,"h":40,"fill":null,"stroke":"#C8553D","strokeWidth":6,
   "props":{"x":540,"y":930},"keys":{"draw":[{"t":0.5,"v":0,"ease":"inOut"},{"t":1.4,"v":1}]}}],
 "audio":[{"id":"musik","src":"assets/musik.mp3","start":0,"offset":0,"volume":0.8,"fadeIn":0.5,"fadeOut":1.5}],
 "notes":[]}
```
* Tipe layer: `text` (text, font, weight, size, align, fill, stroke, strokeWidth, lineHeight, width untuk bungkus), `rect` (w, h, radius), `ellipse` (w, h), `polygon` (radius, sides, star, inner), `path` (d SVG dalam kotak w×h), `image` (src "assets/x.png", w, h, fit contain/cover, radius). Semua punya fill/stroke/strokeWidth kecuali image.
* `props`: x, y (titik tengah layer, piksel video), scale, rotation (derajat), opacity, blur, draw (0-1: garis tergambar, teks terketik).
* `keys`: properti yang bisa dianimasikan: x, y, scale, scaleX, scaleY, rotation, opacity, w, h, radius, size, strokeWidth, draw, blur, fill, stroke. Tiap keyframe `{"t": detik, "v": nilai, "ease": ...}`; ease membentuk gerak dari keyframe itu ke berikutnya: linear, in, out, inOut, outBack, outElastic, hold.
* Urutan `layers` = bawah ke atas. `start`/`end` = kapan layer tampil. Audio: `start` di video, `offset` mulai dari lagu, `duration` opsional, volume 0-4, fadeIn/fadeOut detik, `keys.volume` untuk ducking.
* Catatan dari pengguna: `n`, `t` (detik), `end` opsional, `box` [x,y,w,h] atau `point` [x,y], `layer` (id di bawahnya), `text`, `status`. Jangan menghapus catatan; tandai selesai.

## Gerak yang bagus (aislop berlaku)
* Satu ide per adegan; tahan hasil akhir minimal 1 detik agar terbaca (sekitar 3 kata per detik).
* Gerak masuk 0,4-0,8 dtk dengan `out` atau `outBack`; keluar lebih cepat dengan `in`. Hindari semua elemen masuk bersamaan: geser 0,08-0,15 dtk per elemen.
* Ritme mengikuti musik: taruh ganti adegan di ketukan. Kontras teks dengan latar minimal 4,5:1 (`contrast_check`).
* Jangan zoom dan fade tanpa alasan di setiap adegan; jangan teks melayang terus. Isi nyata dari pengguna, bukan klaim atau angka karangan.
