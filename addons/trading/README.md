# Add-on trading

Panel `/trading`, jembatan EA MT4, adapter MT5, model kuant, LLM analyst, simulasi, dan tool `trading_snapshot`. Mati bawaan.

## Pasang

```bash
python main.py addon install trading
```

Perintah ini memasang `requirements.txt` add-on ini (torch, dan MetaTrader5 khusus Windows), menyalakan add-on, lalu menampilkan langkah manual untuk OS kamu. Restart `python main.py web` sesudahnya.

## Langkah manual

| OS | Yang perlu dilakukan sendiri |
|---|---|
| Windows | Pasang MetaTrader 4/5 dari broker. MT4: salin `mt4/Mt4Bridge.mq4` ke `MQL4/Experts`, compile, izinkan WebRequest ke `http://127.0.0.1` |
| Linux | MT4 lewat Wine (`sudo apt install wine64`), compile EA dengan `metaeditor.exe`, buka port 80 sekali: `sudo sysctl -w net.ipv4.ip_unprivileged_port_start=80` |
| macOS | MT4 lewat Wine atau CrossOver (`brew install --cask wine-stable`), compile EA, izinkan WebRequest |

Daftar lengkapnya ada di `addon.json` (`manual`), dan panduan fitur di [docs/trading.md](../../docs/trading.md). Pengaturan ada di `env.enterprise.example`.

## Isi folder

| Folder | Isi |
|---|---|
| `quant/` | model kuant, candle LM, simulasi, auto trade |
| `mt4/` | EA bridge untuk MetaTrader 4 |
| `web/` | API (`quant_api`, `mt4_bridge`, `mt5_api`), server port 80, halaman panel (`web/static/`) |
| `tools/` | tool agent: `trading_snapshot`, kalkulator risiko |
| `skills/` | skill `analis-trading` |
| `data/` | jurnal, histori candle, bobot model (tidak ikut Git) |

Matikan lagi dengan `python main.py addon remove trading`. File dan data tetap.
