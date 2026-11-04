# Panel trading

Backend trading bisa berjalan sendiri, terpisah dari agent:

```bash
sudo sysctl -w net.ipv4.ip_unprivileged_port_start=80   # sekali, hilang setelah reboot
python main.py trading                                  # panel di http://127.0.0.1/trading
```

Mode ini dipakai untuk EA MT4. MT4 menolak URL berport dengan error 5200, jadi daftar WebRequest dan `BridgeUrl` diisi `http://127.0.0.1` saja, yang berarti port 80. Web UI agent tetap di port 8080. Tiap server menyimpan data EA sendiri, jadi buka panel di server yang sama dengan `BridgeUrl` EA.

Panel juga tersedia di `/trading` pada Web UI, lewat tautan Trading di sidebar. Panel ini punya tiga backend:

| Backend | Cara kerja | Kebutuhan |
|---|---|---|
| `sim` | Pasar simulasi untuk mencoba panel, tanpa broker | Tidak ada |
| `mt4` | EA `trading/mt4/Mt4Bridge.mq4` mengirim harga lewat HTTP dan menerima perintah order | MT4, juga bisa lewat Wine. Tekan **Hubungkan EA** untuk langkah pemasangannya |
| `mt5` | Terhubung langsung lewat paket Python `MetaTrader5` | Windows dengan terminal MT5. Paket ini tidak jalan di Linux, dan belum ada EA bridge untuk MT5 |

Panel memakai warna, huruf, dan pengaturan tema yang sama dengan Web UI (`web/static/css/tokens.css`). Panel memenuhi layar dan mengikuti ukuran jendela. Di chart: roda mouse untuk zoom, seret untuk geser, seret sumbu harga untuk meregangkan, klik dua kali untuk reset. Tombol keyboard: `F` fokus chart, `+` dan `-` zoom, panah untuk geser, `0` kembali ke bar terbaru, `Esc` keluar. Garis antara chart dan tabel bisa diseret. Klik baris posisi untuk membuka simbolnya. Di backend `mt4`, timeframe 15m sampai 1D dibangun dari bar M5.

Usulan kuant memakai aturan Kalman dan HMM pada bar M5 yang sudah tutup, lalu menghitung lot dan mengecek batas risiko. Masuk hanya saat rezim trend dominan. Entry dilewati bila spread lebih dari 15% jarak stop atau ADX turun dalam 3 bar. Backtest walk-forward memakai aturan yang sama.

Usulan ini berbasis aturan, bukan model AI, dan tidak menjamin profit. `trading/advisor.py` baru menyusun teks untuk ditinjau LLM dan belum memanggil model. Uji dulu di akun demo.

Tidak ada order yang terkirim otomatis. Setiap order melewati preview dan konfirmasi, batas volume, dan stop loss wajib. Akun real ditolak kecuali `MT5_ALLOW_LIVE=1`. EA MT4 juga tidak mengirim order kecuali `AllowTrading=true`. Akses dari luar mesin ini butuh `MT5_TRADE_TOKEN`.

EA menyimpan secret di `MQL4/Files/mt4_bridge_secret.txt` bila input `BridgeSecret` kosong. Isi file itu sama dengan `trading/data/bridge_secret`.

## Prosedur praktik

Ikuti urutan ini setiap sesi. Lakukan di akun demo sampai hasilnya layak dinilai.

**Sebelum mulai**

1. Sekali per boot: `sudo sysctl -w net.ipv4.ip_unprivileged_port_start=80`.
2. Jalankan `python main.py trading`, biarkan terminalnya terbuka.
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
2. Set `MT5_TRADE_TOKEN` bila server dibuka ke luar mesin.
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

## Status fitur

| Fitur | Status |
|---|---|
| Harga, bar M5, akun, posisi lewat bridge MT4 | Sudah jalan di demo |
| Order, tutup, ubah SL/TP, batal order tertunda, riwayat lewat bridge MT4 | Sudah dibuat, baru diuji lewat tes otomatis dan simulator. Uji dulu di demo |
| Lot lintas mata uang memakai tick value dari EA | Sudah dibuat, butuh EA versi terbaru |
| LLM mengatur setelan backtest scalp setelah rugi | Sudah dibuat, belum dicoba dengan model sungguhan |
| Eksekusi otomatis scalp 30 detik | Belum ada. Hanya backtest |
| Tinjauan LLM atas usulan (`trading/advisor.py`) | Belum, baru menyusun teksnya |
| Filter berita | Hanya aktif bila `TRADE_NEWS_FILE` diisi |
| EA bridge untuk MT5 | Belum ada |
| Bukti strategi punya keunggulan | Belum ada. Tidak ada jaminan profit |

Setelah server trading di-restart, EA versi terbaru mengirim ulang history-nya dalam satu menit.
