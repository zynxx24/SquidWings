# Panel trading

Backend trading bisa berjalan sendiri, terpisah dari agent:

```bash
python main.py trading      # panel di http://127.0.0.1/trading
python main.py web          # Web UI di 8080, dan server trading port 80 ikut jalan (satu set)
```

Port 80 tidak perlu disiapkan sendiri. Kalau Linux belum mengizinkannya, perintah di atas meminta sudo sekali di terminal. Lewat sudo itu ia menjalankan `sysctl -w net.ipv4.ip_unprivileged_port_start=80` dan menyimpannya di `/etc/sysctl.d/99-squidwings.conf`, jadi tetap berlaku setelah reboot.

Kalau port 80 masih dipegang server trading SquidWings yang lama, server lama itu dihentikan lalu diganti dengan kode terbaru. Simulasi yang sedang berjalan di server lama ikut berhenti. `python main.py web` memakai ulang server trading yang sudah jalan hanya kalau kodenya masih sama dengan file di disk; kalau tidak, server itu juga diganti. Kalau port 80 dipakai program lain, program itu tidak disentuh, dan pesannya menyebut nama dan PID-nya.

Mode ini dipakai untuk EA MT4. MT4 menolak URL berport dengan error 5200, jadi daftar WebRequest dan `BridgeUrl` diisi `http://127.0.0.1` saja, yang berarti port 80. Web UI agent tetap di port 8080. Tiap server menyimpan data EA sendiri, jadi buka panel di server yang sama dengan `BridgeUrl` EA.

Panel juga tersedia di Web UI lewat tautan Trading di sidebar: ia terbuka di dalam web utama, dan di komputer ini datanya diambil dari server trading di port 80. Panel ini punya tiga backend:

| Backend | Cara kerja | Kebutuhan |
|---|---|---|
| `sim` | Pasar simulasi untuk mencoba panel, tanpa broker | Tidak ada |
| `mt4` | EA `trading/mt4/Mt4Bridge.mq4` mengirim harga lewat HTTP dan menerima perintah order | MT4, juga bisa lewat Wine. Tekan **Hubungkan EA** untuk langkah pemasangannya |
| `mt5` | Terhubung langsung lewat paket Python `MetaTrader5` | Windows dengan terminal MT5. Paket ini tidak jalan di Linux, dan belum ada EA bridge untuk MT5 |

Panel memakai warna, huruf, dan pengaturan tema yang sama dengan Web UI (`web/static/css/tokens.css`). Panel memenuhi layar dan mengikuti ukuran jendela. Di chart: roda mouse untuk zoom, seret untuk geser, seret sumbu harga untuk meregangkan, klik dua kali untuk reset. Tombol keyboard: `F` fokus chart, `+` dan `-` zoom, panah untuk geser, `0` kembali ke bar terbaru, `Esc` keluar. Garis antara chart dan tabel bisa diseret. Klik baris posisi untuk membuka simbolnya. Di backend `mt4`, timeframe 15m sampai 1D dibangun dari bar M5.

Usulan kuant memakai aturan Kalman dan HMM pada bar M5 yang sudah tutup, lalu menghitung lot dan mengecek batas risiko. Masuk hanya saat rezim trend dominan, setelah pullback Z dalam 3 bar terakhir dan close menembus high/low bar sebelumnya. Entry dilewati bila:

- jam rollover 21–22 UTC (`QUANT_BLOCK_HOURS_UTC`, kosongkan untuk mematikan),
- timeframe besar (kira-kira EMA 20 di H1) berlawanan arah,
- ada swing high/low kurang dari 1 jarak stop di depan entry (tidak ada ruang ke target),
- spread lebih dari 15% jarak stop, atau ADX turun dalam 3 bar.

Target 1,5R, atau 2R bila setup kuat (timeframe besar searah, ADX minimal 25, tidak ada level dalam 2,5R). Tiap usulan punya skor 0–100 dan daftar alasan; `QUANT_MIN_SCORE` bisa menjadikan skor itu syarat. Backtest walk-forward memakai aturan yang sama.

`GET /api/quant/manage` memberi saran stop untuk posisi terbuka: break-even +0,1R setelah 1,5R, lalu trailing 4 ATR. Saran ini tidak mengirim apa pun. Backtest bisa mengukurnya dengan `?manage=true`.

Pada 5.079 bar M5 XAUUSDcheck yang terekam (November 2026), aturan lama menghasilkan 3 trade dan aturan baru juga 3 trade. Break-even di 1R atau trailing 2–3 ATR menurunkan total R pada data itu, jadi setelan di atas sengaja longgar. Tiga trade tidak membuktikan apa pun. Nilai strateginya setelah minimal 100 trade.

Usulan ini berbasis aturan, bukan model AI, dan tidak menjamin profit. `trading/advisor.py` baru menyusun teks untuk ditinjau LLM dan belum memanggil model. Uji dulu di akun demo.

Tidak ada order yang terkirim otomatis. Setiap order melewati preview dan konfirmasi, batas volume, dan stop loss wajib. Akun real ditolak kecuali `MT5_ALLOW_LIVE=1`. EA MT4 juga tidak mengirim order kecuali `AllowTrading=true`.

## Melihat dari HP (tanpa token)

```bash
python main.py trading --tunnel      # mencetak URL publik .../trading
./tunnel.sh start 80                 # atau: tunnel terpisah untuk server trading yang sudah jalan
```

Data EA hanya ada di proses server trading (port 80), jadi tunnel harus ke port itu, bukan ke Web UI di 8080. Kalau EA sedang mengirim data dan belum ada yang login, panel langsung menempel ke EA, jadi halaman dari HP langsung menampilkan akun.

| Aksi | Di mesin ini | Dari luar (tunnel/LAN) |
|---|---|---|
| Lihat status, akun, harga, posisi, order, riwayat, usulan, saran stop, backtest aturan | Tanpa token | Tanpa token |
| Login, kirim/tutup/ubah/batal order, reset risiko, pairing EA, forward test, backtest dengan LLM | Tanpa token | `MT5_TRADE_TOKEN`, atau tanpa token bila `TRADING_REMOTE_NO_TOKEN=1` |

`TRADING_REMOTE_NO_TOKEN=1` membuat siapa pun yang tahu URL tunnel bisa mengirim order. Panel dan server menampilkan peringatan selama setelan ini aktif. Akun riil yang belum diizinkan `MT5_ALLOW_LIVE=1` tetap bisa dilihat, tetapi hanya mode lihat.

Pengunjung tunnel dibedakan dari browser di mesin ini per request: cloudflared datang dari loopback, tetapi membawa header `Cf-Connecting-Ip` dan hostname publik.

## Agent chat dan data trading

Tool `trading_snapshot` membaca server trading lokal (`TRADING_SERVER_URL`, default `http://127.0.0.1`) lewat endpoint baca saja: akun, posisi, usulan beserta alasannya, saran stop, status risiko, dan backtest. Tool ini tidak bisa mengirim order. Skill `analis-trading` bangun saat percakapan memang soal trading, dan menyuruh agent memakai data itu, menghitung risiko lebih dulu, dan tidak menjanjikan profit.

EA menyimpan secret di `MQL4/Files/mt4_bridge_secret.txt` bila input `BridgeSecret` kosong. Isi file itu sama dengan `trading/data/bridge_secret`.

## Prosedur praktik

Ikuti urutan ini setiap sesi. Lakukan di akun demo sampai hasilnya layak dinilai.

**Sebelum mulai**

1. Jalankan `python main.py trading` (atau `python main.py web`), biarkan terminalnya terbuka. Izin port 80 diminta sekali lewat sudo bila belum ada.
3. Buka MT4 dan pastikan sudah login ke broker. AutoTrading harus aktif.
4. Seret `Mt4Bridge` dari Navigator ke chart M5, satu chart untuk satu simbol. Jangan jalankan dari tombol Debug di MetaEditor.
5. Buka `http://127.0.0.1/trading`. Status harus **DEMO** dan **EA online**.

**Saat praktik**

1. Tekan **Lihat usulan** di bar M5 baru. "Tidak ada usulan" berarti tidak masuk.
2. Tekan **Isi tiket**, lalu cek baris risiko. Jangan lewat dari 1% ekuitas per trade.
3. Tekan **Preview**, baca dialognya, lalu **Kirim**.
4. Setiap posisi harus punya stop loss. Posisi yang dibuka manual dari MT4 tidak melewati batas panel.
5. Berhenti bila rules menghentikan trading, misalnya setelah 3 kali rugi beruntun atau batas rugi harian.

**Setelah selesai**

1. Tutup posisi yang tidak mau dibawa, lalu cek tab Riwayat.
2. Jalankan backtest pada simbol yang sama, lalu bandingkan dengan hasil praktik.
3. Tinjau minimal 100 trade sebelum menilai strateginya. Sampel kecil tidak berarti apa-apa.

**Sebelum akun riil**

1. Set `AllowTrading=false` dulu di EA, lalu pastikan data masuk.
2. Set `MT5_TRADE_TOKEN` bila server dibuka ke luar mesin, dan jangan pakai `TRADING_REMOTE_NO_TOKEN=1` dengan akun riil.
3. `MT5_ALLOW_LIVE=1` baru diisi setelah hasil demo diterima. Default-nya menolak akun riil.

## Backtest scalp 30 detik

Tombol **Scalp 30 detik · 5 posisi** di bagian Backtest menjalankan delapan strategi: EMA, momentum, breakout, RSI, Bollinger, VWAP, Kalman, dan tekanan candle. Bobot tiap strategi mengikuti hit rate terbarunya, dan strategi yang konsisten salah dibaca terbalik. Satu keputusan membuka 5 posisi dengan satu stop, target berjenjang, dan batas 30 detik. Setelah rugi, aturan atau LLM mengubah threshold, target, stop, dan jeda dalam batas `BOUNDS`. Ukuran lot dan batas risiko tidak bisa diubah keduanya. Centang **Atur oleh LLM** untuk memakai model `SCALP_LLM_MODEL`, paling banyak 5 panggilan per backtest.

Baca hasilnya dengan tiga angka: win rate, win rate impas, dan baris **Entry acak**. Win rate di bawah impas berarti rugi, setinggi apa pun angkanya. Bila ensemble tidak mengalahkan entry acak, sinyalnya belum punya keunggulan.

Hasil pada 12.534 bar M1 XAUUSDcheck dan USDCHF dari Mei sampai Juni 2026, dengan spread saat pengujian:

| Pengujian pada data yang belum dilihat | Win rate | Impas | Total |
|---|---|---|---|
| Emas, setelan target 8 dari 10, tahan 300 detik | 78,9% | 85,3% | -8,3R |
| Emas, setelan sama, entry acak | 75,5% | 83,2% | -8,8R |
| USDCHF, setelan sama | 69,8% | 85,3% | -24,2R |

Target 8 dari 10 bisa didekati dengan target sempit dan stop lebar, tetapi tiap kekalahan lebih besar dari beberapa kemenangan, jadi hasil akhirnya rugi. Belum ada setelan yang untung pada data uji, jadi strategi ini jangan dipakai di akun riil.

Bar M1 tidak bisa menunjukkan isi 30 detik. EA terbaru dan server merekam tick ke `trading/data/ticks/`, dan backtest otomatis memakainya setelah ada minimal 400 bar 5 detik, sekitar 35 menit data. Matikan rekaman dengan `MT4_RECORD_TICKS=0`.

## Simulasi live (tanpa order)

Panel **Simulasi live** di kolom kanan menguji aturan di harga yang sedang berjalan tanpa mengirim satu order pun:

1. Setiap siklus, delapan strategi scalp, ensemble berbobotnya, dan satu **lempar koin** sebagai pembanding masing-masing membuka satu posisi virtual di harga ask (beli) atau bid (jual) yang asli.
2. Posisi ditahan selama **Tahan** detik (bawaan 30), lalu ditutup di bid atau ask, jadi spread ikut dibayar.
3. Siklus berikutnya langsung dimulai, terus berulang sampai satu aturan menang di atas **Target** (bawaan 60%) dari minimal **Minimal trade** trade, atau sampai **Batas menit** habis.

**Gaya** memilih cara simulasi berjalan:

- **Scalping** (bawaan): seperti di atas, bar 5 detik dan tahan beberapa detik. Di sesi sepi seperti Sydney + Tokyo, spread XAUUSD bisa 40–90% dari gerak satu bar, jadi hampir tidak ada trade yang untung setelah spread. LLM yang menjawab "tunggu" terus di gaya ini sedang menilai dengan benar, bukan rusak.
- **Harian (intraday)**: bar M15, H1, atau H4. MT4 hanya mengirim M1 dan M5, jadi bar yang lebih besar dibangun dari M5 (6000 bar M5, kira-kira tiga minggu). Satu keputusan per bar yang tutup, 15 detik setelah bar tutup. Posisi ditahan paling lama **Tahan maks.** bar (bawaan 4), dan ditutup lebih awal di **Stop × ATR** (bawaan 1,5) atau **Target × ATR** (bawaan 2). Isi 0 untuk tanpa stop atau target. Setiap aturan memegang paling banyak satu posisi, jadi aturan yang trade-nya masih terbuka melewatkan bar berikutnya. LLM boleh memilih stop dan target sendiri (0,5–5 ATR). Simulasi bisa berjalan sampai 168 jam (**Batas jam**), dan server trading harus tetap hidup selama itu. Di H1, spread XAUUSD hanya sekitar 1% dari ATR, jadi LLM punya ruang untuk trade. Di H4 baru ada sekitar 130 bar, kurang dari 184 bar yang dibutuhkan matematika gabungan, jadi kandidat itu belum ikut sampai historinya cukup.

Halaman panel selalu dibaca dari disk, tapi kode Python server trading tetap versi saat server dinyalakan. Kalau keduanya beda, banner merah di atas panel meminta restart (`Ctrl+C` lalu `python main.py trading`). Server lama dulu diam-diam mengabaikan setelan yang belum ia kenal, jadi gaya Harian berjalan sebagai scalping. Sekarang setelan yang tidak dikenal ditolak, dan panel menghentikan simulasi yang tidak sesuai gaya yang dipilih.

Selama simulasi berjalan, chip di pojok kiri atas chart menampilkan simbol, siklus, menit, dan jumlah panggilan LLM, dengan tombol **Hentikan** yang selalu terlihat. Tombol **Hentikan simulasi** juga ada di bagian atas panel. Posisi yang masih terbuka ditutup di harga terakhir sebelum simulasi berhenti.

Chart pindah ke 1 menit (di gaya Harian ke timeframe yang dipilih) dan menandai trade aturan yang dipilih: panah di titik masuk, garis putus-putus ke titik keluar, dan titik hijau atau merah untuk hasilnya. Posisi yang masih terbuka diberi hitung mundur. Klik baris di tabel untuk melihat trade aturan lain. Simulasi berjalan di server, jadi tetap jalan walau tab ditutup.

Selain delapan aturan dan ensemble, ada dua kandidat lagi:

- **Matematika gabungan (14 ukuran).** Di setiap bar dihitung 14 ukuran: momentum 3, 10, dan 30 bar dalam z-score, jarak EMA 9/21 dan kemiringan Kalman dalam ATR, RSI, z Bollinger, jarak VWAP, posisi close di bar, lalu rezim pasar: eksponen Hurst (di atas 0,5 cenderung tren, di bawah cenderung balik), variance ratio, autokorelasi, dan entropi arah, masing-masing dikali arah gerak terakhir, plus lonjakan volatilitas. Regresi logistik (ridge) mempelajari bobot tiap ukuran dari bar yang sudah selesai, lalu memberi **P(naik)** setelah waktu tahan. Bar yang lebih baru berbobot lebih besar (half-life setengah sampel). Ujinya **walk-forward**: bar berlabel dipotong jadi 4 blok berurutan, setiap blok ditebak dari bobot yang dilatih hanya di blok sebelumnya, dengan jeda sebesar horizon supaya label yang menjangkau ke blok uji tidak ikut melatih (purge). Kekuatan ridge dipilih dari yang paling tepat menebak blok uji itu. Aturan ini hanya ambil arah bila hasil ujinya lebih baik dari tebakan mayoritas (selalu menebak "naik" di pasar naik) dan P(naik) minimal 5 poin dari 50%. Menambah rumus tidak otomatis membuat arah lebih jelas. Yang menentukan adalah hasil uji di bar yang belum dilihat, jadi panel selalu menampilkannya.
- **LLM.** Model yang dipilih di kotak **LLM trading** ditanya sekali per siklus. Ia melihat simbol, sesi, gerak 20 bar terakhir, hasil matematika gabungan, catatan setiap aturan sejauh ini, trade-nya sendiri berikut alasannya, kondisi pasar dari harga MT4, dan ringkasan berita. Jawabannya BUY, SELL, atau tunggu, dengan tingkat yakin dan alasan singkat. Posisinya dibuka saat jawaban datang, di harga saat itu. Jawaban yang datang setelah lewat setengah waktu tahan dilewati. Trade-nya sendiri ikut masuk ke pertanyaan berikutnya, dan dari situ ia menyesuaikan diri. Paling banyak **Maks. panggilan** per simulasi (bawaan 120). Kalau gagal 2 kali berturut-turut, model tidak ditanya lagi. Token dan perkiraan biayanya tampil di panel dan tercatat di halaman Router.

Di bawah tabel aturan ada **Matematika gabungan**: P(naik), kejelasan, hasil uji, ukuran yang paling mendorong arah, dan rezim pasar. Lalu **Per sesi**: catatan aturan yang dipilih di setiap sesi. Sesi dihitung dari jam lokal tiap kota, jadi ikut daylight saving: Sydney 07–16, Tokyo 09–18, London 08–17, New York 08–17, termasuk tumpang tindihnya seperti London + New York. Terakhir **Perhitungan per siklus**: 10 siklus terakhir dengan jam, sesi, P(naik), suara matematika, skor ensemble, dan spread.

Setiap siklus disimpan ke `trading/data/sim/<waktu>-<simbol>.jsonl`: satu baris per siklus (harga, suara semua aturan, hasil matematika beserta kontribusi tiap ukuran, sesi, kondisi pasar, mood berita), satu baris per jawaban LLM, dan satu baris per trade yang tutup. Tautan **unduh log** ada di panel. `GET /api/quant/sim/logs` mendaftar semua log, dan `SIM_LOG_DIR` memindahkan foldernya.

Di chart, posisi virtual ditandai seperti di aplikasi trading. BUY diberi badge ▲ di bawah candle dan SELL diberi badge ▼ di atas candle, dengan garis ke harga entry yang tepat. Sebelumnya panah BUY digambar di atas badan candle hijau dengan warna yang sama, jadi tidak terlihat. Posisi yang masih terbuka diberi garis level entry dan label di kanan (`BUY 12s`).

Di bawah tabel aturan ada satu baris signifikansi: aturan dengan rata-rata hasil terbaik, t-statistiknya, dan peluang hasil sebagus itu muncul karena kebetulan pada salah satu dari semua aturan yang dicoba. Tabel **Per kondisi saat masuk** memecah catatan aturan yang dipilih menurut tag kondisi (lihat bagian Checklist trader), jadi kelihatan *di mana* satu aturan menang dan kalah. LLM menerima catatannya sendiri per kondisi di pertanyaan berikutnya.

Win rate saja belum berarti untung. Tabel menampilkan **batas bawah 95%** setiap aturan, dan hasilnya memberi peringatan bila batas itu masih di bawah 50% (belum lebih baik dari lempar koin) atau di bawah target (butuh lebih banyak trade), atau bila rata-rata kalah lebih besar dari rata-rata menang. Dengan sepuluh aturan dicoba sekaligus, satu aturan bisa tembus 60% hanya karena untung. Pakai minimal trade 50 ke atas sebelum percaya hasilnya. Di backend simulasi harganya buatan, jadi hasilnya hanya untuk mencoba alur.

## Berita dan kondisi pasar

Bagian **Berita & kondisi pasar** di kolom kanan (dan `GET /api/quant/news?symbol=...`) mengumpulkan data dari sumber gratis tanpa API key:

| Sumber | Isi | Disimpan |
|---|---|---|
| Kalender ekonomi minggu ini (feed JSON ForexFactory) | Jam, mata uang, dampak, perkiraan, angka sebelumnya | 30 menit |
| CoinGecko | Perubahan market cap kripto 24 jam, dominasi BTC, koin trending, BTC dan ETH 24 jam | 5 menit |
| Crypto Fear & Greed (alternative.me) | Indeks 0–100 | 30 menit |
| RSS CoinDesk, Cointelegraph, Decrypt, FXStreet, dan Google News (makro, teknologi & AI) | Judul berita | 10 menit |

Setiap judul diberi topik dari daftar kata kunci: **influencer & tokoh**, **teknologi**, **AI**, **blockchain & kripto**, **regulasi**, **bank sentral & suku bunga**, **makro**, **geopolitik**, dan **komoditas**. Nadanya diberi -1 sampai +1 dari daftar kata positif dan negatif. Topik yang relevan dengan simbol diberi garis tepi: emas membaca komoditas, bank sentral, makro, dan geopolitik, sedangkan BTC membaca blockchain, influencer, dan regulasi. Mood keseluruhan (risk-on, netral, atau risk-off) diambil dari Fear & Greed, market cap kripto, dan nada berita. Daftar kata tidak paham sindiran atau konteks, jadi nada hanya petunjuk, bukan sinyal. LLM membaca judulnya langsung.

MT4 tidak bisa mengirim berita: MQL4 tidak punya akses ke tab News atau kalender terminal. Yang diambil dari MT4 adalah **kondisi pasar** dari harganya: ramai, normal, atau sepi (range 10 bar terakhir dibanding sebelumnya), gerak 30 bar, dan spread dibanding range bar. Ada peringatan bila spread lebih dari separuh gerak bar.

Kalender ini juga dipakai filter berita pada **Usulan kuantitatif**: entry dilewati 30 menit sebelum dan sesudah berita berdampak besar untuk mata uang simbol. Ini berlaku otomatis bila `TRADE_NEWS_FILE` kosong. Matikan dengan `TRADE_NEWS_AUTO=0`. Sumber yang gagal tetap memakai salinan terakhirnya, dan cache di `trading/data/news/` bertahan setelah restart. Tombol **Muat ulang** memaksa semua sumber diambil lagi, dan dari luar mesin ini tombol itu butuh token.

## LLM trading

Satu model dipakai untuk semua bagian trading. Pilih model di kotak **LLM trading** pada Simulasi live. Daftarnya berisi model yang sedang dilayani setiap provider di halaman Router, dikelompokkan per provider, dengan model yang baru dipakai di atas. Daftar ini disimpan 5 menit, dan tombol **Muat ulang daftar** mengambilnya lagi. Pilihan **Default Router** memakai model default halaman Router atau `DEFAULT_MODEL`, tapi hanya bila model itu masih dilayani. Model yang tidak ada di daftar ditolak saat simulasi atau forward test dimulai, jadi id lama di `.env` tidak lagi gagal di tengah jalan dengan "not found". Setelan disimpan di `trading/data/llm.json`, dan `TRADING_LLM_MODEL` bisa mengisinya dari `.env`.

| Dipakai di | Cara kerja |
|---|---|
| Simulasi live | Aturan **LLM**: posisi virtual sendiri tiap siklus |
| Forward test (bot demo) | **LLM menyetujui**: basket hanya dibuka bila LLM memilih sisi yang sama dengan ensemble. **LLM memutuskan**: LLM yang memilih BUY, SELL, atau lewati |

Di forward test, LLM hanya memilih sisi. Lot, stop, target, penolakan akun riil, batas rugi 2%, dan batas waktu tetap berlaku. Kalau LLM tidak tersedia, basket dilewati, tidak dibuka tanpa LLM. Alasan LLM tercatat di setiap basket. Setelannya ada di `GET`/`POST /api/quant/llm` (`model`, `sim_enabled`, `max_calls`, `forward_mode`). `POST /api/quant/forward/start` juga menerima `llm_mode`.

Semua panggilan lewat klien yang sama dengan chat, jadi rotasi akun Router berlaku. Tokennya tercatat di halaman Router dengan sesi `trading-sim` atau `trading-forward`. Satu siklus 30 detik berarti sampai 120 panggilan per jam, jadi pilih model murah dan pasang harganya di Router supaya biayanya terlihat. Belum ada bukti LLM ini punya keunggulan. Bandingkan barisnya dengan lempar koin dan matematika gabungan, minimal 100 trade.

## Otak trading dan LLM Analyst Trade

Panel **LLM Analyst Trade** di kolom kanan memberi rencana trade lengkap dengan entry, **SL**, dan **TP**. Rencana ini **tidak dieksekusi**. **Isi tiket** menyalinnya ke tiket order (lot dihitung dari setelan risiko), lalu Preview dan konfirmasi tetap berlaku seperti biasa. Di chart, rencana yang masih berjalan digambar sebagai garis Analyst entry, SL, dan TP.

Di belakangnya ada tiga lapis:

1. **Memori** (`trading/quant/memory.py`). Setiap bar M15, M30, H1, atau H4 yang sudah tutup disimpan bersama 109 ukuran:
   - tiga candle terakhir (badan, sumbu, range dalam ATR);
   - gerak 1 sampai 55 bar, jarak ke EMA 9 sampai 200 dan kemiringannya, RSI, stochastic, Bollinger, perubahan volatilitas;
   - high/low 20, 50, dan 100 bar, open/high/low hari ini dan kemarin;
   - 14 ukuran matematika gabungan, volume tick, jam, hari, sesi yang buka;
   - berita high-impact untuk mata uang simbol itu (berapa jam ke rilis berikutnya, berapa jam sejak rilis terakhir, berapa banyak yang dekat);
   - spread dan mood berita kalau direkam simulasi live;
   - stochastic %D dan histogram MACD;
   - **level harga** (10 ukuran): jarak ke pivot harian, posisi di pita Camarilla, berapa bagian leg terakhir yang sudah terkoreksi, arah dan besar leg itu, jarak ke level Fibonacci terdekat, umur swing, ruang ke zona confluence di atas dan di bawah, dan kekuatan zona tempat harga berdiri;
   - **jam pasar dan kalender** (9 ukuran): jam New York, jam ke berapa sejak London dan New York buka, kedekatan ke rollover, posisi dalam bulan, akhir bulan, fase bulan;
   - **musiman dan siklus** (5 ukuran): apa yang dilakukan jam berikutnya di hari-hari sebelumnya (arah dan lebar), dan fase serta kekuatan siklus terkuat di 128 bar terakhir.

   Untuk setiap bar dicatat juga apa yang terjadi pada **24 rencana**: BUY atau SELL, SL 1, 1,5, atau 2 ATR, TP 1, 1,5, 2, atau 3 ATR. Rencana dibuka di harga close (ask untuk BUY, bid untuk SELL), dan dicatat mana yang kena duluan, TP atau SL, dibaca dari bar M5 di dalamnya. Kalau keduanya kena di satu bar M5, yang dihitung SL, jadi memori tidak pernah memoles hasil. Sumbernya semua bar M5 yang pernah dikirim EA (`trading/data/bars/`), jadi memori bertambah sendiri setiap hari selama server trading dan EA hidup.
2. **Jaringan saraf** (`trading/quant/neural.py`, numpy murni).
   - Lima jaringan dengan dua lapis tersembunyi. Ukurannya ikut tumbuh bersama memori: 64-32 neuron di bawah 3.000 bar, 128-64 di bawah 20.000, lalu 256-128.
   - Setiap jaringan menebak, untuk ke-24 rencana sekaligus, peluang kena TP dulu, kena SL dulu, atau habis waktu. Dari situ dihitung nilai harapannya dalam ATR setelah spread.
   - Urutan waktu tidak pernah diacak. 70% bar terlama untuk belajar, 15% berikutnya untuk menghentikan belajar sebelum ia menghafal, mengkalibrasi peluang, dan memilih **ambang masuk**. 15% bar terbaru adalah **ujian** yang tidak pernah dipakai memilih apa pun.
   - Bar yang lebih baru berbobot lebih besar saat belajar (half-life 35% memori, bar tertua tetap berbobot minimal 0,15), jadi otak mengikuti pasar sekarang tanpa melupakan yang lama. Bobot tiap jaringan dirata-ratakan sepanjang pelatihan (Polyak averaging) dan rata-rata itulah yang dipakai menjawab: lebih stabil dari langkah terakhir yang berisik. Learning rate naik perlahan lalu turun mengikuti kosinus, gradien dipotong, dan label dihaluskan sedikit supaya jaringan tidak pernah 100% yakin.
   - Kalibrasi peluang memakai temperatur yang dicari kontinu (golden section) di data validasi. Ujian juga melaporkan **Brier** dan **ECE** (seberapa jujur p_target) serta **t-stat** dan batas bawah rata-rata hasil.
   - Ambang masuk perlu dipilih karena memilih yang terbaik dari 24 rencana selalu terlihat bagus. Ambang diambil di titik yang memaksimalkan **batas bawah total profit** pilihan otak di data validasi (jumlah trade × (rata-rata − 1 standar error)), bukan rata-rata semata: ambang dengan sedikit trade yang kebetulan untung punya batas lebar dan tidak menang.
   - Otak **lulus** hanya kalau di ujian ia untung, mengalahkan "selalu rencana yang sama" (rencana terbaik di data latih, yang menangkap tren yang kebetulan berlanjut), mengalahkan acak, dan peluangnya lebih tepat dari frekuensi dasar.
   - Tiga kelompok ukuran baru (level harga, jam pasar, musiman dan siklus) bersifat pilihan. Menambah rumus di memori yang pendek kebanyakan menambah cara untuk salah, jadi otak dilatih lima kali dengan susunan berbeda dan memakai yang paling tepat menebak data validasi. Susunan yang lebih kaya harus mengalahkan ukuran dasar dengan selisih log loss minimal 0,002. Ujian diambil sesudah pilihan itu, di bar yang tidak ikut memilih. Panel **Otak neural** menampilkan kelompok mana yang dipakai.
   - Sesudah ujian, jaringan dilatih ulang di seluruh memori.
3. **Pembanding** (`trading/quant/brain.py`):
   - 40 momen paling mirip di masa lalu, beserta hasil rencana BUY dan SELL terbaik setelahnya (juga khusus di sesi yang sama). Jaraknya menimbang tiap ukuran menurut seberapa besar jaringan mengandalkannya (norma bobot lapis pertama), dan hasilnya juga dirata-ratakan dengan kernel Gaussian pada jarak, jadi momen yang paling mirip paling berbicara (`weighted_p_target`, `weighted_avg_r_atr`);
   - tabel kondisi untuk rencana acuan SL 1,5 / TP 2 ATR: per sesi, per jam, per hari, dan dekat atau jauh dari berita besar.

**LLM Analyst** (`trading/quant/analyst.py`) membaca semua itu, ditambah candle terakhir, berita, dan **jurnal rencananya sendiri**. Jurnal itu yang membuatnya belajar lintas run dan restart:
- Setiap rencana diikuti di bar M5 sampai kena TP, kena SL, atau habis waktu.
- Saat ia memilih tunggu, rencana terbaik otak di momen itu juga diikuti, jadi kelihatan kapan menunggu itu benar dan kapan ia melewatkan yang menang.
- Aturannya: ambil arah hanya kalau minimal dua dari tiga (EV otak, situasi mirip, kondisi sekarang) condong ke arah yang sama, dan jangan masuk 30 menit sebelum rilis high-impact.
- Prompt-nya juga membawa **kalibrasi kepercayaannya sendiri** (`my_calibration`): tingkat kena TP per pita confidence, skor Brier, dan vonis seperti "terbalik" bila rencana paling yakin justru paling sering gagal, plus target rate yang disusutkan (posterior Beta(2,2)) supaya rekor 2 dari 2 tidak dibaca 100%.

Cara melatihnya lewat simulasi live:
1. Pilih **Gaya: Harian** (H1 atau M15), centang **Otak neural ikut**, nyalakan LLM, lalu mulai.
2. Setiap bar tutup:
   - kandidat **Jaringan saraf (otak)** ikut trade dengan SL/TP pilihannya;
   - LLM menulis rencana ke kartu Analyst, dan posisinya memakai SL/TP rencana itu;
   - spread dan mood bar itu disimpan untuk pelatihan.
3. Otak dilatih ulang otomatis di latar setiap 3 jam kalau ada bar baru yang sudah punya hasil.
4. Tombol **Latih ulang sekarang** melatih di luar jadwal itu.
5. **Belajar dari waktu ke waktu** menunjukkan hasil ujian setiap pelatihan, jadi kelihatan apakah otak membaik seiring memori bertambah.

Yang perlu diketahui:
- **Kalender berita.** Kalender ForexFactory hanya memberi minggu ini, jadi setiap minggu yang diambil disimpan ke `trading/data/news/calendar-archive.json`. Ukuran berita baru dipakai jaringan setelah arsip menutup minimal 30% memori. Sebelum itu baris "sekarang", yang punya data berita, terlihat sangat beda dari semua baris yang dipelajari, dan tebakannya jadi liar. Selama itu berita tetap dibaca LLM langsung.
- **Ukuran memori.** Dengan history M5 sekitar 3 minggu, H1 baru punya sekitar 370 bar dengan hasil. H4 belum cukup (butuh 300).
- **Hasil di data asli (10 November 2026).** M15 belum lulus. Ia untung di ujian, tapi peluangnya belum lebih tepat dari frekuensi dasar. H1 tidak menemukan ambang yang untung, jadi sangat pemilih.
- **Dengan ukuran baru (11 November 2026, 29 hari data XAUUSDcheck).** Di M15 otak memilih level harga dan jam pasar (log loss validasi 0,967 lawan 0,984 dengan ukuran dasar) dan membuang musiman dan siklus. Di H1 ia memilih level harga saja (1,047 lawan 1,052). Ujiannya tetap belum lulus di kedua timeframe: tebakan validasi membaik, bukti untung belum ada.
- **Otak lama dilatih ulang sendiri.** Otak yang dilatih sebelum ukuran ini ada tidak bisa menjawab untuk memori yang baru. Begitu ditanya, ia melatih ulang di latar (M15 sekitar 20 detik) dan panel mengatakannya.
- **Hasil di data buatan.** Di data buatan yang naik setiap pagi London, otak lulus dengan +0,3 sampai +1,6 ATR per trade, dan hanya mau BUY di jam itu. Artinya otak bisa menemukan pola kalau polanya memang ada. Apakah XAUUSD punya pola seperti itu, hanya ujian yang bisa menjawab.
- **Lokasi data.** Semua ada di `trading/data/brain/`: model `<simbol>-<tf>.npz`, ringkasan `<simbol>-<tf>.json`, konteks live, dan jurnal `analyst-<simbol>.json`. `TRADING_BRAIN_DIR` memindahkannya.

API: `GET /api/quant/brain?symbol=&timeframe=`, `POST /api/quant/brain/train`, `GET /api/quant/analyst?symbol=`, `POST /api/quant/analyst/run` (satu panggilan model).

## Checklist trader: level, waktu, siklus, dan matematika risiko

Sebelum memilih arah, LLM trading (di simulasi live, di Analyst, dan di auto trade) membaca checklist yang sama dengan yang digambar di panel **Level, waktu & siklus**. Semua dihitung dari bar yang sudah tutup, jadi tidak ada yang mengintip ke depan. Level adalah zona untuk bertindak, bukan ramalan.

**Level harga** (`trading/quant/levels.py`)

- **Fibonacci** dari leg swing terakhir yang sudah selesai. Retracement uptrend `High − (High − Low) × rasio` untuk 0,236, 0,382, 0,5, 0,618, 0,786. Ekstensi `Low + (High − Low) × rasio` untuk 1,272, 1,618, 2,618. Downtrend dicerminkan. Swing baru diakui setelah harga berbalik 3 ATR darinya (zigzag), jadi puncak tidak pernah ditebak.
- **Pivot** dari high, low, dan close hari trading sebelumnya, empat jenis: Klasik (`P = (H+L+C)/3`, `R1 = 2P − L`, `S1 = 2P − H`, `R2 = P + (H−L)`, `S2 = P − (H−L)`, `R3 = H + 2(P−L)`, `S3 = L − 2(H−P)`), Fibonacci (`P ± 0,382 / 0,618 / 1,0 × (H−L)`), Camarilla (`C ± (H−L) × 1,1/4` dan `× 1,1/2`), dan Woodie (`P = (H+L+2C)/4`).
- **Hari trading** dihitung dari 17:00 ke 17:00 New York, bukan hari UTC. Itu high, low, dan close yang dipakai pasar, dan batasnya ikut bergeser saat daylight saving.
- **Zona confluence.** Level yang berjarak kurang dari 0,25 ATR digabung jadi satu zona. Kekuatannya 1 sampai 4: berapa *jenis* level yang bertemu di situ (pivot, Fibonacci swing, range kemarin dan minggu lalu, angka bulat). Empat rumus pivot berasal dari tiga angka yang sama, jadi dua pivot yang berimpit dihitung satu. Zona kuat berarti 2 jenis atau lebih, misalnya Fibo 61,8% berimpit dengan S1.

**Waktu** (`trading/quant/cycles.py`)

- **Jam pasar** dibaca dari jam tiap kota: pembukaan Sydney, Tokyo, London, New York, menit rilis data AS (08:30 New York), pembukaan bursa saham AS (09:30), fix emas LBMA (10:30 dan 15:00 London), fix London 16:00, dan rollover 17:00 New York. London buka 07:00 UTC di musim panas dan 08:00 UTC di musim dingin. Beberapa minggu tiap Maret dan musim gugur, London dan New York terpaut 4 jam, bukan 5, dan panel menyebutnya.
- **Kebiasaan tiap jam.** Bar dikelompokkan per jam New York. Tiap jam punya rata-rata gerak dalam ATR, t-statistik, p-value, dan lebar bar dibanding rata-rata. Dua puluh empat jam berarti dua puluh empat percobaan, jadi p-value dikoreksi (Benjamini-Hochberg) dan satu jam baru dihitung sebagai temuan bila q di bawah 0,10. Lebar bar per jam itu nyata dan stabil. Arahnya jarang lolos uji, dan panel mengatakannya.
- **Siklus.** Periodogram dari return 128 bar terakhir. Yang dipakai return, bukan harga: spektrum harga random walk selalu memuncak di periode terpanjang dan terlihat seperti siklus padahal bukan. Siklus baru dipakai kalau lolos uji Fisher g dan uji acak-ulang (199 kali mengacak urutan return), dua-duanya di bawah 5%. Kalau lolos, fasenya memberi arah sekarang dan jarak ke puncak dan lembah berikutnya.
- **Waktu Fibonacci.** Jumlah bar sejak swing terakhir dan hitungan Fibonacci terdekat (8, 13, 21, 34, ...). Hitungan ini ditampilkan tapi tidak diuji sendiri; otak menilainya lewat umur swing.
- **Kalender lambat.** Posisi dalam bulan, akhir bulan, dan fase bulan ikut diukur, tapi otak baru membacanya setelah memori mencakup 90 hari. Di bawah itu "tanggal" hanya jam yang terus naik dan akan mengajari otak tren beberapa minggu itu.

**Matematika risiko** (`trading/quant/riskmath.py`)

- Dasar: ukuran posisi `(modal × %risiko) / (jarak SL × nilai per poin)` yang dibulatkan ke bawah, R:R, win rate impas `1/(1+RR)` dan `(1+biaya)/(1+RR)` dengan spread, expectancy, Kelly, drawdown dan `Recovery = DD/(1−DD)`, nilai pip, margin, compounding.
- Yang membuat akun bertahan: win rate dari sedikit trade diganti angka hati-hati (posterior Beta, rata-rata dikurangi satu simpangan), Kelly dihitung setengah dari angka hati-hati itu dan dibatasi 2%, peluang rugi beruntun dihitung eksak, dan risk of ruin diselesaikan dari persamaan gambler's ruin untuk menang dan kalah yang tidak sama besar.
- Rata-rata hasil diuji dengan t-test. Kalau beberapa aturan dicoba sekaligus, p-value dikoreksi (Šidák), jadi "terbaik dari 12" dinilai sebagai terbaik dari 12.
- `GET /api/quant/riskmath?win_rate=0.45&rr=2&risk_pct=1&trades=100` memberi semua angka itu. Panel punya **Kalkulator risiko** yang memakainya.

**Cara LLM belajar dari checklist** (`trading/quant/playbook.py`)

1. Prompt membawa blok `levels` (zona di atas dan di bawah harga, ruang ke zona kuat, leg Fibonacci, pivot), `time` (jam, event berikutnya, kebiasaan jam ini, siklus bila lolos uji), dan `plan_math` (R:R, tingkat TP yang baru impas, selisih peluang otak dari titik itu).
2. Setiap rencana dan setiap posisi simulasi diberi **tag kondisi**: bagian hari, masuk "dari zona" atau "menuju zona dekat" atau di "ruang terbuka", searah atau melawan pivot, searah atau melawan leg dan sedalam apa koreksinya, searah atau melawan siklus.
3. Prompt berikutnya membawa `my_record_like_now`: hasil rencana lama yang dibuat di kondisi yang sama dengan sekarang, misalnya "sell menuju zona dekat: 0 dari 4 kena TP". `my_math` memberi catatannya dalam R berikut t-test, jadi beberapa kemenangan tidak dibaca sebagai keahlian.
4. Setiap rencana dibaca ulang dan diberi **peringatan** di kartu Analyst: spread lebih dari 15% jarak stop, P(TP) otak tidak di atas titik impas, zona kuat menghalang sebelum target, stop tepat di depan zona, jam rollover, menit data AS, atau posisi menginap akhir pekan. Peringatan tidak memblokir apa pun. Kodenya ikut jadi tag, jadi jurnal menunjukkan berapa harga mengabaikannya.

Di panel: tombol **Level** di atas chart menggambar zona (pita untuk zona kuat, garis tipis untuk level tunggal) dan empat pita sesi di tepi atas chart. Panel **Level, waktu & siklus** memakai timeframe yang dipilih di kartu Analyst. Kartu Analyst menampilkan TP impas, selisih dari impas, EV dalam R, setengah Kelly, peringatan, dan tag. Di bawahnya ada catatan per kondisi. Simulasi live menampilkan tabel **Per kondisi saat masuk** untuk aturan yang dipilih dan satu baris signifikansi.

API: `GET /api/quant/considerations?symbol=&timeframe=` (M5 sampai H4). Agent chat membacanya lewat `trading_snapshot(symbol=..., sections=["levels"])`.

## Laporan walk-forward di data harian

```bash
python -m trading.quant.wf_report --symbol XAUUSD      # sekitar 5 detik, hanya paper
```

Membaca `trading/data/bars/XAUUSD1440.csv` dan menguji jaringan harian (`forecast.oos_predictions`): tiap prediksi dibuat oleh jaringan yang dilatih hanya dengan hari yang hasilnya sudah diketahui saat itu. Laporannya (`reports/trading/walkforward-XAUUSD-D1-<tanggal>.md` dan `.json`) berisi, per horizon 1 dan 5 hari:

- hit rate melawan tebakan konstan terbaik, dengan interval 99% dan p-value binomial eksak;
- skor Brier melawan "selalu tebak base rate", dan hit rate per refit, supaya satu periode beruntung tidak membawa rata-ratanya;
- untuk horizon 1 hari, P&L dari memperdagangkan sinyalnya (long di atas 0,52, short di bawah 0,48) dengan biaya 2 bps tiap ganti posisi, dibanding buy-and-hold dan dua aturan sederhana (arah kemarin, momentum 20 hari). Sharpe diberi interval block-bootstrap, dan uji permutasi mengocok sinyal ke hari acak untuk bertanya seberapa sering kebetulan saja sebaik itu.

Vonisnya **EDGE** hanya bila hit rate, interval Sharpe, dan uji permutasi sepakat. Dua horizon diuji, jadi satu lolos adalah petunjuk untuk dikonfirmasi di data baru, bukan bukti. Hasil 2026-11-12 pada 2048 hari: **tidak ada edge** (1 hari: 51,9% melawan tebakan konstan 53,3%; Sharpe long/short 0,25 melawan buy-and-hold 0,82; Brier lebih buruk 8,5% dari base rate, jadi jaringan terlalu yakin). Itu cocok dengan yang dikatakan prompt analis.

## Kartu bagikan

Tombol **Bagikan** di setiap posisi terbuka dan riwayat trade, **Bagikan portofolio** di atas tabel, dan **Bagikan hasil** di simulasi membuat kartu PNG 1080×1350 seperti di exchange: simbol, LONG/SHORT, profit besar, grafik harga atau kurva P/L, dan rinciannya. Ada tiga gaya (Gelap, Neon, Terang) dan pilihan **Sembunyikan nominal**, yang menyembunyikan angka uang dan hanya menampilkan persen atau hasil. Kartu dibuat di browser dan baru keluar dari halaman saat kamu menekan Unduh, Salin, atau Bagikan.

## Forward test demo

`POST /api/quant/forward/start` dengan `{"symbol": "...", "minutes": 30}` menjalankan ensemble scalp dengan posisi sungguhan: 5 posisi 0,01 lot tiap 30 detik, dengan SL dan TP. Status ada di `GET /api/quant/forward/status`, dan `POST /api/quant/forward/stop` menghentikannya. Tiap basket dicatat di `trading/data/forward/`.

Runner ini mengirim order tanpa konfirmasi, jadi ia menolak akun riil, berhenti sendiri di batas waktu (paling lama 120 menit), saat rugi 2% ekuitas awal, atau setelah 3 kali gagal berturut-turut. Posisi lain tidak disentuh.

## Auto trade: otak dan LLM mengirim order, trailing stop sebagai jalan keluar

Panel **Auto trade** (`trading/quant/autotrade.py`) membuat otak neural, atau LLM Analyst, membuka order sungguhan di akun yang terhubung. Satu simbol, satu timeframe intraday (M15 sampai H4), satu posisi sekaligus.

Cara kerjanya:
- Di setiap bar yang tutup, otak ditanya (`brain.analyse`). Mode **Otak neural** mengambil rencana terbaik otak bila otak mau masuk. Mode **LLM Analyst** menanyai LLM dengan bukti yang sama seperti tombol Analisa sekarang, dan memakai sisi, stop, target, dan `trail_atr` jawabannya. Mode **Otak & LLM sepakat** hanya masuk bila keduanya memilih sisi yang sama.
- Order dikirim dengan **SL asli di terminal dan tanpa TP**. Level target hanya mempersenjatai trailing: begitu harga menyentuhnya, SL dikunci `lock_atr` ATR di belakang target (bawaan 0,3), lalu mengikuti harga terbaik sejauh `trail_atr` ATR (bawaan 1,0) dan tidak pernah dilonggarkan. Tiap pergeseran dikirim ke terminal lewat `modify`. Posisi ditutup oleh SL saat harga berbalik, atau oleh runner saat waktu tahan habis.
- Matematika trailing ada di `trading/quant/trail.py`, dan dipakai sama persis di simulasi live (isian **Trailing × ATR** di gaya Harian, untuk posisi otak dan LLM) dan di jurnal Analyst: rencana dengan `exit_style: trail` diikuti di bar M5 dengan aturan yang sama, dan selesai dengan status `trail`. Jadi yang dipelajari LLM dari jurnalnya adalah gaya keluar yang benar-benar dipakai.
- Batas yang tidak bisa dilepas: akun live ditolak kecuali **izinkan akun live** dicentang saat mulai; lot paling besar 1; berhenti sendiri di batas jam, saat rugi hari ini menyentuh **rugi harian maks. %** dari ekuitas awal hari, atau setelah 3 order ditolak berturut-turut. Posisi yang bukan dibukanya tidak disentuh, dan posisinya sendiri ditutup saat run berhenti.
- API: `POST /api/quant/auto/start` (`symbol`, `timeframe`, `mode`, `lots`, `trail_atr`, `lock_atr`, `max_daily_loss_pct`, `max_hours`, `allow_live`), `POST /api/quant/auto/stop`, `GET /api/quant/auto/status`. Log per run di `trading/data/autotrade/`, dan tiap order serta penutupan masuk jurnal trade dengan `source: auto`.

Otak harus sudah dilatih untuk simbol dan timeframe itu; kalau belum, start memulai pelatihan di latar dan minta coba lagi. Mulai dari akun demo dan lot kecil: ujian otak di data asli belum lulus, dan trailing di akun sungguhan menanggung slippage yang tidak ada di simulasi.

## Data yang tersimpan

Semua ada di `trading/data/` dan tidak ikut Git: `journal.jsonl` (order dan close, termasuk SL, TP, dan tutup manual di MT4 lewat EA terbaru), `bars/`, `ticks/`, `forward/`, `sim/` (perhitungan per siklus simulasi live), `news/` (cache dan arsip kalender berita), `brain/` (otak neural dan jurnal Analyst), dan `llm.json` (setelan LLM trading). Folder ini hanya ada di mesin ini, jadi cadangkan sendiri.

## Status fitur

| Fitur | Status |
|---|---|
| Harga, bar M5, akun, posisi lewat bridge MT4 | Sudah jalan di demo |
| Order, tutup, ubah SL/TP, batal order tertunda, riwayat lewat bridge MT4 | Sudah dibuat, baru diuji lewat tes otomatis dan simulator. Uji dulu di demo |
| Lot lintas mata uang memakai tick value dari EA | Sudah dibuat, butuh EA versi terbaru |
| LLM mengatur setelan backtest scalp setelah rugi | Sudah dibuat, belum dicoba dengan model sungguhan |
| Eksekusi otomatis scalp 30 detik | Belum ada. Hanya backtest |
| Tinjauan LLM atas usulan (`trading/advisor.py`) | Belum, baru menyusun teksnya |
| LLM trading di simulasi live dan forward test | Sudah dibuat, baru diuji dengan model tiruan. Belum dicoba dengan model sungguhan |
| Matematika gabungan 14 ukuran | Sudah dibuat. Belum ada bukti keunggulan di data live |
| Berita dan kondisi pasar | Sudah jalan dengan sumber gratis. Berita dari MT4 tidak mungkin di MQL4 |
| Filter berita | Otomatis dari kalender ekonomi, atau dari `TRADE_NEWS_FILE` bila diisi |
| EA bridge untuk MT5 | Belum ada |
| Otak neural (memori, ensemble, ujian, situasi mirip) | Sudah dibuat dan diuji. Menemukan pola di data buatan; di XAUUSD asli belum lulus ujian |
| Trailing stop (simulasi, jurnal Analyst, auto trade) | Sudah dibuat dan diuji di simulator; belum dicoba di akun demo sungguhan |
| Auto trade (otak/LLM kirim order, SL mengikuti harga) | Sudah dibuat dan diuji dengan broker palsu; coba di demo dengan lot kecil dulu |
| LLM Analyst Trade (rencana SL/TP, Isi tiket, jurnal yang diikuti sampai selesai) | Sudah dibuat, diuji dengan model tiruan. Belum dicoba dengan model sungguhan |
| Checklist trader (level, jam pasar, musiman, siklus, matematika risiko) | Sudah dibuat dan diuji lewat tes otomatis dan data M5 yang terekam. Belum dicoba dengan model sungguhan |
| Tag kondisi dan peringatan rencana | Sudah dibuat, diuji dengan model tiruan |
| Bukti strategi punya keunggulan | Belum ada. Tidak ada jaminan profit |

Setelah server trading di-restart, EA versi terbaru mengirim ulang history-nya dalam satu menit.
