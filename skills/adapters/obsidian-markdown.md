---
skill: obsidian-markdown
upstream: inline
---
[Penyesuaian SquidWings untuk Obsidian]
- Vault memori harness ini sudah berformat Obsidian (wikilink, frontmatter, folder per tipe). Untuk catatan di vault itu pakai `vault_write` (frontmatter dibuat otomatis, jangan tulis YAML sendiri), `vault_read`, `vault_search`, dan `vault_neighbours` untuk tautan.
- Panduan di bawah dipakai saat menulis atau menyunting file `.md` Obsidian milik pengguna di folder proyek (`file_write`, `file_patch`), atau saat isi catatan butuh callout, embed, dan properti.
- Jangan memindahkan atau mengganti nama catatan vault tanpa diminta: wikilink yang menunjuk ke sana akan putus.
