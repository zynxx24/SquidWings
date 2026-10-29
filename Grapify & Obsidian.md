# Arsitektur memori berlapis: Obsidian + Graphify

Penjelasan sebelumnya hanya membagi dua peran, yaitu Obsidian sebagai database dan Graphify sebagai cache. Dalam praktik, ada empat lapisan, mirip hierarki memori komputer. Pembagian lapisan ini rancangan saya, bukan standar resmi kedua alat.

```
┌─────────────────────────────────────────────────────────┐
│ L0  KONTEKS AKTIF (jendela chat)         ~puluhan ribu token │
│     hanya yang dimuat untuk tugas sekarang                │
├─────────────────────────────────────────────────────────┤
│ L1  BOOT FILE (CLAUDE.md, hot-notes)     ~1–3 ribu token │
│     dibaca otomatis tiap sesi: aturan, peta, pointer      │
├─────────────────────────────────────────────────────────┤
│ L2  GRAPH INDEX (Graphify)               "cache" terstruktur │
│     node = konsep, edge = relasi, ringkasan per node      │
│     murah di-query, bisa dibangun ulang kapan saja        │
├─────────────────────────────────────────────────────────┤
│ L3  VAULT (Obsidian, file .md)           "database" sumber kebenaran │
│     catatan penuh, log sesi, keputusan, riset mentah      │
└─────────────────────────────────────────────────────────┘
   baca: L1 → L2 → L3 (turun hanya jika perlu)
   tulis: L0 → L3 (lalu L2 diperbarui)
```

Aturan emasnya: **L3 adalah satu-satunya sumber kebenaran.** L2 boleh dihapus lalu dibangun ulang tanpa kehilangan apa pun. Itulah yang membuatnya cache, bukan database.

## 1. Lapisan L3: struktur vault

Contoh struktur untuk memori chat jangka panjang:

```
vault/
├── CLAUDE.md                  ← L1: pintu masuk memori
├── 00-inbox/                  ← catatan mentah dari sesi
├── 10-sessions/
│   └── 2026-10-26-ide-kasir.md   ← ringkasan tiap sesi chat
├── 20-concepts/               ← 1 konsep = 1 file (atomik)
│   ├── offline-first.md
│   └── pricing-umkm.md
├── 30-decisions/              ← keputusan + alasannya
│   └── ADR-003-pakai-sqlite.md
├── 40-people-projects/
├── 90-meta/
│   └── graph-report.md        ← keluaran Graphify
└── graphify-out/graph.json    ← L2 (bisa dibuang)
```

Setiap catatan pakai **frontmatter** supaya mudah difilter, plus `[[wikilink]]` supaya jadi edge:

```markdown
---
type: decision
status: active
updated: 2026-10-26
confidence: medium
---
# ADR-003: Pakai SQLite lokal
Alasan: [[offline-first]] penting karena koneksi pelanggan tidak stabil.
Konflik dengan: [[sync-multi-device]]
Sumber: [[2026-10-26-ide-kasir]]
```

Dua field penting adalah `status` (aktif/usang) dan `confidence`. Tanpa keduanya, memori lama yang sudah salah akan diperlakukan AI sama dengan fakta baru.

## 2. Lapisan L2: apa yang dilakukan Graphify

Graphify memindai vault lalu membuat:

- **Node**: konsep, keputusan, orang, modul. Setiap node punya ringkasan pendek (1–3 kalimat).
- **Edge** bertipe: `bergantung_pada`, `bertentangan_dengan`, `menggantikan`, `mendukung`. Edge yang berasal dari wikilink eksplisit lebih bisa dipercaya daripada edge hasil inferensi AI. Idealnya keduanya diberi label berbeda.
- **Komunitas/cluster**: kelompok node yang saling rapat, misalnya "klaster pricing" dan "klaster teknis".
- **Hub**: node dengan banyak koneksi, yang sering menjadi tema inti proyek.

Contoh penghematan, dengan angka ilustrasi (bukan hasil ukur):

| Pendekatan | Yang dimuat ke konteks | Token kira-kira |
|---|---|---|
| Baca seluruh vault | 300 catatan | 150.000+ (tidak muat) |
| RAG biasa | 8 potongan teks mirip | 6.000 |
| Graph dulu | peta 40 node (ringkasan) → buka 3 catatan | 2.500–4.000 |

Keunggulan graph atas RAG ada pada **relasi**. RAG menemukan teks yang mirip dengan pertanyaan, sedangkan graph menemukan hal yang *terhubung* walaupun kata-katanya tidak mirip.

## 3. Siklus hidup memori

1. **Capture**: selama chat, AI menandai hal yang layak disimpan, seperti keputusan, hipotesis, pertanyaan terbuka, dan fakta baru.
2. **Distill**: di akhir sesi dibuat ringkasan ke `10-sessions/`. Simpan *kesimpulan*, bukan transkrip penuh, karena transkrip penuh menjadi noise.
3. **Atomize**: ide baru dipecah ke `20-concepts/` satu file per konsep dan ditautkan.
4. **Index**: jalankan Graphify (idealnya inkremental, hanya file yang berubah) untuk memperbarui L2.
5. **Retrieve**: sesi berikutnya membaca L1 → query graph → buka L3 secukupnya.
6. **Reconcile**: bila ada konflik, catatan lama diberi `status: superseded` dan ditautkan ke yang baru. Jangan ditimpa diam-diam, karena riwayat alasan keputusan itu berharga.
7. **Prune**: berkala, tinjau node yang tidak punya tautan (orphan) atau sudah usang.

## 4. Contoh brainstorming nyata

Skenario ilustrasi: Anda membangun aplikasi kasir untuk UMKM dan sudah melakukan beberapa sesi chat selama berminggu-minggu.

**Sesi 1 (hari ke-1).** Anda memberi tahu AI bahwa target pengguna adalah warung kecil yang sinyalnya kadang hilang. Di akhir sesi, AI menulis:

- `20-concepts/offline-first.md` (hipotesis: pengguna butuh kasir yang tetap jalan tanpa internet)
- `30-decisions/ADR-003-pakai-sqlite.md`
- `00-inbox/pertanyaan-terbuka.md`: "Bagaimana sinkronisasi kalau pemilik punya 2 HP?"

**Sesi 5 (3 minggu kemudian).** Anda membuka chat baru dan menulis:

> "Aku mau brainstorming fitur laporan harian untuk pemilik. Pakai memori vault, jangan mulai dari nol."

Alur di balik layar:

1. AI membaca `CLAUDE.md` (L1) dan tahu ada proyek kasir, plus aturan "baca graph dulu".
2. AI meng-query graph untuk "laporan harian" dan mendapat node terdekat: `offline-first`, `pricing-umkm`, `sync-multi-device`, `ADR-003`.
3. AI menyadari *edge* penting: `laporan harian → butuh data lengkap → bertentangan dengan → sync-multi-device (pertanyaan terbuka)`.
4. AI membuka hanya 3 catatan terkait di L3.

Contoh jawaban AI:

> "Dari memori proyek: kamu sudah memutuskan SQLite lokal (ADR-003) karena sinyal tidak stabil, dan masalah sinkronisasi 2 HP masih terbuka. Itu berimbas langsung ke laporan harian: kalau pemilik membuka laporan di HP lain, datanya bisa tidak lengkap.
> Tiga arah brainstorm:
> A. Laporan 'per perangkat' dengan label jelas, sederhana tapi jujur.
> B. Kirim ringkasan harian lewat WhatsApp dari perangkat kasir, tanpa perlu sinkronisasi penuh.
> C. Sinkronisasi 'eventual' hanya untuk agregat harian, bukan transaksi detail.
> Opsi B paling selaras dengan keputusan sebelumnya. Mau kita uji asumsinya: apakah pemilik UMKM target kamu memang aktif di WhatsApp?"

Perhatikan dua hal. AI menghubungkan **pertanyaan terbuka lama** dengan **topik baru**, yaitu sesuatu yang sulit dilakukan chat tanpa memori. AI juga menyebut sumbernya (ADR-003), jadi Anda bisa memverifikasi.

**Penutup sesi 5.** AI menulis `30-decisions/ADR-005-laporan-via-wa.md` dengan `confidence: low`, menautkannya ke pertanyaan terbuka, lalu memperbarui graph. Node baru muncul, dan pertanyaan lama kini punya jawaban sementara.

## 5. Template prompt untuk brainstorming dengan memori

- **Mulai sesi:** "Baca CLAUDE.md, lalu query graph untuk topik X. Rangkum apa yang sudah kita putuskan, apa yang masih terbuka, dan apa yang saling bertentangan sebelum kita mulai."
- **Cari koneksi tersembunyi:** "Cari 3 node yang tidak terhubung langsung tapi berbagi tetangga yang sama. Apa ide yang muncul dari pertemuan keduanya?"
- **Uji keputusan lama:** "Tampilkan semua keputusan berstatus active yang `confidence` rendah. Mana yang paling layak ditinjau ulang?"
- **Tutup sesi:** "Buat ringkasan sesi, daftar konsep baru (satu file tiap konsep), keputusan dengan alasan, dan pertanyaan terbuka. Tandai mana yang fakta dan mana yang hipotesis."

## 6. Titik rawan dan cara mengatasinya

| Masalah | Penyebab | Mitigasi |
|---|---|---|
| Memori salah terus dipakai | Tidak ada penanda usang | `status` + `superseded_by` di frontmatter |
| Graph tidak sinkron dengan catatan | Cache basi | Bangun ulang inkremental setiap akhir sesi |
| Edge hasil inferensi keliru | AI menebak relasi | Bedakan edge eksplisit (wikilink) dan inferensi, minta AI menyebut sumbernya |
| Vault menggembung dengan noise | Menyimpan transkrip penuh | Simpan kesimpulan, bukan percakapan |
| AI "mengarang" memori | Tidak ada pengecekan | Wajibkan AI mengutip nama file; kalau tidak ada, katakan "tidak ada di memori" |
| Privasi | Catatan sensitif ikut terbaca | Pisahkan folder sensitif, kecualikan dari indeks |

## 7. Setup minimum untuk mencoba

1. Buat vault Obsidian dengan folder di atas.
2. Tulis `CLAUDE.md` berisi tiga hal: tujuan vault, aturan baca (graph dulu, lalu catatan), dan aturan tulis (akhir sesi: ringkasan + konsep atomik + pertanyaan terbuka).
3. Jalankan agen yang punya akses file (misalnya Claude Code) di folder vault.
4. Setelah beberapa sesi, jalankan Graphify dan buka hasilnya di graph view Obsidian untuk melihat klaster dan orphan.

Karena Anda di paket gratis, perlu diingat bahwa langkah 3 butuh agen berbasis file lokal di luar aplikasi chat ini. Kalau mau, saya bisa menuliskan isi `CLAUDE.md` lengkap atau template catatan sesi yang siap Anda salin.
