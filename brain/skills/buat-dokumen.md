---
name: buat-dokumen
description: Membuat file jadi - Word (.docx), PowerPoint (.pptx), Excel (.xlsx), CSV, dan PDF - yang rapi, lalu langsung menampilkannya di chat untuk diunduh.
tasks: activity, school_task, reason
triggers: docx, ms word, file word, format word, dokumen word, bentuk word, ke word, jadi word, word nya, ppt, pptx, powerpoint, power point, presentasi, slide, slides, excel, xlsx, spreadsheet, lembar kerja, csv, pdf, file dokumen, bentuk file, dalam bentuk file
scope: contextual
context: buat, buatkan, bikin, bikinin, bikinkan, jadikan, ubah, ubahkan, convert, simpan, export, ekspor, kirim, kirimkan, download, unduh, file, format, template, rapikan, susun, siapkan, generate, make, create
tools: doc_create, slides_create, sheet_create, office_to_pdf, share_file, table_convert
---
# Membuat dokumen jadi

Pengguna minta file, bukan teks di chat. Tulis isinya dengan baik, lalu biarkan tool yang memformat. Jangan membuat file dengan `execute_command` atau menulis XML sendiri.

## Pilih tool

| Diminta | Tool |
|---|---|
| Laporan, makalah, surat, proposal, CV, notulen, modul (Word) | `doc_create(output, markdown, title, ...)` |
| Presentasi, slide, bahan ppt | `slides_create(output, slides)` |
| Tabel, rekap, daftar, anggaran, jadwal (Excel/CSV) | `sheet_create(output, sheets)` |
| Versi PDF dari file di atas | buat dulu filenya, lalu `office_to_pdf(path)` |
| Tabel CSV/Excel yang sudah ada diubah formatnya | `table_convert` |

Semua tool ini menampilkan file di chat sendiri (`share` bawaan true). Jangan panggil `share_file` lagi untuk file yang sama, dan jangan tempel ulang isi dokumen di jawaban.

## Word

- Tulis seluruh isi sebagai Markdown: `#` judul bab, `##` subbab, daftar `-` dan `1.`, tabel Markdown, `**tebal**`. Gambar dari workspace: `![Grafik](grafik.png)`. Ganti halaman: baris `---`.
- Judul dokumen lewat `title`; nama, NIM, kelas, tanggal lewat `subtitle` (satu baris per item). `cover_page=true` untuk halaman sampul.
- Format akademik Indonesia kalau diminta "sesuai format kampus/sekolah" atau tidak disebut untuk makalah/skripsi: `font="Times New Roman"`, `font_size=12`, `line_spacing=1.5`, `margins_cm=[3, 3, 3, 4]`, `justify=true`. Surat dan dokumen kantor: Calibri 11, spasi 1.15.
- Isi harus lengkap dan benar. Kalau ini tugas sekolah, ikuti juga panduan `jawab-tugas` untuk gaya bahasanya.

## PowerPoint

- Slide pertama `layout: "title"` dengan `subtitle` (nama, kelas, tanggal). Lalu satu gagasan per slide.
- Paling banyak 5-6 poin per slide, tiap poin satu baris pendek. Penjelasan panjang taruh di `notes` (catatan pembicara), bukan di slide.
- Bandingkan dua hal dengan `left` dan `right`; data dengan `table {headers, rows}`; gambar dari workspace dengan `image`.
- Akhiri dengan slide kesimpulan dan, kalau perlu, sumber.

## Excel dan CSV

- Angka ditulis sebagai angka (`15000`, bukan `"Rp15.000"`); format tampilannya lewat `number_formats` (`"rupiah"`, `"#,##0"`, `"0.00%"`, `"date"`).
- Total pakai `totals: ["Jumlah"]` atau rumus yang dimulai `=`, supaya tetap hidup kalau angkanya diubah. Jangan menghitung total di kepala.
- Beberapa tabel berbeda jadi beberapa sheet. CSV hanya satu tabel; untuk Excel berbahasa Indonesia pakai `csv_delimiter=";"`.

## Ukuran dan nama file

- Simpan di folder yang jelas, mis. `dokumen/laporan-praktikum.docx`. Nama tanpa spasi aneh.
- Ada batas ukuran file yang bisa dibagikan ke chat (lihat `warning` di hasil tool). Kalau terlampaui, kecilkan gambar atau pecah filenya, lalu buat ulang.

## Jawaban di chat

Dua sampai tiga kalimat: file apa yang dibuat, isinya garis besar (jumlah halaman/slide/baris), dan apa yang perlu dicek atau diisi sendiri oleh pengguna (mis. nama dosen, tanda tangan). File muncul otomatis di bawah jawaban.
