# Add-on Studio video

Editor video motion di web (klip, keyframe dengan editor kurva, transisi, koreksi warna, chroma key, LUT, mask, track, potong di playhead, catatan di frame, chat revisi) dengan **render engine C di server**. Mati bawaan.

## Pasang

```bash
python main.py addon install studio
```

Perintah ini memasang `ziglang` (compiler C dari pip, juga untuk Windows tanpa Visual Studio), mengompilasi engine `engine/sqengine.c`, mengecek ffmpeg, lalu menyalakan add-on. Restart `python main.py web` sesudahnya.

## Engine render (C)

| Bagian | Isi |
|---|---|
| `engine/sqengine.c` | Rasterizer anti-alias sendiri, teks (stb_truetype), path SVG, stroke, blur, gambar, klip video, transisi, warna, chroma key, LUT .cube, mask, easing cubic-bezier, mixer audio, pool thread lintas OS |
| `engine/vendor/` | stb_truetype, stb_image, stb_image_write (public domain / MIT) |
| `renderer.py` | Kompilasi engine, motion.json ke scene, pilih encoder GPU, ekspor dan still |
| `fonts.py` | TTF Google Fonts yang sama dengan editor (cache bersama), font sistem kalau offline |
| `captions.py` | Caption dari suara (Whisper, waktu per kata) |

Ekspor jalan di mesin server, bukan di browser: HP boleh tidur, render tetap jalan, dan editor yang dibuka lagi langsung menampilkan progresnya. Hasil ekspor sama persis dengan pratinjau karena aturannya mengikuti `web/static/render.js`.

| Pengaturan `.env` | Arti |
|---|---|
| `STUDIO_GPU` | `auto` (bawaan: NVENC, Quick Sync, VAAPI, VideoToolbox, atau AMF yang lolos tes encode), `off` (CPU/libx264), atau nama encoder |
| `STUDIO_THREADS` | Thread render (bawaan: semua core kecuali satu) |
| `STUDIO_MEM_MB` | Batas memori engine (bawaan 2048); thread dikurangi supaya muat |
| `STUDIO_NICE` | `1` (bawaan): render berprioritas rendah supaya mesin tetap lancar |

Status: `GET /api/studio/engine` (engine siap, GPU yang terdeteksi, thread, batas memori).

## Efek dan sinkron

* Efek (transisi, warna, chroma key, LUT, mask) dihitung dengan rumus yang sama di `render.js` (pratinjau) dan `sqengine.c` (ekspor). Kalau satu diubah, ubah yang lain juga; `tests/test_studio_engine.py` membandingkan keduanya lewat node.
* Klip video besar (lebih dari 960 px atau 40 MB) dibuatkan **proxy** di `proxies/` saat diunggah: 960 px, keyframe tiap setengah detik, supaya editor lancar di HP. Tombol Proxy di bawah pratinjau mematikannya. Ekspor dan still selalu memakai file asli.
* Editor tidak polling: `GET /api/studio/{sesi}/events?project=` adalah satu aliran Server-Sent Events yang mengirim stamp proyek (simpanan AI atau tab lain), progres render, dan progres proxy. HP yang tidur memutus aliran; browser menyambung lagi sendiri.

## Langkah manual

| OS | Yang perlu dilakukan sendiri |
|---|---|
| Windows | Tidak ada yang wajib. ffmpeg bawaan imageio-ffmpeg dipakai kalau belum ada `winget install Gyan.FFmpeg`. GPU NVIDIA/Intel/AMD otomatis kalau driver terpasang |
| Linux | `sudo apt install ffmpeg`; GPU Intel/AMD: `intel-media-va-driver` atau `mesa-va-drivers` |
| macOS | `brew install ffmpeg`; VideoToolbox otomatis |

## Isi folder

| Folder | Isi |
|---|---|
| `studio_doc.py`, `studio_context.py` | Dokumen motion.json dan konteks untuk agent |
| `harness_studio.txt` | Protokol chat revisi di dalam editor |
| `web/` | API Studio (proyek, aset, render server, still, proxy, aliran events) dan halaman editor (`web/static/`) |
| `tools/` | Tool agent `studio_*`, termasuk `studio_still` (engine) dan `studio_captions` |
| `skills/` | Skill `studio` |

Matikan lagi dengan `python main.py addon remove studio`.
