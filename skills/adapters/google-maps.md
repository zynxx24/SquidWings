---
skill: google-maps
upstream: guide
fallback: true
---
# Google Maps dan lokasi

Kamu membantu orang memakai data tempat: daftar bisnis hasil scrape Google Maps, tempat terdekat, jarak, dan link peta. Semua angka (rating, jarak, jumlah) datang dari tool, bukan dari ingatanmu.

## Pilih jalur yang benar

1. **Ada file hasil scrape** (CSV/JSON dari gosom/google-maps-scraper atau ekspor lain) di workspace atau baru diunggah: panggil `gmaps_import(path, query)` sekali. `query` adalah apa yang dicari, mis. "klinik gigi bandung". Setelah itu pakai `places_search`, `places_stats`, `places_export`. Jangan membaca CSV besar dengan `read_file` lalu menghitung sendiri.
2. **Sudah pernah diimpor di chat ini**: langsung `places_search` / `places_stats`. `places_datasets` menunjukkan dataset yang ada.
3. **Belum ada data dan pengguna ingin scrape**: `gmaps_scrape(queries)` hanya kalau scraper terpasang. Itu butuh beberapa menit, jadi bilang dulu ke pengguna. Kalau belum terpasang, sampaikan cara pasang dari hasil tool, atau minta mereka mengunggah ekspor.
4. **Pertanyaan "terdekat" tanpa data scrape** (apotek, ATM, SPBU, masjid, rumah sakit terdekat): `osm_nearby(kind, near)`. Kalau lokasi pengguna belum jelas, tanya alamat atau titiknya. Jangan mengarang lokasi pengguna. Kalau hasilnya membawa `note` "none within", bilang bahwa tidak ada dalam radius itu lalu sebut yang terdekat beserta jaraknya.
4b. **Cari tempat menurut nama atau jenis tanpa data scrape** ("seafood di Jimbaran", "kafe di Dago"): `places_search(text, near=area)` langsung mencari live di OpenStreetMap. Hasilnya tanpa rating; sertakan `google_maps_search` supaya pengguna bisa membuka pencarian yang sama di Google Maps. Untuk "terbaik" atau rating, tambah `web_search` ulasan (mis. "seafood Jimbaran ulasan terbaik") dan sebut sumbernya; jangan mengarang rating.
5. **Alamat ke koordinat**: `geocode`. Koordinat ke alamat: `reverse_geocode`. Jarak: `geo_distance` (garis lurus, sebutkan itu; jalan sebenarnya lebih jauh). Link untuk dibagikan atau rute: `maps_link`.

## Menyaring dan merangking

- "Terbaik" atau "rekomendasi": urutkan dengan `sort="score"`. Skor itu rata-rata Bayes, jadi 4,7 dengan 900 ulasan mengalahkan 5,0 dengan 3 ulasan. Sebutkan rating dan jumlah ulasannya.
- "Terdekat dari X": `near="X"` (alamat atau "lat,lon"), `sort="distance"`, dan `radius_km` kalau pengguna memberi batas.
- Leads untuk jasa website: `has_website=false`. Untuk riset pasar atau kompetitor: `places_stats` (kategori, kota, sebaran rating, persen yang punya website).
- Tempat yang tutup permanen dilewati secara bawaan.

## Privasi

Nomor telepon dan email dibuang saat impor. Simpan kontak (`keep_contacts=true`) hanya kalau pengguna jelas memintanya dan untuk keperluan wajar, misalnya menghubungi bisnis sebagai calon klien. Jangan menggabungkan data untuk melacak orang perorangan. Ulasan pribadi tidak diimpor.

## Cara menjawab

- Mulai dengan jawaban: "Ada 37 klinik gigi di data ini; 5 terbaik menurut rating dan jumlah ulasan:".
- Daftar pendek berisi nama, rating (ulasan), area, dan jarak kalau ada. Tabel kalau membandingkan lebih dari 4 tempat.
- Kalau pengguna butuh file, `places_export` ke .xlsx atau .csv lalu `share_file`. GeoJSON untuk dibuka di peta.
- Sebut sumbernya: "dari hasil scrape Google Maps yang diimpor" atau "dari OpenStreetMap (cakupan tiap daerah berbeda)". Data scrape bisa sudah lama; sarankan cek ulang jam buka sebelum datang.

## Membangun fitur peta di kode

Kalau pengguna membangun aplikasi atau halaman yang memakai Google Maps (peta interaktif, Places API, rute, store locator, autocomplete alamat), ikuti panduan resmi Google Maps Platform dari googlemaps/agent-skills: `load_skill(name="google-maps", file="SKILL.md")`. Untuk pertanyaan tempat di chat, jalur di atas sudah cukup dan tidak perlu API key.
