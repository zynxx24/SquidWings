---
skill: ponytail
upstream: inline
---
[Penyesuaian SquidWings untuk Ponytail]
- Level Ponytail diatur per chat: perintah `/ponytail off|lite|full|ultra` di terminal, atau tombol Ponytail di web. Level yang berlaku tertulis di system prompt ("Ponytail level for this chat"); ikuti itu, bukan level bawaan di panduan.
- "stop ponytail" atau "normal mode" dari pengguna: berhenti memakai aturan ini untuk sisa balasan, lalu sarankan `/ponytail off` supaya tersimpan.
- Perintah `/ponytail-review`, `/ponytail-audit`, `/ponytail-debt`, `/ponytail-gain`, dan `/ponytail-help` di sini adalah skill tersimpan: buka dengan `load_skill(name="ponytail-review")` dan seterusnya.
- Test kecil yang diminta panduan dijalankan dengan `execute_command`. Laporkan hasilnya apa adanya.
