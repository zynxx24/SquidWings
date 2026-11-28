# Catatan perubahan

## 2026-11-28: update otomatis lagi, repo dan skill selalu sinkron

- Repo kembali menarik commit baru dari GitHub sendiri saat start dan setiap 12 jam (`AUTO_UPDATE`, bawaan `apply`; `notify` kalau hanya mau diberi tahu, `false` untuk mati). Paket yang berubah ikut terpasang, tetap mengikuti `requirements.lock`.
- Skill GitHub juga mengikuti upstream setiap hari tanpa perlu disetujui. Pin commit tetap mencatat versi yang dipakai, dan setiap perpindahan (dari, ke, file yang berubah) tercatat di `python main.py skills history` untuk audit dan rollback. `SKILL_SOURCES_AUTO_APPROVE=0` mengembalikan mode tahan-dulu.

## 2026-11-28: pengerasan keamanan dan efisiensi setelah review

- **Akses jarak jauh butuh PIN.** Siapa pun yang membuka lewat tunnel atau jaringan harus masuk di `/access` dengan PIN akses, termasuk untuk socket chat. PIN dibuat otomatis dan tampil sekali di konsol saat tunnel dibuka, disimpan sebagai hash, dan cookie-nya batal saat PIN diganti (`PUT /api/access/pin` dari komputer ini). Dari komputer ini tetap tanpa login. Router dan kredensial dari luar juga butuh PIN router.
- **Penjaga perintah membaca isi perintah, bukan pola teks.** `rm -r -f /`, `rm -rf ~`, kode Python yang menghapus home, mengirim kunci SSH, `base64 -d | sh`, `bash <(curl …)`, dan shell root (`sudo -i`) ditolak. `sudo` untuk satu perintah, installer resmi (nvm, bun, composer), dan kerja biasa di folder proyek tetap jalan. `terminal_run` sekarang dijaga sama seperti `execute_command`, dan kalau penjaga gagal dimuat, yang boleh jalan hanya tool baca.
- **Konten dari luar tidak bisa memerintah.** Hasil web, scrape, dan unduhan ditandai tidak tepercaya. Setelah itu, perintah `sudo`, installer dari internet, atau `git push` menunggu jawaban "ya" dari pengguna sendiri.
- **Supply chain.** Skill GitHub dipin ke satu commit. Update upstream menunggu di `python main.py skills pending` sampai `skills approve`. Repo hanya memberi tahu ada commit baru (`python main.py update` untuk menerapkan, atau `AUTO_UPDATE=apply`), dan paket dipasang mengikuti `requirements.lock`.
- **Batas biaya, bukan batas kemampuan.** Satu hasil tool dipotong di 120 ribu karakter (awal dan akhir, sisanya bisa dibaca per bagian), dan satu giliran punya anggaran token (`AGENT_TURN_TOKEN_BUDGET`, bawaan 3 juta; bilang "lanjut" untuk meneruskan). Panduan di atas 12 ribu karakter dikirim sebagai daftar isi dan dibuka per bagian dengan `load_skill(..., section=...)`.
- **Data di luar folder repo.** Chat, memori, vault, kredensial, key router, dan data trading pindah ke `~/.local/share/squidwings` (Windows: `%LOCALAPPDATA%`), dengan link di tempat lama, jadi `git clean` atau clone ulang tidak menghapusnya.
- **Satu sumber aturan.** Aturan ditulis sekali di `.claude/rules/`. Prompt harness (`_rules.txt`), skill `bar-kualitas`, `AGENTS.md`, dan `~/.codex/AGENTS.md` dibangun dari sana oleh `scripts/sync_agent_config.py`.

## 2026-11-28: rencana dulu, naskah dulu, bar kualitas enam aspek, dan folder `.claude` + `.codex`

- **Rencana menyeluruh sebelum eksekusi.** Prompt dasar dan protokol harness sekarang meminta model memetakan kondisi dulu, lalu menulis rencana dari segala segi (tujuan, pemakai, desain, enam aspek kualitas, risiko, langkah, verifikasi, cara mundur), baru dieksekusi.
- **Video: naskah final dulu.** Teks di layar dan narasi dibangun dari satu `naskah.md`, jadi tidak pernah beda. Ada di skill `video-audio` dan `voice-over` (bagian 0, plus pelajaran dari video sebelumnya), dan di playbook vault yang otomatis muncul untuk request video serupa.
- **Bar kualitas enam aspek.** Skill baru `bar-kualitas` ikut setiap kali membangun web, backend, fitur, Laravel, atau memperbaiki bug: keamanan, kualitas kode, keandalan, performa, maintainability, dan usability, masing-masing dilaporkan PASS atau FAIL dengan bukti. Prompt kode juga mewajibkan keenamnya.
- **`.claude/` dan `.codex/` di repo.** Aturan ditulis sekali di `.claude/rules/` dan diimpor `CLAUDE.md`. Isinya: pengaturan bersama Claude Code (izin untuk perintah cek, blokir baca `.env`, kredensial, dan key router), skill `super-plan`, `quality-gate`, `video-script`, dan subagent `quality-reviewer`. Untuk Codex: `.codex/config.toml`, plus `AGENTS.md` dan `.codex/skills/` yang dibangun oleh `scripts/sync_agent_config.py`. Tes gagal kalau salinan Codex tertinggal.

## 2026-11-27: routing Gemini TTS lintas key dan model

- Kredensial > Gemini API key sekarang bisa diisi beberapa key, dipisah koma atau baris baru.
- Gemini TTS memilih pasangan (key, model) yang masih segar. Batas per menit langsung pindah ke key lain di model yang sama; kalau semua key sedang kena, jedanya ditunggu di model yang sama supaya satu trek tetap satu warna suara.
- Batas harian mengistirahatkan key itu untuk model tersebut. Setelah semua key habis di satu model, suara pindah ke model berikutnya: `gemini-3.8-flash-tts`, `gemini-3.8-flash-lite-tts`, `gemini-3.1-flash-tts-preview`, lalu `gemini-2.5-flash-preview-tts`. Key yang ditolak diistirahatkan 30 menit. Key tidak pernah disimpan di status routing, hanya sidik jarinya.
- Skill voice-over menjelaskan routing ini dan mengingatkan bahwa arahan gaya harus di `## Director's notes`, bukan di teks yang dibacakan.

## 2026-11-27: harness lebih tahan kasus pinggir

- Koneksi SQLite (simpan hasil tim, tabel data baru) sekarang ditutup setelah dipakai; sebelumnya hanya di-commit dan file-nya tetap terbuka.
- Sumber skill GitHub: skill dari file selain SKILL.md masuk ke daftar aktif atau simpanan sesuai manifest; `sources.json` yang rusak (bukan objek JSON) tidak lagi membuat pemuatan skill gagal.
- Tunnel: membaca alasan gagal tidak lagi error kalau log tunnel belum ada.
- Add-on trading: status run yang rusak, run yang sudah berhenti, dan stop yang kosong ditangani tanpa error.
- Pemeriksaan kode bersih seluruhnya: ruff, mypy (250 file), dan 2058 tes.

## 2026-11-27: voice-over yang memeriksa dirinya sendiri

- `voice_track` mendengar ulang trek yang sudah jadi (whisper lokal). Baris yang tidak terdengar, terdengar lain, atau katanya kepotong ke baris tetangga diucapkan ulang sendiri satu kali, lalu trek disusun lagi. Hasilnya `ok_audio`, `check`, dan `repaired`.
- Kata yang sudah cocok dengan naskah tidak lagi pindah ke baris berikutnya hanya karena jeda napas sebelumnya lebih panjang (kasus "lunas. Beres.").
- Naskah diperiksa sebelum kuota dipakai: rentang terbalik, tumpang tindih, lewat panjang video, atau terlalu banyak kata untuk rentangnya.
- Baris yang tetap kepanjangan tidak lagi menimpa baris berikutnya; baris berikutnya mundur sedikit dan dilaporkan di `shifted`. Volume antarbaris disamakan.
- Gemini TTS mencoba lagi saat server sibuk (5xx), koneksi putus, jawaban tanpa audio, atau audio yang terpotong jauh dari teksnya.
- `video_add_voice` menolak menimpa video aslinya dan memberi tahu kalau suara masih bicara setelah videonya habis. `mode="recut"` tanpa rekaman utuh menjelaskan apa yang kurang, bukan error mentah.

## 2026-11-27: voice-over video dengan Gemini TTS, tepat di detik adegannya

- **Gemini TTS** sebagai engine suara (`voice_speak` engine `gemini`, suara seperti `Kira`, `Kore`, `Puck`). Suaranya mengikuti arahan gaya, jeda, dan nada, seperti di AI Studio. Key diisi di Kredensial > Gemini API key (atau `GEMINI_API_KEY`). Batas paket gratis (3 permintaan per menit, 10 per hari) ditangani: tunggu otomatis untuk batas per menit, dan pesan jelas saat kuota harian habis.
- **`voice_track`**: naskah bertimestamp (`mic.txt`, atau format "## Transcript" dari AI Studio) menjadi satu trek suara yang tiap barisnya jatuh di detik adegannya. Baris yang terlalu panjang dipercepat sedikit (maks 1,3x). Hasilnya juga berisi caption `.srt` dan `.words.json`. Tanda baca suara seperti (↑) (↓) (...) (//), `*tekanan*`, `[senyum]`, `Gaya suara :`, dan `"Nama" dibaca: ...` dibaca sebagai arahan, tidak ikut diucapkan.
- **Sekali ambil**: dengan Gemini, seluruh naskah diucapkan dalam 1 permintaan (nada menyambung, hemat kuota), lalu dipotong per baris dengan whisper lokal dan pencarian jeda di audio. Rekaman utuhnya disimpan (`voice.take.wav`), jadi `mode="recut"` bisa memotong ulang tanpa memakai kuota.
- **`video_add_voice`**: video yang sudah dirender langsung diberi suara tanpa render ulang; musik bawaannya mengecil saat ada suara.
- **Skill baru `voice-over`**: format naskah, pilihan engine, cara memasukkan suara ke Remotion, HyperFrames, atau Studio, dan cara mengecek hasilnya. Video promosi dan explainer sekarang selalu diberi suara, dan `video_check` mengingatkan bila proyek punya naskah tapi belum punya trek suara.

## 2026-11-26 (malam): pembaruan otomatis tiap 12 jam, dan semua jalur mencatat perubahan file

- **Pembaruan otomatis tiap 12 jam selama terminal atau web menyala, hanya saat online.** Repo SquidWings (dari tiap 30 menit), skill dari GitHub (dari tiap 24 jam), dan alat lokal seperti Agent Reach, graphify, aislop, dan yt-dlp (dulu hanya sekali saat start, sekarang berulang dan ikut jalan di chat terminal). Saat offline tidak ada yang dicoba, start tidak menunggu fetch, dan pembaruan berjalan begitu jaringan kembali. Satu pengaturan untuk semuanya: `UPDATE_EVERY_HOURS` (bawaan 12).
- **Perubahan file dari chat terminal dan run alur juga tercatat**, bukan hanya dari chat web: IDE dan "Perubahan AI" menampilkan dan bisa mengurungkan semuanya, termasuk pekerjaan dari Telegram. Chat terminal menulis ringkasannya di bawah jawaban.

## 2026-11-26 (malam): Agent Reach di harness

- **Agent Reach** ([Panniantong/Agent-Reach](https://github.com/Panniantong/Agent-Reach)) terpasang sebagai alat harness. AI bisa membaca subtitle dan transkrip YouTube, thread Reddit beserta komentarnya, X/Twitter, issue dan PR GitHub, Bilibili, V2EX, lowongan LinkedIn, kurs Xueqiu, dan platform lain yang sulit dibaca pencarian biasa. Tool barunya: `reach_read`, `reach`, `reach_status`, `reach_manage`. Semuanya hanya membaca, tidak pernah posting, dan tidak pernah login atas namamu.
- `web_read` otomatis memakai Agent Reach untuk link YouTube, Reddit, X, Bilibili, Instagram, Facebook, dan LinkedIn, serta sebagai cadangan ketika halaman GitHub atau V2EX gagal dibaca.
- **Diperbarui otomatis tiap hari** bersama graphify, aislop, dan yt-dlp (`agent-reach update` saat ada rilis baru di GitHub).
- Penyesuaiannya ada di harness (`brain/utils/agent_reach.py`), bukan di skill upstream. Skill bawaan Agent Reach dicabut dari folder skill AI lain (Claude Code, Gemini, dan sebagainya); skill untuk Claude Code tetap dipilih lewat `skills/sources.json`.
- Perintah terminal: `python main.py reach install | update | status | add <platform> | remove <platform> | read <link>`. Login ke platform dilakukan sendiri dengan `agent-reach login <platform>` di Terminal. Matikan dengan `AGENT_REACH=off`.

## 2026-11-26 (malam): AI berpikir lebih luas di semua bidang

- **Berpikir lebih luas di setiap giliran yang punya ruang pilihan**: desain, kode, matematika, tulisan, strategi, dan data. AI mendapat daftar jawaban autopilot yang biasa dibuat semua model (hero di tengah dengan gradasi, loop bersarang O(n²), langsung pakai rumus pertama, "Di era digital ini") sebagai patokan yang harus dikalahkan. AI juga mendapat tiga sumbu variasi, tiga lensa sudut pandang yang belum dipakai belakangan di chat mana pun, dan cara membuktikan hasilnya: kode dijalankan, soal matematika diselesaikan dengan cara kedua, halaman dicek terhadap arah desainnya. Permintaan seperti "yang beda" atau "jangan sama" meminta perubahan di paling sedikit dua sumbu.
- Tool baru **`think_wider`** untuk brainstorming atau saat hasil pertama terasa biasa: lebih banyak lensa, semua sumbu, dan aturan memilih satu yang terkuat lalu membangunnya.
- Think prompt untuk kode, matematika, gambar, dan penalaran sekarang meminta membandingkan beberapa cara sebelum memilih.
- Katalognya `input/think/lenses.yaml` (bisa ditambah tanpa restart). `THINK_WIDER=off` mematikannya. Desain dan tulisan memakai temperatur sedikit lebih tinggi; kode dan matematika tetap presisi.

## 2026-11-26 (malam): desain tidak lagi itu-itu saja

- **Aplikasi punya gaya aplikasi.** `style_pick` mengenali aplikasi, dashboard, panel admin, dan sistem sekolah atau toko (`medium="app"`, juga otomatis dari subjeknya). Hasilnya 14 tata letak aplikasi sungguhan, misalnya rail dan workspace, daftar dan detail, tabel dulu, langkah dengan ringkasan, roster foto, form dan pratinjau cetak, satu tugas fokus. Tata letaknya dipilih menurut kegunaan layar (pendaftaran, kamera absensi, stok, jadwal), bukan landing page. Font poster dan tulisan tangan tidak dipakai untuk layar aplikasi.
- **Arah desain membawa kerangka.** Tiap tata letak aplikasi menyertakan `skeleton` (grid CSS layarnya), dan setiap arah web atau aplikasi menyebut struktur bawaan yang harus diganti: hero di tengah, tiga kartu, pita selang-seling, CTA.
- **Hasil dicek setelah giliran.** Kalau halaman yang baru ditulis tidak memakai font atau warna dari arah yang dipilih, atau masih memakai ciri template bawaan (gradasi Tailwind indigo atau ungu, kerangka `max-w-7xl` yang diulang, Inter, kaca buram), verifier meminta AI memperbaikinya di giliran yang sama.
- Permintaan membuat aplikasi, dashboard, atau tampilan apa pun sekarang langsung membawa `style_pick` dan `design_explore`.

## 2026-11-26 (malam): Telegram tidak lagi membuat folder per pesan, harness lewat Telegram, ulangi, garis bisa digeser

- **Satu folder per alur.** Setiap run alur sekarang bekerja di folder alur itu (`[team] <nama alur>`), bukan folder baru per pesan. File dari pesan sebelumnya tetap ada, jadi AI di Telegram bisa melanjutkan pekerjaannya. Di Pemicu ada pilihan **Folder kerja**: folder alur ini (bawaan), folder baru tiap run (untuk alur yang membangun proyek terpisah), atau folder chat web yang sudah ada, supaya Telegram dan web memakai folder yang sama.
- **Satu pesan, satu alur.** Pesan dari kamu tanpa kata pemicu hanya dijawab satu alur Telegram: yang terakhir disimpan. Alur dengan kata pemicu (misalnya `/atur`) tetap menangkap pesannya sendiri. Di Pemicu tampil daftar alur Telegram aktif lain yang juga menangkap semua pesan, dengan tombol Matikan.
- **Node agent "Harness penuh".** Agent memakai harness yang sama dengan chat: semua alat dan skill, tanpa batas langkah, model boleh Auto, dan seluruh percakapan diingat (tersimpan sebagai chat, bisa dilanjutkan di web dengan tag tg). Template baru **Harness lewat Telegram (seperti chat lokal)**.
- **File dikirim ke Telegram.** File yang dibagikan AI (`share_file`) selama run dikirim ke chat Telegram sebagai file: gambar sebagai foto, video dan audio diputar langsung, lainnya sebagai dokumen (batas bot 50 MB). Tiap file dikirim sekali.
- **Ulangi dari node sebelumnya.** Jalur IF atau Switch bisa mengirim pekerjaan kembali ke node sebelumnya, misalnya "bila hasil belum lolos, agent penulis mengerjakan ulang", dengan batas 1 sampai 20 kali. Node yang diulang mendapat isi yang dicek dan alasan dari AI kenapa belum lolos. Buat dengan menarik garis dari jalur itu ke node sebelumnya, atau lewat bagian Ulangi di panel node. Garisnya putus-putus dengan label jumlah ulangannya.
- **Garis bisa digeser.** Seret garis ke node lain untuk memindah ujungnya; klik untuk memilih, lalu sisipkan node atau putus.
- **Node Database bisa memakai sumber dari Tabel data** (file, Google Sheets, PostgreSQL, MySQL, MongoDB, tabel bawaan), dengan tombol **+ Sumber** di node itu. Sumber yang baru dibuat langsung dipakai node itu, dan tabel log dibuat otomatis bila belum ada.

## 2026-11-26 (malam): add-on IDE, perubahan file AI tercatat, tampilan chat

- **Add-on baru: IDE** (`python main.py addon install ide`, menu IDE atau Alt+6). Semua folder chat dan proyek buatan AI dalam satu tempat: pohon file, editor CodeMirror dengan warna sintaks untuk puluhan bahasa, cari di semua file, buka cepat (Ctrl+P), pratinjau halaman HTML, Markdown, dan server pengembangan yang sedang jalan, unduh .zip, serta file baru, folder, ganti nama, dan hapus ke sampah proyek.
- **Setiap perubahan AI tercatat per giliran**, siapa pun alat yang menulisnya (tool file, perintah shell, build). Di IDE ada tab Perubahan AI: diff per file, kembalikan satu file atau satu giliran, dan "Minta AI jelaskan". Edit tangan di antara giliran tidak dianggap perubahan AI.
- **IDE mengikuti AI langsung**: file yang terbuka ikut diperbarui saat AI mengubahnya. Kalau kamu sedang mengedit file yang sama, kamu memilih versi mana yang dipakai, jadi tidak ada yang tertimpa diam-diam.
- **Tanya AI dari IDE** (Ctrl+I): pertanyaan tentang file atau bagian yang dipilih dikirim ke chat proyek itu, dengan tombol cepat Jelaskan, Cari bug dan perbaiki, Rapikan, dan Tulis tes.
- **Chat: kartu "file diubah AI"** di bawah setiap jawaban yang mengubah file, dengan +/− baris per file, tombol Buka di IDE (langsung ke diff-nya), dan Urungkan perubahan. Kartunya muncul lagi saat chat lama dibuka.
- **Chat: blok kode** menunjukkan jumlah baris, punya tombol Bungkus untuk baris panjang, dan blok di atas 30 baris diringkas dengan tombol Tampilkan semua.
- **Chat: file yang dibagikan AI** tampil sebagai kartu (jenis, nama, Unduh, buka di IDE), berjajar rapi, bukan deretan tautan.

## 2026-11-26 (malam): template Harness lengkap

- **Template "Harness lengkap lewat Telegram (semua node)"**: satu alur yang memakai kesepuluh jenis node. `/catat` dirapikan AI jadi catatan berlabel lalu disimpan ke Tabel data, `/lihat` menampilkan 10 catatan terakhir beserta arti labelnya lewat relasi, `/kurs` membaca kurs dari API publik (node HTTP), `/fokus` mulai sesi fokus 25 menit dan mengabari saat habis. Pesan lain dinilai AI: kerja berat direncanakan, dikerjakan dengan semua alat, lalu diperiksa; selain itu dijawab Asisten. Semua jawaban lewat satu Merge ke Telegram dan tercatat di database. Tabel `catatan`, `label`, dan relasinya dibuat otomatis saat template dipakai.
- Jeda di alur Telegram tidak lagi mengunci chat: selama alur menunggu (misalnya 25 menit fokus), pesan berikutnya tetap dijawab.
- Agent sebelum Merge yang menuju Telegram sekarang dianggap langkah terakhir, jadi ia menjawab seperti di chat.
- Node Tabel data bisa memilih relasi lewat namanya, dan JSON di dalam blok ```json dari AI tetap terbaca oleh `{{json.kolom}}`.

## 2026-11-26 (malam): Telegram jadi chat biasa, bukan cuma bot toko

- **Chatmu ke bot selalu dilayani sebagai pemilik.** Pesan dari chat di Kredensial tidak lagi diperlakukan sebagai pelanggan, juga saat menulis ke alur publik: agent memakai semua alat dan data, tanpa batas pesan per menit atau per hari, dan tanpa aturan untuk orang asing.
- **Ingat obrolan.** Alur Telegram "Hanya saya" sekarang juga mengingat giliran terakhir tiap chat (bawaan 6, isi 0 untuk perintah yang berdiri sendiri), jadi "kalau besok?" nyambung dengan pertanyaan sebelumnya.
- **Pesan berikutnya antre, tidak ditolak.** Pesan yang datang saat jawaban sebelumnya belum selesai menunggu gilirannya, bukan dibalas "masih berjalan". Kalau alur gagal, alasannya dikirim ke chatmu.
- **Agent yang membalas tahu ia sedang chat** denganmu: jawaban mengikuti percakapan, panjangnya secukupnya, Markdown dirapikan untuk Telegram. Alur tanpa node Telegram tetap membalas dengan hasil akhirnya.
- Template baru **Ngobrol lewat Telegram**: Pemicu Telegram, satu Asisten dengan semua alat, lalu balas di chat yang sama.
- Pilihan penerima sekarang "Hanya saya" dan "Siapa saja (publik)"; penanda untuk agent jadi netral ([PENGIRIM], [PESAN BARU]), tidak lagi menganggap setiap pengirim pelanggan toko.

## 2026-11-26 (sore): pesan Telegram rapi, jendela web di Telegram, run langsung terlihat di web

- **Run dari Telegram dan jadwal tampil di web.** Editor yang sedang membuka sebuah alur sekarang ikut menampilkan setiap run alur itu, apa pun pemicunya: node menyala satu per satu, hasilnya muncul, dan baris status menyebut asalnya ("dari Telegram (nama pengirim)", "dari jadwal"). Sebelumnya kanvas hanya bergerak untuk run yang dimulai dari tombol Jalankan alur, jadi pesan Telegram terlihat seperti tidak berefek padahal sudah dijalankan dan dibalas.
- **Pesan Telegram yang rapi.** Mode "Rapi otomatis" (bawaan) mengubah Markdown jadi huruf tebal, miring, daftar berpoin, kutipan, dan kode. Tabel dibuat muat di layar HP. Data JSON, misalnya hasil Tabel data, jadi daftar singkat dengan angka yang mudah dibaca (75.000). Kalau Telegram menolak format, pesan dikirim ulang sebagai teks biasa, jadi tidak ada yang hilang. Mode "Teks biasa" tetap ada.
- **Isi pesan sendiri**: node Telegram punya kotak Isi pesan dengan `{{input}}`, `{{json.kolom}}`, `{{date}}` dan lainnya, plus pratinjau berbentuk gelembung chat di editor tanpa mengirim apa pun. Bisa juga dikirim tanpa bunyi notifikasi.
- **Tombol dan jendela web.** Tombol "Buka jendela web" membuka isi lengkap sebagai halaman di dalam Telegram: tabel untuk data, teks rapi untuk jawaban, mengikuti tema terang atau gelap Telegram. Tombol "Tautan" membuka alamat apa pun (boleh pakai `{{json.kode}}`). Pesan yang terlalu panjang otomatis dikirim sebagai ringkasan dengan tombol "Buka lengkap".
- **Alamat publik untuk jendela web**: isi "Alamat web publik" di Kredensial > Telegram bila punya domain sendiri. Kalau kosong, dipakai tunnel web server (`--tunnel`), atau dibuat otomatis lewat Cloudflare Tunnel milik server kecil yang hanya berisi halaman hasil, jadi aplikasi lainnya tidak ikut terbuka ke internet. Halaman memakai tautan acak dan dihapus setelah 60 hari.

## 2026-11-26: Tim agent dirombak seperti n8n

- **Yang tersambung itulah yang jalan.** Tidak ada lagi Lead dan Reviewer tersembunyi atau garis putus-putus otomatis. Pemicu menjalankan node yang ditarik garis darinya, tiap node menerima isi dari garis yang masuk, dan node yang tidak tersambung tidak dijalankan (tampil pudar di kanvas). Hasil akhir adalah output langkah terakhir.
- **Alur lama tetap jalan.** Alur yang dibuat dengan model Lead/Reviewer berjalan seperti biasa sampai dibuka dan disimpan lagi. Saat dibuka, sambungannya ditampilkan sebagai garis biasa: Lead menerima hasil semua agent, Reviewer menjadi agent sesudahnya yang memperbaiki jawaban sendiri.
- **Halaman depan alur kerja**: daftar alur dengan pencarian, saklar Aktif (jadwal dan pemicu Telegram bisa diistirahatkan tanpa kehilangan pengaturan), duplikat, ekspor dan impor JSON, hapus, plus kartu template. Tab Tabel data dan Kredensial pindah ke halaman ini.
- **Node Tabel data** (pengganti "simpan ke database" yang hanya menulis log): pilih sumber dan tabel dari daftar, atau buat tabel baru langsung dari node. Operasi Ambil, Tambah, Ubah, Upsert, Hapus dengan syarat (AND/OR), urutan, batas baris, dan pemetaan kolom. Nilai bisa memakai `{{json.kolom}}` dari input. Hasilnya JSON, jadi node berikutnya bisa membaca kolomnya. Isian yang tidak ada di input ditolak dengan pesan jelas, tidak ditulis mentah ke tabel.
- **Relasi antar tabel**: tandai kolom yang menunjuk ke tabel lain (misalnya `pesanan.kode_produk` ke `produk.kode`), juga lintas sumber. Node Tabel data bisa ikut mengambil baris yang berelasi (`{{json.produk.nama}}`), dan agent melihat relasinya lewat `db_schema`. Diatur dari tombol Relasi di tab Tabel data atau dari node.
- **Tabel data bawaan**: tabel baru tanpa perlu membuat file atau mengisi URL database, disimpan di SQLite milik aplikasi.
- **Tes langkah**: jalankan satu node saja dengan input yang terlihat di panel kiri, tanpa menjalankan seluruh alur. Panel input menampilkan kolom JSON yang bisa diklik untuk dimasukkan ke kotak yang sedang aktif. Output JSON tampil sebagai tabel atau JSON.
- **Kanvas**: node berbentuk kotak dengan nama di bawahnya, garis bisa diklik untuk menyisipkan node atau memutusnya, menghapus node di tengah menyambung ulang garisnya, urungkan dan ulangi (Ctrl+Z, Ctrl+Shift+Z), salin, tempel, duplikat (Ctrl+C, Ctrl+V, Ctrl+D), klik sekali memilih dan klik lagi membuka node. Tanda merah menunjukkan node yang belum lengkap, dan daftar "perlu diisi" di atas membawa langsung ke node itu. Posisi node disimpan di server, jadi sama di HP dan laptop.
- **Batas dihapus**: tidak ada lagi batas 12 node dan 6 agent per alur. Alur boleh tanpa agent, misalnya hanya Tabel data dan Telegram. Syarat IF sampai 20, jam harian sampai 48.
- Garis di kanvas sebelumnya tidak bisa diklik karena tertutup lapisan node; sudah diperbaiki.

## 2026-11-26: video hasil ekspor bisa ditonton, unduh otomatis

- **Perbaikan**: video, audio dan gambar yang dibagikan di chat (termasuk hasil ekspor Studio) gagal diputar saat dibuka di tab sendiri. Header `sandbox` membuat Chrome menolak pemutarnya memuat file itu sendiri. Media sekarang dikirim dengan kebijakan ketat tanpa `sandbox`; file lain (HTML dan sebagainya) tetap `sandbox` dan diunduh.
- **Unduh otomatis**: ekspor yang dimulai dari Studio langsung mengunduh MP4-nya begitu selesai. Dialog ekspor juga memberi tautan Tonton dan Unduh. `?download=1` di tautan file mana pun memaksa unduh.

## 2026-11-26: keyframe sendiri, template gerak, filter

- **Atur keyframe sendiri**: bagian "Keyframe" di panel Properti menampilkan semua keyframe lapisan (waktu, nilai, gerak) dan bisa diedit langsung. Tombol Haluskan semua, Pelan awal-akhir, Linear.
- **Gerak "Halus"**: gerak mengalir melewati semua keyframe tanpa berhenti di setiap titik (kurva Catmull-Rom), sama di pratinjau dan ekspor.
- **Jalur gerak di kanvas**: lapisan yang bergerak menampilkan jalurnya dengan titik per keyframe posisi; titiknya bisa diseret.
- **Template gerak**: Muncul naik, Pop, Geser dari kiri, Jatuh memantul, Ketik / gambar garis, Zoom pelan, Melayang, Denyut, Goyang, Keluar pudar, Hapus semua gerak. Dihitung dari posisi dan waktu lapisan itu, lalu tetap bisa diatur keyframe demi keyframe.
- **Filter** dengan pratinjau kecil dari frame saat ini: Hangat, Dingin, Sinematik, Vintage, Senja, Cerah, Pastel, Pudar, Dramatis, Hitam putih, Noir. Bisa untuk seluruh video (dengan vinyet) atau per lapisan, dengan slider kekuatan; angkanya tetap bisa disetel sendiri. Properti warna baru: Pudar.

## 2026-11-25 (malam): jalankan tanpa venv

- `./run.sh` (Linux, macOS) dan `run.bat` (Windows) menjalankan SquidWings tanpa mengaktifkan venv. Tanpa argumen membuka web; `./run.sh chat`, `./run.sh addon list` dan perintah lain diteruskan ke `main.py`. Kalau `.venv` belum ada, setup dijalankan dulu.

## 2026-11-25 (malam): Studio tahap 2, efek dan editor yang lebih lengkap

- **Transisi** per lapisan, masuk dan keluar: pudar, geser (4 arah), zoom, blur, sapu (4 arah), lingkaran. Durasi dan gerak bisa diatur; batang di timeline menunjukkan panjang transisinya. Silang antar klip = pudar keluar ditumpuk pudar masuk.
- **Warna**: kecerahan, kontras, saturasi, rona, suhu warna. Semuanya bisa diberi keyframe.
- **Chroma key** (green screen) dengan kemiripan, kehalusan tepi, dan pembuang pantulan warna.
- **LUT .cube** (color grading 3D) dengan kekuatan 0 sampai 1. Unggah lewat tombol "Unggah .cube".
- **Mask** kotak atau elips dengan tepi lembut dan opsi balik, ikut bergerak bersama lapisannya.
- **Editor kurva easing**: pilih keyframe, seret dua titik kurva (cubic-bezier). Gerak baru: ambil ancang-ancang, memantul jatuh.
- **Track**: jalur di timeline untuk beberapa klip sekaligus, bisa disembunyikan, dikunci, atau dibisukan bersama. Batang yang digeser menempel ke tepi klip lain di track yang sama dan ke playhead (Alt untuk lepas).
- **Proxy media**: klip video besar (lebih dari 960 px atau 40 MB) otomatis dibuatkan salinan kecil saat diunggah, supaya editor lancar di HP. Tombol Proxy di bawah pratinjau. Ekspor tetap memakai file asli.
- **Sinkron push**: editor tidak lagi polling tiap 2,5 detik. Satu aliran Server-Sent Events (`/api/studio/{sesi}/events`) mengirim simpanan AI, progres render, dan progres proxy begitu terjadi. Setelah HP bangun, browser menyambung lagi sendiri.
- Semua efek dihitung dengan rumus yang sama di pratinjau (`render.js`) dan engine C (`sqengine.c`). Tes membandingkan keduanya lewat node: warna selisih maksimal 2 dari 255, posisi kurang dari 0,6 piksel. Build Windows (dijalankan lewat Wine) menghasilkan piksel yang sama persis dengan Linux.
- Agent tahu format barunya (skill `studio`, protokol Studio, ringkasan efek per lapisan di konteks).

## 2026-11-25 (sore): Studio video dengan engine C

- **Render di server dengan engine C** (`addons/studio/engine/sqengine.c`): rasterizer anti-alias sendiri, teks, path SVG, stroke, blur, gambar, klip video, mixer audio. Aturannya sama dengan pratinjau editor. Ekspor tidak lagi di browser: HP boleh tidur, render tetap jalan, editor yang dibuka lagi menampilkan progresnya; bisa dibatalkan.
- **Kontrol sumber daya**: `STUDIO_THREADS` (thread), `STUDIO_MEM_MB` (batas memori, thread dikurangi supaya muat), `STUDIO_NICE` (prioritas rendah), `STUDIO_GPU` (auto/off/nama encoder). GPU dipilih lewat tes encode sungguhan: NVENC, Quick Sync, VAAPI, VideoToolbox, AMF; gagal di tengah jalan pindah ke CPU.
- **Lintas OS tanpa repot**: engine dikompilasi saat `addon install studio` dengan cc/gcc/clang/MSVC atau `ziglang` dari pip (tanpa admin, juga Windows). Diuji: Linux, build Windows dijalankan lewat Wine, build macOS arm64/x86_64.
- **Caption otomatis** dari voice-over atau klip video (Whisper, waktu per kata): tool `studio_captions`.
- **Agent bisa melihat hasilnya** tanpa Chrome: `studio_still` memakai engine.
- **Potong di playhead** (tombol Potong, tombol S) di editor.
- Batas 300 lapisan / 600 keyframe / 2 MB dokumen dilonggarkan jadi batas pengaman saja.

## 2026-11-25: rombak struktur, otak/harness, model, web di HP

### Struktur folder
- Kode dibagi per segmen dan setiap folder punya `README.md`: `brain/` (otak + harness: `agent/`, `harness/`, `classifier/`, `memory/`, `model/`, `system_prompt/`, `config/`, `utils/`, `security/`), `input/`, `skills/`, `tools/`, `web/`, `addons/`, `scripts/`, `tests/`, `docs/`.
- Dulu terpisah: `agent/`, `core/` (config, utils, security), `model/`, `tool/`, `trading/`, `knowledge/`, `config/`, `utils/`, `security/`. Semua import sudah diganti.
- Data saat jalan pindah ke `data/` (log, `audit.jsonl`, laporan, arsip). File lama di root dipindah otomatis saat start (`brain/config/paths.py`); nama yang bentrok diberi nomor, tidak ditimpa.
- `cli.py` dihapus (cukup `main.py`), `tunnel.sh` ke `scripts/`, config pyright/pyrefly masuk `pyproject.toml`.
- `CLAUDE.md` di-publish: konteks proyek untuk siapa pun yang fork lalu membuka Claude Code.

### Add-on
- Trading dan Studio jadi add-on di `addons/`, **mati bawaan**. `python main.py addon list | install | enable | remove`. Tiap add-on punya `addon.json` (file, paket, program, langkah manual per OS) dan README.

### Skill
- Skill asli dari GitHub tidak diubah; penyesuaian kita di `skills/adapters/<nama>.md`. Baru: Ponytail (DietrichGebert/ponytail), Obsidian (kepano/obsidian-skills), graphify (safishamsi/graphify), Google Maps (googlemaps/agent-skills). Google Maps dan graphify tetap jalan walau repo belum ter-clone.
- Skill desain digilir antar chat (design-ui, ui-ux-pro-max, impeccable, accessibility) supaya tidak itu-itu saja.
- Skor trigger berbobot IDF, frasa tumpang tindih dihitung sekali, pencarian skill dan `find_tools` pakai BM25 (`brain/harness/relevance.py`).
- `skills/__init__.py` dipecah: `loader.py`, `selection.py`, `design.py`, `runtime.py`. File skill yang diedit langsung berlaku tanpa restart.

### Model
- **Pilih sendiri:** model yang dipilih dikunci. Kalau error, alasannya ditampilkan (key, saldo, rate limit, model tidak ada, konteks, timeout, server) dan tidak diganti model lain (`brain/harness/model_errors.py`).
- **Auto:** autopilot hemat token (`brain/harness/autopilot.py`, `ROUTING_QUALITY=smart`): tier dari tingkat kesulitan, model termurah yang andal, naik satu tier setelah giliran gagal. Alasan pilihan tampil di chat dan terminal. `.env`: `ROUTING_QUALITY` dari `max` ke `smart`.

### Konteks dan cache
- Tanpa batas buatan: model yang tidak menyebut ukuran konteks dianggap 1 juta token (`DEFAULT_CONTEXT_WINDOW`), riwayat dan jumlah pesan tidak dipotong harness (`.env`: `HISTORY_MAX_TOKENS=0`), indeks skill memuat semua skill dengan yang paling relevan di atas.
- Cache terpusat `brain/utils/cache.py` (batas ukuran, kedaluwarsa, statistik `GET /api/cache`, bersih per chat). Hasil klasifikasi LLM di-cache supaya pesan yang sama tidak memanggil model dua kali. Prompt `.txt` yang diedit langsung berlaku.

### Web dan terminal
- **Web di HP:** jawaban tidak berhenti lagi saat layar mati atau pindah aplikasi. Jawaban jalan di server sampai selesai dan tersimpan; halaman menyambung lagi sendiri dan melanjutkan tampilan dari awal (`web/live_turns.py`). Hanya tombol stop yang menghentikan.
- Selector model: "Auto (hemat)" atau model terkunci (🔒). Alasan Auto tampil di langkah jawaban.
- Terminal: `/model auto`, `/model <id>`, `/cache [clear]`; baris rute menampilkan 🔒 atau alasan Auto.

### Lintas OS
- Satu bahasa (Python) untuk Windows, macOS, Linux: `brain/utils/platform_compat.py`, panel Terminal Windows lewat `pywinpty`, `python scripts/setup.py`.
