---
name: motlab-assets
description: Membuat aset sebagai kode untuk video dan desain - ikon, ilustrasi, logo vektor, karakter 2D dengan rig, objek 3D, transisi, tekstur - disimpan dalam satu kit supaya semua video tampil seragam. Output PNG transparan + SVG, atau GLB/FBX/OBJ dengan tekstur PBR, tanpa AI generator.
triggers: motlab-assets, asset kit, aset kit, kit aset, kit ikon, set ikon, icon set, karakter 2d, rig karakter, character rig, maskot, mascot, aset 3d, 3d asset, model 3d, glb, fbx, tekstur pbr, kumpulan aset, aset video
scope: contextual
context: aset, asset, kit, karakter, character, ikon, icon, ilustrasi, illustration, 3d, glb, fbx, obj, png, svg, transisi, transition, tekstur, texture, maskot, mascot, video, brand
requires: node, motlab
tools: execute_command, load_skill, share_file, read_file, file_write, file_patch, style_pick
---
# motlab-assets (aset sebagai kode)

Diadaptasi dari staryone/motlab 0.4.1 (MIT OR Apache-2.0, dipakai di bawah MIT, lihat LICENSE; commit fd2bfda). Panduan, referensi, template dan skripnya milik penulis asli dan tidak diubah.

Aset dibangun sebagai kode (SVG, canvas, three.js, Blender) dan disimpan dalam **kit**: satu folder dengan aturan gaya, sumber setiap aset, dan file hasil render. Sumber adalah asetnya; PNG/GLB adalah output yang dirender ulang, tidak diedit tangan.

1. Baca panduan: `load_skill(name="motlab-assets", file="panduan.md")`, lalu `references/kit.md`, dan `references/2d.md` atau `references/3d.md` sesuai jenis aset.
2. Pasang salinan ke chat ini: `load_skill(name="motlab-assets", install_files=true)`.
3. Butuh perintah `motlab` (lihat skill motlab untuk cara pasang) dan Python dengan numpy, scipy, Pillow. Blender hanya untuk jalur 3D utama.
4. Kerja dari referensi pengguna (brand guide, logo, foto produk, sketsa). Jangan menggambar logo atau produk nyata dari ingatan; minta file aslinya.
5. Gaya: `style_pick` kalau belum ada brand; aturan `aislop` berlaku.
