# input/

Pengaturan input yang diterima AI: apa yang dikirim ke model, seberapa panjang, dan bagaimana model diminta berpikir.

| Folder | Isi |
|---|---|
| `context/` | Menyusun dan meranking konteks (file, memori, hasil tool) sebelum dikirim |
| `max_token/` | Batas token jawaban per model dan per jenis tugas |
| `generate_efficient_token/` | Anggaran input, cache prompt, penghitung token, pemangkas pesan |
| `prompt_must_think/` | Cara berpikir per jenis tugas (`for_code.txt`, `for_math.txt`, ...) |
| `styles/` | `styles.yaml` (31 arah desain) dan `genome.yaml` (kombinasi font, warna, layout) untuk `style_pick` |
| `think/` | `lenses.yaml`: cara berpikir lebih luas per bidang (desain, kode, matematika, tulisan, strategi, data): jawaban autopilot yang harus dikalahkan, sumbu variasi, lensa, dan cara membuktikan |

Mau mengubah gaya jawaban untuk satu jenis tugas: sunting file `.txt` di `prompt_must_think/`. Mau AI berpikir ke arah yang lebih beragam: tambah lensa atau sumbu di `think/lenses.yaml` (berlaku tanpa restart, `THINK_WIDER=off` mematikannya). Prompt sistemnya sendiri ada di `brain/system_prompt/`. Keduanya berlaku di permintaan berikutnya tanpa restart.

## Hemat token

- **Tanpa batas buatan.** Riwayat dan pesan tidak dibatasi harness: jendela model (1 juta token; `DEFAULT_CONTEXT_WINDOW` untuk model yang tidak menyebut ukurannya) yang menentukan, jadi sesi panjang yang butuh banyak putaran tetap ingat semuanya. Riwayat yang tetap tidak muat diringkas, bukan dibuang diam-diam. `HISTORY_MAX_TOKENS` atau `HISTORY_MESSAGES` > 0 memasang batas kalau suatu saat diperlukan.
- **Prompt caching.** Prompt sistem dan riwayat lama ditandai `cache_control` untuk provider yang mendukung (Claude, Gemini, GPT); estimasi token yang ter-cache dicatat di log.
- **Anggaran input/output proporsional** per jenis tugas (`generate_efficient_token/input_budget.py`), jadi jawaban selalu punya ruang.
