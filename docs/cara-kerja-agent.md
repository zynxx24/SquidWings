# Cara kerja agent

`agent/harness/` memindahkan disiplin kerja dari model ke loop:

| Lapisan | Fungsi |
|---|---|
| `tiers.py` | Membagi model ke lite, standard, dan frontier, dengan pengawasan lebih ketat untuk tier murah |
| `tool_select.py` | Menawarkan tool yang relevan saja, sehingga skemanya hemat token. Toolbox tersembunyi baru ditawarkan saat dibutuhkan |
| `guards.py` | Memperbaiki argumen rusak (nama salah, `"5"` untuk angka, `"a, b"` untuk list, `"1.250.000"`, `"ya"`), memangkas hasil panjang, dan mendeteksi loop |
| `verifier.py` | Memeriksa file dan tes sebelum jawaban diterima |
| `advisor.py` | Meminta saran model lebih kuat saat macet, lalu menyerahkan tugasnya bila perlu |
| `playbooks.py`, `briefing.py` | Menyimpan cara kerja yang berhasil dan memakainya lagi |

Panduan kerja ada di `brain/skills/`: `fix-bug`, `add-feature`, `clean-code`, `web-research`, `design-ui`, `build-web`, `build-backend`, `teman-curhat`, `jawab-tugas`, `baca-dokumen`, `google-maps`, `buat-dokumen`, `humanizer-indonesia`, dan `animate`. Tiap panduan utama berisi bagian alur berpikir, supaya model murah tahu urutan menalarnya, bukan cuma aturannya. `teman-curhat` dipakai saat orang butuh didengar, dan `jawab-tugas` membuat jawaban tugas terdengar seperti tulisan orang. Baris `tasks:` membatasi skill ke jenis tugas tertentu, jadi kata "bingung" di permintaan coding tidak memuat panduan curhat, dan daftar skill di prompt hanya memuat skill utama untuk jenis tugas itu: chat curhat tidak membawa daftar skill backend.

Skill besar berupa folder dengan `SKILL.md` dan file pendamping. `animate` (diadaptasi dari [cth9191/animate](https://github.com/cth9191/animate), MIT) membuat video animasi pendek sepenuhnya dari kode, dengan gaya cut paper, crosshatch, riso, sketchbook, math, pixel, atau isometric. Agent memasang kit-nya ke `.skills/<sesi>/animate/`, satu salinan per sesi supaya dua chat di proyek yang sama tidak saling campur, lalu bertanya soal cerita, tampilan, dan storyboard sebelum merender. Butuh Node 18+, ffmpeg, dan Chromium untuk Playwright (`npx playwright install chromium`).

`humanizer-indonesia` (diadaptasi dari [adhiprib/humanizer-indonesia](https://github.com/adhiprib/humanizer-indonesia) versi 1.7, MIT) menyunting teks bahasa Indonesia supaya luwes dan tidak terbaca seperti keluaran AI atau terjemahan, lengkap dengan katalog pola dan `scripts/metrik.py` untuk mengukur teks sebelum dan sesudah. Hasilnya draf untuk disunting penulis, bukan alat lolos detektor AI. Edisi Enterprise menambah `analis-trading`, yang membaca data server trading lewat tool `trading_snapshot` sebelum memberi pandangan soal posisi atau setup. Skill bertanda `edition: enterprise` tidak dimuat bila modulnya tidak terpasang.

Skill punya dua jenis:

| Jenis | Kapan aktif |
|---|---|
| Utama (`scope: core`, bawaan) | Selalu ada di daftar skill. Panduannya ditambahkan saat permintaan cocok |
| Kontekstual (`scope: contextual`) | Diam, tidak ada di prompt sama sekali, sampai permintaan cocok dengan `triggers` dan `context`-nya. Setelah itu tetap aktif sampai sesi itu selesai, dan hanya di sesi itu |

`animate`, `humanizer-indonesia`, `google-maps`, dan `buat-dokumen` termasuk kontekstual. "Buat video animasi 30 detik" membangunkannya, sedangkan "tambah animasi hover di tombol" tidak, karena tidak menyebut video. Memanggil `load_skill` dengan namanya juga membangunkannya.

`google-maps` bangun saat ada konteks tempat, misalnya "rekap hasil scrape google maps klinik gigi" atau "apotek terdekat dari Monas", tetapi tidak untuk "source maps webpack" atau "jarak antar tombol css". Ia membawa tool impor dan pencarian tempat, lalu membuang nomor telepon dan email saat impor kecuali pengguna jelas memintanya.

`buat-dokumen` bangun saat pengguna minta file, misalnya "buatkan makalah dalam bentuk word", "bikin ppt presentasi kelompok", "rekap ke excel", atau "jadikan pdf". Kontekstual dengan `tasks:` juga tetap tidur di jenis tugas lain, jadi "buat parser csv di python" (coding) tidak membangunkannya. Tool-nya membuat file lalu langsung menampilkannya di bawah jawaban sebagai tombol unduh.

Skill tambahan bisa ditaruh di folder `SKILLS_DIR`, dan skill yang tidak dipakai bisa dimatikan dengan `SKILLS_DISABLED=animate,build-web`.

## Toolbox: tool tepat yang tersembunyi

Model murah sering salah kalau menghitung atau mengonversi di kepala. `tool/execute/execute_toolbox/` berisi 127 tool kecil yang jawabannya pasti, dalam 16 grup:

| Grup | Contoh tool |
|---|---|
| `math` | `calc` (pecahan tetap eksak, `15%`, `5!`), `solve_equation`, `stats_describe`, `matrix`, `geometry`, `weighted_average` (IPK) |
| `units`, `time` | `unit_convert` (ons, kuintal, sdm), `date_diff`, `date_add` (hari kerja), `timezone_convert` (WIB/WITA/WIT), `cron_next`, `hijri_date`, `weton` |
| `text`, `encode` | `text_stats`, `regex_test`, `text_diff`, `extract_patterns`, `encode_decode`, `hash_text`, `password_generate`, `jwt_decode` |
| `data` | `table_inspect`, `table_query` (filter, group by, simpan ke Excel), `table_convert`, `json_tool`, `sqlite_query` (read-only), `format_convert` |
| `dev` | `url_parse`, `http_status`, `semver`, `ip_cidr`, `color_contrast` (WCAG), `syntax_check`, `python_inspect`, `dependencies_list` |
| `finance`, `indonesia` | `loan_payment` (anuitas, bunga flat, efektif), `compound_interest`, `npv_irr`, `zakat_calc`, `terbilang`, `rupiah`, `phone_id`, `plat_nomor` |
| `maps` | `gmaps_import`, `places_search`, `places_stats`, `places_export`, `geo_distance`, `maps_link`, `geocode`, `osm_nearby`, `gmaps_scrape` |
| `office` | `doc_create` (Word dari Markdown: judul, daftar, tabel, gambar, nomor halaman, margin akademik), `slides_create` (PowerPoint 16:9 dengan catatan pembicara), `sheet_create` (Excel dengan format Rupiah, total, rumus; atau CSV), `office_to_pdf` (lewat LibreOffice). Ketiganya memakai 20 tema (warna, font, gaya judul, dekorasi slide) plus `formal`; tanpa `theme`, setiap file memakai tema yang belum dipakai di chat itu maupun di chat lain akhir-akhir ini |
| `design` | `style_pick`: satu arah desain untuk web, slide, gambar, video, atau dokumen dari 31 arah di `brain/styles/styles.yaml` (palet hex, Google Fonts, tata letak, motif, gerak, resep gambar, yang dihindari). Memilih yang cocok dengan subjek dan melewati arah yang dipakai chat-chat terakhir (`.agent_memory/style_history.json`), jadi dua permintaan yang sama tampil beda |
| `voice` | `voice_list` (suara di akun ElevenLabs dan sisa kuota), `voice_speak` (teks jadi MP3 di workspace, misalnya voice-over video). Butuh ElevenLabs API key di tab Kredensial atau `ELEVENLABS_API_KEY`; tanpa itu keduanya menjelaskan cara menyambungkan |
| `tradecalc`, `health`, `live` | `position_size`, `risk_reward`, `drawdown_stats`, `bmi_calc`, `weather`, `currency_convert`, `public_holidays`, `wiki_summary` |

Tidak ada satu pun yang dikirim secara bawaan, juga ke model frontier, jadi prompt tetap hemat. Sebuah tool toolbox ditawarkan kalau:

1. kata-kata permintaan cocok dengan kata kunci tool itu (`cicilan kpr` membawa `loan_payment`, `cuaca bandung` membawa `weather`; "perbaiki bug login di api" tidak membawa apa pun),
2. skill kontekstual yang bangun menyebutnya di `tools:`,
3. model memanggil `find_tools("apa yang dibutuhkan")`. Tool yang ditemukan dibuka untuk sesi itu saja (`session.json`, paling banyak 12) dan langsung ditawarkan di permintaan berikutnya dalam run yang sama, atau
4. model memanggilnya langsung dengan nama, yang selalu jalan.

Tiap tool mengembalikan `{"ok": false, "error", "usage"}` kalau argumennya salah, jadi model bisa memperbaiki panggilannya. Tool yang menyentuh file hanya bisa membaca dan menulis di workspace sesi. Tool `live` dan `geocode`/`osm_nearby` butuh internet dan hanya menghubungi host publik yang terdaftar di `_net.py`, dengan cache per sesi. `gmaps_scrape` menjalankan [gosom/google-maps-scraper](https://github.com/gosom/google-maps-scraper) kalau terpasang (`GMAPS_SCRAPER_BIN` untuk lokasi lain); tanpa itu, unggah hasil ekspor lalu `gmaps_import`.

### Batas ukuran file

| Batas | Nilai bawaan | Variabel |
|---|---|---|
| File yang diunggah pengguna | 20 MB | `MAX_UPLOAD_MB` |
| File yang dibagikan agent ke chat (dokumen, slide, sheet, PDF, video) | 500 MB | `SHARE_MAX_MB` |
| Isi arsip ZIP yang dibaca | 100 MB | tetap |

File yang lebih besar dari `SHARE_MAX_MB` tetap tersimpan di folder sesi, tetapi tidak ditampilkan. Tool memberi `warning` supaya model mengecilkan atau memecah file itu.
