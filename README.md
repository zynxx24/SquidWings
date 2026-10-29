# SquidWings

Asisten AI berbasis Python dengan terminal interaktif, Web GUI, routing multi-model,
dan tool lokal (baca/tulis file, eksekusi command, planning) yang dibungkus
**Security Layer** berlapis.

---

## Daftar Isi

- [Fitur](#fitur)
- [Kebutuhan](#kebutuhan)
- [Instalasi](#instalasi)
- [Konfigurasi](#konfigurasi)
- [Cara Menjalankan](#cara-menjalankan)
- [Perintah CLI](#perintah-cli)
- [Security Layer](#security-layer)
- [Menjalankan Test](#menjalankan-test)
- [Struktur Proyek](#struktur-proyek)
- [Troubleshooting](#troubleshooting)
- [Lisensi](#lisensi)

---

## Fitur

| Fitur | Keterangan |
|---|---|
| **Terminal interaktif** | Mode `chat` dengan riwayat, markdown rendering, dan konteks workspace |
| **Web GUI** | Antarmuka browser (FastAPI + WebSocket), opsional diekspos via Cloudflare Tunnel |
| **Multi-model routing** | Registry model, capability matcher, fallback, load balancer, rate limiter |
| **Tool lokal** | Baca file, edit file (dengan backup + diff), eksekusi command (sanitized), discard perubahan |
| **Planning engine** | Dekomposisi task, dependency resolver, flow engine, penyimpanan plan |
| **Memori berlapis** | Conversation store, persistent memory, summary compressor, workspace memory |
| **Token management** | Penghitung token, kompresi konteks, kalkulasi max/min token |
| **Security Layer** | Policy engine, path jail, secrets vault, audit hash-chain, prompt shield, redactor |

---

## Kebutuhan

- **Python 3.11+** (diuji pada 3.12.3)
- Endpoint API yang **kompatibel dengan OpenAI Chat Completions**
  (lokal seperti llama.cpp/Ollama, atau provider cloud)
- Linux/macOS, atau Windows via PowerShell

> Catatan: di banyak sistem hanya tersedia `python3` (bukan `python`).
> Gunakan `python3` pada semua perintah di bawah jika demikian.

---

## Instalasi

### Linux / macOS


---

## Lisensi

SquidWings adalah perangkat lunak berbayar (proprietary). Penggunaan memerlukan
lisensi yang dibeli. Lihat file [LICENSE](LICENSE) untuk syarat lengkap.
