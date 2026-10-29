# Panduan Menambahkan Model Baru ke Agent Router

Dokumen ini menjelaskan bagian kode dan konfigurasi mana saja yang harus diubah saat ingin menambahkan model AI baru ke dalam sistem agent ini.

Sistem agent ini dirancang secara modular dan **config-driven**, sehingga sebagian besar pengaturan dilakukan melalui file YAML konfigurasi, didukung oleh sinkronisasi kode Python untuk prioritas dan fallback.

---

## Ringkasan Alur & File yang Terlibat

| Prioritas | File | Peran / Fungsi |
| :--- | :--- | :--- |
| **Wajib (Utama)** | `config/models_config.yaml` | Mendaftarkan spesifikasi model, kapabilitas, aturan routing, dan rantai fallback. |
| **Penting (Python)** | `model/settings/model_priority.py` | Menentukan prioritas statis model per task & tingkat kesulitan. |
| **Penting (Python)** | `brain/max_token/max_token_config.py` | Menyediakan limit cadangan token output (`MODEL_MAX_OUTPUT`). |
| **Kondisional** | `config/constants.py` | Digunakan jika model memiliki provider baru atau format thinking baru. |
| **Kondisional** | `model/parser/` | Digunakan jika model membutuhkan parser output thinking/reasoning khusus. |
| **Opsional** | `config/settings.py` | Mengubah default model global sistem. |

---

## 1. File Utama & Wajib: `config/models_config.yaml`

File ini adalah **sumber kebenaran utama (*single source of truth*)**. Komponen seperti `ModelRegistry`, `RoutingType`, dan `FallbackStrategy` membaca langsung dari file ini.

Ada 3 bagian di file ini yang perlu diperbarui:

### A. Daftarkan Spesifikasi Model (`models:`)
Tambahkan ID model baru dan kapabilitasnya di bawah blok `models:`:

```yaml
models:
  # Contoh: Model Baru Anda
  ag/model-baru-anda:
    display_name: "Model Baru Anda"
    provider: ag                               # Prefix provider (ag, openai, th, dll)
    thinking_format: openai                   # Format thinking: openai, deepseek, gemini-level, claude-adaptive, claude-budget
    thinking_can_disable: true
    thinking_effort_supported: false
    context_window: 128000                    # Batas token konteks input
    max_output: 16384                         # Batas maksimal token output
    capabilities:
      vision: true                            # Apakah mendukung input gambar?
      pdf: false                              # Apakah mendukung input dokumen PDF?
      audio_input: false                      # Apakah mendukung input suara?
      video_input: false                      # Apakah mendukung input video?
      image_output: false                     # Apakah menghasilkan output gambar?
      audio_output: false                     # Apakah menghasilkan output suara?
      search: false                           # Apakah terintegrasi web search?
      tools: true                             # Apakah mendukung tool/function calling?
      reasoning: false                        # Apakah model reasoning / thinking?
    tags:
      - fast
      - code
      - general
```

### B. Masukkan ke Aturan Routing (`routing:`)
Tentukan pada task apa (`code`, `math`, `activity`, `school_task`, `generate_img`, `reason`, `multimodal`) serta tingkat kesulitan apa (`easy`, `medium`, `hard`) model ini akan dipakai:

```yaml
routing:
  code:
    easy: "ag/gemini-3.8-flash-high"
    medium: "ag/model-baru-anda"         # <-- Pasang di tingkat kesulitan yang sesuai
    hard: "ag/claude-opus-4-6-thinking"
    fallback_chain:
      - "ag/model-baru-anda"             # <-- Masukkan juga ke urutan cadangan jika model lain gagal
      - "ag/gemini-3.8-flash-high"
      - "ag/claude-opus-4-6-thinking"
```

### C. Ganti Default Model (Opsional)
Jika Anda ingin model baru ini menjadi model default global jika tidak ada kecocokan routing:
```yaml
default_model: "ag/model-baru-anda"
```

---

## 2. File Sinkronisasi Python: `model/settings/model_priority.py`

File ini menyimpan urutan prioritas statis yang digunakan untuk pemilihan model berdasarkan `TaskType` dan level kesulitan.

Tambahkan ID model Anda ke dalam list pada dictionary `MODEL_PRIORITY`:

```python
MODEL_PRIORITY: dict[str, dict[str, list[str]]] = {
    TaskType.CODE.value: {
        "easy": ["ag/gemini-3.8-flash-high"],
        "medium": ["ag/model-baru-anda", "ag/claude-sonnet-4-6"],  # <-- Tambahkan di sini (urutan paling kiri = prioritas tertinggi)
        "hard": ["ag/claude-opus-4-6-thinking", "ag/claude-sonnet-4-6"],
    },
    TaskType.MATH.value: {
        # Tambahkan ke task type lain yang relevan...
    },
}
```

---

## 3. File Cadangan Token: `brain/max_token/max_token_config.py`

Meskipun limit token dimuat dari file YAML melalui properti `max_output`, file ini memiliki dictionary statis `MODEL_MAX_OUTPUT` sebagai cadangan (*fallback safety*).

Tambahkan entri model baru Anda di dictionary:

```python
MODEL_MAX_OUTPUT: dict[str, int] = {
    "ag/gemini-3.8-flash-high": 65536,
    "ag/claude-sonnet-4-6": 128000,
    "ag/claude-opus-4-6-thinking": 64000,
    "ag/model-baru-anda": 16384,          # <-- Tambahkan batas token output
}
```

---

## 4. Bagian Kondisional (Hanya Jika Ada Kasus Khusus)

Sentuh bagian ini hanya jika model baru Anda memiliki spesifikasi yang belum didukung oleh kode saat ini:

### A. Provider Baru (Bukan `ag` atau `TH`)
Buka `config/constants.py`, lalu tambahkan provider baru pada enum `ModelProvider`:
```python
@unique
class ModelProvider(str, Enum):
    AG = "ag"
    TH = "TH"
    OPENAI = "openai"     # <-- Tambahan baru jika ada
    OLLAMA = "ollama"     # <-- Tambahan baru jika ada
```

### B. Format Thinking/Reasoning Baru
Jika model baru Anda mengeluarkan tag reasoning khusus yang belum dikenali (`gemini-level`, `claude-adaptive`, `claude-budget`, `openai`, `deepseek`):
1. Tambahkan di `config/constants.py` pada enum `ThinkingFormat`.
2. Buat parser baru di dalam folder `model/parser/` mewarisi `BaseParser`.
3. Daftarkan parser tersebut di blok `thinking_handlers` pada `config/models_config.yaml`.

### C. Model Default di Setting Pydantic
Buka `config/settings.py` jika ingin default fallback pada level environment pydantic diubah:
```python
default_model: str = "ag/model-baru-anda"
```

---

## Langkah-langkah Ringkas Saat Eksekusi:

1. **Edit `config/models_config.yaml`**:
   - Daftarkan model di bagian `models:`.
   - Masukkan ke dalam skema `routing:` dan `fallback_chain:`.
2. **Edit `model/settings/model_priority.py`**:
   - Tambahkan ID model ke dalam list task di `MODEL_PRIORITY`.
3. **Edit `brain/max_token/max_token_config.py`**:
   - Daftarkan max tokens di `MODEL_MAX_OUTPUT`.
4. **Validasi & Uji Coba**:
   - Jalankan `python main.py models` untuk memastikan model terdeteksi di registry.
   - Jalankan `python main.py ask "Halo, tes model baru"` untuk mencoba request langsung.
