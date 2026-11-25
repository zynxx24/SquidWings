# brain/harness/

Lapisan yang membuat model murah terasa pintar dan menjaga prompt tetap hemat saat skill dan tool terus bertambah.

| File | Isi |
|---|---|
| `autopilot.py` | Mode Auto: tier dari tingkat kesulitan, filter (konteks, tool, vision, sehat), lalu biaya per jawaban sukses terendah. Eskalasi satu tier setelah giliran gagal, turun lagi setelah berhasil |
| `model_errors.py` | Menamai kegagalan model (auth, billing, rate_limit, not_found, context, timeout, connection, server, empty) dan menjelaskannya ke pengguna |
| `relevance.py` | `TriggerWeights` (trigger yang jarang bobotnya besar, frasa panjang mengalahkan kata yang tumpang tindih) dan `Index` (BM25 dengan bobot per field) |
| `briefing.py` | Skill, partner desain bergilir, dan catatan vault yang masuk prompt, dalam anggaran karakter per tier |
| `tool_select.py` | Tool yang ditawarkan: yang disebut, yang dibutuhkan skill aktif, inti per jenis tugas, lalu toolbox yang cocok, dibatasi per tier |
| `tiers.py` | Tier model (lite, standard, frontier) dan batasnya: jumlah tool, putaran, anggaran skill dan briefing |
| `verifier.py`, `guards.py` | Memeriksa hasil, memutus loop, memperbaiki argumen tool |
| `advisor.py` | Model kedua untuk konsultasi (hanya Auto) |
| `playbooks.py` | Pelajaran dari chat lama yang relevan |
| `runtime.py` | Statistik dan batas satu giliran |

## Supaya tetap pintar saat skill dan tool ditumpuk

- **Indeks skill diurutkan menurut relevansi.** Yang aktif di chat dulu, lalu yang dipanggil permintaan, lalu hasil BM25. Semua skill tetap terdaftar (tanpa batas buatan); `SKILL_INDEX_MAX` > 0 memendekkan daftar kalau suatu saat perlu.
- **Trigger berbobot IDF.** Kata yang dimiliki banyak skill ("desain") bobotnya kecil, kata khusus ("gsap") besar. Skill khusus yang dibangunkan permintaan ini bobotnya dua kali lipat.
- **Tool tersembunyi.** 100+ tool toolbox tidak dikirim ke model sampai diminta; `find_tools` mencarinya dengan BM25.
- **Anggaran per tier.** Model lite mendapat tool dan panduan lebih sedikit dan lebih pendek.

## Menambah logika

Kode baru di sini punya test di `tests/` (lihat `test_model_modes.py`, `test_skill_adapters.py`). Jangan menambah fallback ke model lain untuk model yang dipilih pengguna.
