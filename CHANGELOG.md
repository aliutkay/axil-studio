# Değişiklik günlüğü

Geliştirme aşamaları: **early-alpha → alpha → beta → release**. AXIL Studio (hub) ve her aracın
kendi sürümü vardır (`axil_studio/__init__.py` → `APP_VERSION`, her aracın `version` alanı).

**Sürüm kuralı**
- Her commit AXIL Studio'nun (hub) sürümünü 0.0.1 artırır; `.githooks/pre-commit` bunu kendiliğinden yapar
  (`git config core.hooksPath .githooks` ile etkinleştir). Bir aracı değiştiren commit o aracın sürümünü de artırır.
- `0.0.x`: düzeltme ve küçük değişiklik · `0.x.0`: yeni özellik · aşama değişimi (alpha, beta …) ayrıca duyurulur.
- Her sürüm için bu dosyaya bir bölüm eklenir ve `early-alpha-<hub sürümü>` etiketi atılır.
- early-alpha boyunca eski dosya biçimleriyle geriye dönük uyumluluk yoktur; alpha'dan itibaren sağlanır.

## early-alpha 0.1.1 — tek paket

- Çekirdek modüller (model, addrmap, library, axilgen, xbargen, axilpack, hexbus, numfmt, regmap_xlsx) depo kökünden
  `axil_studio/core/` paketine taşındı; `sys.path` eklemesi kalktı. Komut satırı: `python -m axil_studio.core.axilgen`.
- `uart_validate.py` / `uart_validate_sim.py` → `scripts/`.
- Her commit hub sürümünü 0.0.1 artırır (`.githooks/pre-commit`, `scripts/bump_version.py`).

## early-alpha 0.1.0 — araçlar ayrıldı, adres haritası örnekleri

| Bileşen | Sürüm |
|---|---|
| AXIL Studio (hub) | early-alpha 0.1.0 |
| Register Map Editor (yeni) | early-alpha 0.1.0 |
| Slave Interface Wrapper | early-alpha 0.1.0 |
| Address Map Editor (yeni) | early-alpha 0.1.0 |
| Interconnect Configurator | early-alpha 0.1.0 |
| UART-to-AXI Debug Bridge | early-alpha 0.0.2 |
| IP Library | early-alpha 0.1.0 |

- Eski Tkinter arayüzleri (`app.py`, `wrapgen_gui.py`, `terminal.py`, `widgets.py`) ve "Klasik arayüz" düğmeleri kaldırıldı.
- Slave Interface Wrapper ikiye ayrıldı: **Register Map Editor** register haritasını tasarlar (base adres yok),
  **Slave Interface Wrapper** yalnızca HDL / testbench / header / doküman / Excel üretir.
- Address Map & Interconnect Configurator ikiye ayrıldı: **Address Map Editor** (adresler, pencereler, sıra, harici
  slave'ler, Excel) ve **Interconnect Configurator** (modül adı, port stili, master'lar, blok diyagramı, üretim).
- Proje modeli: IP tanımları (`ips`, adres yok, `regspec` ile) ve adres haritası örnekleri (`instances`) ayrıldı;
  aynı IP adres haritasına birden çok kez eklenebilir. Proje dosyası biçim sürümü 2; sürüm 1 dosyaları açılırken çevrilir.
- Register haritası düzenlenen her yer Register Map Editor'u, adres düzenlenen her yer Address Map Editor'u,
  IP seçilen / eklenen her yer IP Library'yi (seçici diyalog) açar.
- IP Library: projedeki tüm IP'ler ve örnekleri; kütüphane IP'sini projeye alma (adres vermeden) ya da adres
  haritasına ekleme; aynı adlı IP için kullan / değiştir / yeni adla al.
- Debug Bridge: adres haritasındaki her örnek ayrı listelenir; register haritası düzenleme Register Map Editor'a taşındı.

## early-alpha 0.0.1 — ilk sürüm

| Bileşen | Sürüm |
|---|---|
| AXIL Studio (hub) | early-alpha 0.0.1 |
| Slave Interface Wrapper | early-alpha 0.0.1 |
| Address Map & Interconnect Configurator | early-alpha 0.0.1 |
| UART-to-AXI Debug Bridge | early-alpha 0.0.1 |
| IP Library | early-alpha 0.0.1 |

- Hub: başlatıcı, her araç kendi penceresinde; ortak proje (`.axproj.json`); kurumsal renk paleti, açık/koyu tema; Türkçe arayüz.
- Slave Interface Wrapper: register / bit alanı düzenleme, veri tipleri, değer açıklamaları, IP sürümü, HDL Designer `_pk` wrapper'ı, Excel (formüllü base adres), testbench, kütüphaneye kaydetme.
- Address Map & Interconnect Configurator: liste sırasına göre otomatik / sabit adres, sürükle-bırak, master/slave ekleme-silme, harici slave, blok diyagramı (zoom), Verilog önizleme.
- UART-to-AXI Debug Bridge: projeden adres haritası, ek IP ve ham adres erişimi, veri tipine göre yazma araçları, özel değer, register haritası düzenleme, canlı izleme ve geçmiş (CSV), config hazırlama / gönderme / karşılaştırma.
- IP Library: birden fazla `.axlib.json` kütüphanesi, IP sürümleri, projeye ekleme / projeden kaydetme, Wrapper ile bağlantı.
- Sekmeler ayrı pencereye alınabilir (çoklu monitör).
