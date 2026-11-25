# Add-on Studio video

Editor video motion (klip, keyframe, catatan) dengan chat revisi, render MP4, gambar diam, dan tool `studio_*` untuk agent. Mati bawaan.

## Pasang

```bash
python main.py addon install studio
```

Perintah ini mengecek `ffmpeg` dan memasangnya lewat Homebrew kalau ada, menyalakan add-on, lalu menampilkan langkah manual untuk OS kamu. Restart `python main.py web` sesudahnya.

## Langkah manual

| OS | Yang perlu dilakukan sendiri |
|---|---|
| Windows | `winget install Gyan.FFmpeg`, dan Google Chrome atau Chromium untuk `studio_still` |
| Linux | `sudo apt install ffmpeg` (atau `brew install ffmpeg` tanpa sudo), dan Chromium/Chrome |
| macOS | `brew install ffmpeg`, dan Google Chrome |

## Isi folder

| Folder | Isi |
|---|---|
| `studio_doc.py`, `studio_context.py` | dokumen motion.json dan konteks untuk agent |
| `harness_studio.txt` | protokol chat revisi di dalam editor |
| `web/` | API Studio dan halaman editor (`web/static/`) |
| `tools/` | tool agent `studio_*` |
| `skills/` | skill `studio` |

Matikan lagi dengan `python main.py addon remove studio`.
