# Değişiklik günlüğü

Geliştirme aşamaları: **early-alpha → alpha → beta → release**. AXIL Studio (hub) ve her aracın
kendi sürümü vardır (`axil_studio/__init__.py` → `APP_VERSION`, her aracın `version` alanı).

**Sürüm kuralı**
- Bir aracı değiştiren her commit o aracın sürümünü artırır; hub'ın sürümü de her sürümde artar.
- `0.0.x`: düzeltme ve küçük değişiklik · `0.x.0`: yeni özellik · aşama değişimi (alpha, beta …) ayrıca duyurulur.
- Her sürüm için bu dosyaya bir bölüm eklenir ve `early-alpha-<hub sürümü>` etiketi atılır.
- early-alpha boyunca eski dosya biçimleriyle geriye dönük uyumluluk yoktur; alpha'dan itibaren sağlanır.

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
