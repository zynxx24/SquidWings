# Cara kerja agent

`agent/harness/` memindahkan disiplin kerja dari model ke loop:

| Lapisan | Fungsi |
|---|---|
| `tiers.py` | Membagi model ke lite, standard, dan frontier, dengan pengawasan lebih ketat untuk tier murah |
| `tool_select.py` | Menawarkan tool yang relevan saja, sehingga skemanya hemat token |
| `guards.py` | Memperbaiki argumen rusak, memangkas hasil panjang, dan mendeteksi loop |
| `verifier.py` | Memeriksa file dan tes sebelum jawaban diterima |
| `advisor.py` | Meminta saran model lebih kuat saat macet, lalu menyerahkan tugasnya bila perlu |
| `playbooks.py`, `briefing.py` | Menyimpan cara kerja yang berhasil dan memakainya lagi |

Panduan kerja ada di `brain/skills/`: `fix-bug`, `add-feature`, `clean-code`, `web-research`, `design-ui`, `build-web`, `build-backend`, `teman-curhat`, `jawab-tugas`, `baca-dokumen`, dan `animate`. Tiap panduan utama berisi bagian alur berpikir, supaya model murah tahu urutan menalarnya, bukan cuma aturannya. `teman-curhat` dipakai saat orang butuh didengar, dan `jawab-tugas` membuat jawaban tugas terdengar seperti tulisan orang. Baris `tasks:` membatasi skill ke jenis tugas tertentu, jadi kata "bingung" di permintaan coding tidak memuat panduan curhat.

Skill besar berupa folder dengan `SKILL.md` dan file pendamping. `animate` (diadaptasi dari [cth9191/animate](https://github.com/cth9191/animate), MIT) membuat video animasi pendek sepenuhnya dari kode, dengan gaya cut paper, crosshatch, riso, sketchbook, math, pixel, atau isometric. Agent memasang kit-nya ke `.skills/<sesi>/animate/`, satu salinan per sesi supaya dua chat di proyek yang sama tidak saling campur, lalu bertanya soal cerita, tampilan, dan storyboard sebelum merender. Butuh Node 18+, ffmpeg, dan Chromium untuk Playwright (`npx playwright install chromium`).

Skill punya dua jenis:

| Jenis | Kapan aktif |
|---|---|
| Utama (`scope: core`, bawaan) | Selalu ada di daftar skill. Panduannya ditambahkan saat permintaan cocok |
| Kontekstual (`scope: contextual`) | Diam, tidak ada di prompt sama sekali, sampai permintaan cocok dengan `triggers` dan `context`-nya. Setelah itu tetap aktif sampai sesi itu selesai, dan hanya di sesi itu |

`animate` termasuk kontekstual. "Buat video animasi 30 detik" membangunkannya, sedangkan "tambah animasi hover di tombol" tidak, karena tidak menyebut video. Memanggil `load_skill` dengan namanya juga membangunkannya.

Skill tambahan bisa ditaruh di folder `SKILLS_DIR`, dan skill yang tidak dipakai bisa dimatikan dengan `SKILLS_DISABLED=animate,build-web`.
