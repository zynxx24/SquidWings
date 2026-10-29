# Aturan konteks proyek SquidWings

## Urutan kerja WAJIB

1. **Cek kesegaran graph.** `graphify-out/graph.json` harus lebih baru dari file `.py` terakhir yang diubah. Basi -> `graphify update .`
2. **Query graph** untuk topik tugas:

   ```bash
   graphify explain "NamaKelas"
   graphify path "A" "B"
   graphify query "topik" --seed NamaKelas --context calls
   ```

3. **Baca catatan vault** di `vault/20-concepts/` atau `vault/30-decisions/` yang relevan.
4. **Tulis Context Manifest** SEBELUM edit (format di bawah).
5. **Read hanya file/fungsi** yang disebut graph atau catatan vault.

## Aturan kepercayaan edge

Graph saat ini: **92.1% EXTRACTED / 7.9% INFERRED / 0% AMBIGUOUS**.

- Edge `INFERRED` -> verifikasi dengan membaca kode sebelum mengubah.
- Edge `AMBIGUOUS` -> jangan bersandar sama sekali; baca kode dulu.
- Keputusan hapus/ubah signature hanya boleh bersandar pada `EXTRACTED`.

## Pola dinamis yang TIDAK terlihat graph

Lihat `vault/20-concepts/dynamic-tool-registry.md`. Ringkasnya:

| Pola | Lokasi | Kenapa tak terlihat |
|---|---|---|
| Registry tool | `tool/register_tools.py` | didaftarkan runtime, bukan impor |
| Event bus | `utils/event_bus.py` | handler didaftarkan runtime |
| DI lewat config | `config/models_config.yaml` | relasi di data, bukan kode |
| Endpoint -> JS | `web/server.py` <-> `web/static/js/*.js` | beda bahasa; spec melarang edge `calls` lintas bahasa |

## Context Manifest (wajib sebelum edit)

```markdown
## Context Manifest
- Tujuan:
- File yang akan diubah:
- Caller terdampak (dari graph):
- Invarian terkait (dari vault/):
- Test yang harus lulus:
- Yang TIDAK saya ketahui:
- Sumber tiap klaim: [graph query #N], [vault/...]
```

Kolom **"Yang TIDAK saya ketahui"** wajib terisi, atau tulis "tidak ada" + alasan.

## Penutup tugas

```bash
.venv/bin/ruff check .
.venv/bin/python -m mypy --no-incremental --show-error-codes
.venv/bin/python -m compileall -q .
.venv/bin/python -c "import main; print('IMPORT OK')"
graphify update .
```

Perbarui catatan vault yang berubah.

## Perintah

| Keperluan | Perintah |
|---|---|
| Lint | `.venv/bin/ruff check .` |
| Type | `.venv/bin/python -m mypy --no-incremental` |
| Test | `.venv/bin/python -m pytest tests/ -q` |
| Compile | `.venv/bin/python -m compileall -q .` |
| Graph build | `graphify update .` |
| Graph query | `graphify explain "X"` / `graphify path "A" "B"` |

## Jebakan yang sudah diketahui

- `main.py` punya **dua** node bernama `main`: modul (L1, id `main`) dan fungsi (L2196, id `main_main`). Pakai id penuh saat `path`.
- Tiga modul bernama "graphify" **bukan** Graphify:
  - `model/connection/graphify_client.py` - 9Router LLM client (dibuang di `main.py:1807`)
  - `tool/execute/execute_local_memory/graphify_store.py` - HTTP ke endpoint yang tidak ada
  - `tool/execute/execute_local_memory/vault/graph.py` - graph vault Obsidian, kosakata edge `explicit`/`inferred`
- `web/static/js/*.js` tidak terhubung ke Python di graph - beda bahasa.
- `pytest` bisa naik ke `/home/wira/Documents/agent/pyproject.toml` bila section `[tool.pytest.ini_options]` hilang dari `pyproject.toml` ini. Rootdir **wajib** SquidWings.
