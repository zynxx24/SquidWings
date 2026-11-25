# skills/

Panduan kerja untuk AI. Skill yang cocok dengan permintaan dimasukkan ke prompt secara otomatis; sisanya bisa dibuka dengan `load_skill`.

| File / folder | Isi |
|---|---|
| `builtin/` | Skill buatan sendiri: `fix-bug`, `build-web`, `design-ui`, `aislop`, `redhat`, `laravel`, `teman-curhat`, ... |
| `sources.json` | Skill dari GitHub: repo, skill yang aktif, trigger, tool, scope |
| `.sources/` | Clone repo GitHub (otomatis, tidak ikut Git, **tidak pernah diubah**) |
| `adapters/` | Penyesuaian kita untuk skill GitHub, satu file per skill |
| `loader.py` | Membaca skill dan `load_all` (ter-refresh sendiri saat file skill berubah) |
| `selection.py` | Skor trigger berbobot, membangunkan skill, `select`, indeks skill yang dibatasi |
| `design.py` | Rotasi partner desain |
| `runtime.py` | Catatan peta tool untuk skill GitHub, instal file skill ke folder chat |
| `sources.py` | Clone, pull harian, aktif atau tersimpan, link ke Claude Code, pencarian BM25 |
| `adapters.py` | Menggabungkan adapter dengan skill aslinya |
| `__init__.py` | API publik (`skills.select`, `skills.load_all`, ...) |

## Scope

- `core`: selalu ada di indeks skill.
- `contextual`: tidur sampai trigger-nya muncul, lalu aktif untuk chat itu saja.
- `always`: aturan pendek yang selalu masuk prompt.
- `stored`: skill GitHub yang tidak aktif, dibuka dengan nama persisnya.

## Adapter

Skill asli dari GitHub tetap utuh. Penyesuaian kita ditulis di `adapters/<nama>.md`:

```markdown
---
skill: google-maps
upstream: guide      # inline: adapter lalu teks asli; guide: adapter saja, teks asli lewat load_skill(file="SKILL.md")
fallback: true       # adapter tetap jalan walau repo belum ter-clone
---
Tool mana yang dipakai di harness ini, kapan panduan aslinya berlaku, ...
```

Sekarang ada adapter untuk Ponytail, Obsidian (markdown, canvas, bases), graphify, dan Google Maps.

## Rotasi desain

Skill dengan `pool: design` (design-ui, ui-ux-pro-max, impeccable, accessibility) bergiliran: tiap chat desain mendapat satu, yang paling lama tidak dipakai di semua chat, dan chat itu memakainya terus. Menyebut namanya ("pakai impeccable") memilih yang itu.

## Menambah skill

- Buatan sendiri: `builtin/<nama>.md`, atau `builtin/<nama>/SKILL.md` kalau butuh file pendamping.
- Dari GitHub: tambah repo ke `sources.json`, lalu `python main.py skills sync`. Kalau perlu penyesuaian, tulis `adapters/<nama>.md`.
- Cari skill tersimpan: `python main.py skills search "<kebutuhan>"`.
