# Değişiklik günlüğü

Geliştirme aşamaları: **early-alpha → alpha → beta → release**. AXIL Studio (hub) ve her aracın
kendi sürümü vardır (`axil_studio/__init__.py` → `APP_VERSION`, her aracın `version` alanı).

**Sürüm kuralı**
- Her commit AXIL Studio'nun (hub) sürümünü 0.0.1 artırır; `.githooks/pre-commit` bunu kendiliğinden yapar
  (`git config core.hooksPath .githooks` ile etkinleştir). Bir aracı değiştiren commit o aracın sürümünü de artırır.
- `0.0.x`: düzeltme ve küçük değişiklik · `0.x.0`: yeni özellik · aşama değişimi (alpha, beta …) ayrıca duyurulur.
- Her sürüm için bu dosyaya bir bölüm eklenir ve `early-alpha-<hub sürümü>` etiketi atılır.
- early-alpha boyunca eski dosya biçimleriyle geriye dönük uyumluluk yoktur; alpha'dan itibaren sağlanır.

## early-alpha 0.1.12 — kilitler, pastel blok renkleri, diyagram dışa aktarma

| Bileşen | Sürüm |
|---|---|
| AXIL Studio (hub) | early-alpha 0.1.12 |
| Register Map Editor | early-alpha 0.5.1 |
| Slave Interface Wrapper | early-alpha 0.2.4 |
| Address Map Editor | early-alpha 0.3.1 |
| Interconnect Configurator | early-alpha 0.3.1 |
| UART-to-AXI Debug Bridge | early-alpha 0.0.4 |
| IP Library | early-alpha 0.2.1 |

- Pencere başlıkları *AXIL Studio <araç> <sürüm> | <proje>* biçiminde; araçların üst barındaki sürüm yazısı kalktı
  (sürüm başlıkta ve adın ipucunda).
- Listelerin zemini satırların açık tonuyla aynı.
- Address Map Editor: *Adresleme* sütunu yerine adresin ve boyutun yanında çalışan kilit düğmeleri; altta *Tüm
  adresleri kilitle / kilitlerini aç* ve *Tüm boyutları kilitle / kilitlerini aç*. Bellek şeridi blok diyagramının
  pastel renklerini kullanır.
- Interconnect Configurator: blok diyagramının zemini beyaz, bloklar türüne göre pastel renkli (master, interconnect,
  IP, harici slave, Sub-bus Bridge) ve renk açıklamalı. Bağlantılar *AXI4-Lite* (tüm sistemde AW / W / B / AR / R
  kanalları) ya da *HDL Designer* (REQ / RSP) olarak gösterilir. **PDF…** ve **draw.io…** gösterilen diyagramı o anki
  gösterimle kaydeder (açık renkler, seçim vurgusu olmadan).

## early-alpha 0.1.11 — Sub-bus Bridge: hiyerarşik adres haritası ve interconnect'ler

| Bileşen | Sürüm |
|---|---|
| AXIL Studio (hub) | early-alpha 0.1.11 |
| Register Map Editor | early-alpha 0.5.1 |
| Slave Interface Wrapper | early-alpha 0.2.4 |
| Address Map Editor | early-alpha 0.3.0 |
| Interconnect Configurator | early-alpha 0.3.0 |
| UART-to-AXI Debug Bridge | early-alpha 0.0.4 |
| IP Library | early-alpha 0.2.1 |

- **Sub-bus Bridge**: adres haritasında bir pencere kaplayan, isteğe bağlı kendi penceresi olan ve kalan adresleri
  değiştirmeden arkasındaki interconnect'e ileten blok (yer tutucu; HDL'i üretilmez). Altına IP, harici slave ve
  başka Sub-bus Bridge eklenir, derinlik sınırsız. Adresler globaldir; bridge'in içindekiler otomatik olarak onun
  penceresine yerleşir. Pencere otomatikse içerikle büyür ve sonraki adresler yeniden dizilir; elle seçilmişse
  sığmayan ekleme / taşıma / boyut değişikliği yapılmaz.
- Address Map Editor: tablo yerine ağaç; **+ Sub-bus Bridge ekle…**; satırlar sürükle-bırak ya da sağ tık → Taşı ile
  bus'lar arasında taşınır (başka bus'a taşınan sabit adres otomatiğe döner); bellek şeridi iç içe çizilir.
- Interconnect Configurator: interconnect hiyerarşisi listesi; her interconnect'in kendi modül adı ve master'ları
  (bridge arkasında MASTER0 bridge'in kendisi, ek master eklenebilir); **Tüm sistem** blok diyagramı; **Üret…** her
  interconnect için ayrı wrapper yazar. Kademeli iki axilxbar ile global adreslemenin çalıştığı iverilog
  simülasyonuyla doğrulandı (boş adresler DECERR döner).
- Debug Bridge: adres ağacında IP'ler Sub-bus Bridge'lerinin altında gösterilir.
- Proje dosyası: Sub-bus Bridge kaydı `"kind": "bridge"`, `self_window`, `xbar`, `children` alanlarıyla; eski projeler
  değişmeden açılır.

## early-alpha 0.1.10 — satır ikonları, beyaz paneller, başlık düzeltmeleri

| Bileşen | Sürüm |
|---|---|
| AXIL Studio (hub) | early-alpha 0.1.10 |
| Register Map Editor | early-alpha 0.5.1 |
| Slave Interface Wrapper | early-alpha 0.2.4 |
| Address Map Editor | early-alpha 0.2.3 |
| Interconnect Configurator | early-alpha 0.2.5 |
| UART-to-AXI Debug Bridge | early-alpha 0.0.3 |
| IP Library | early-alpha 0.2.1 |

- Düzeltme: Register Map Editor'da **Farklı kaydet…** (düğme ve menü) `x not in tuple` hatasıyla çöküyordu (Qt'nin
  `clicked(bool)` değeri seçim parametresine gidiyordu).
- Üst bar: sürüm düz, ince yazı (kalın etiket değil); **Kaydet** ve **Farklı kaydet…** ikonlu; bardaki ana düğme yine
  mavi. » menüsü yeniden yazıldı: pencere daraldıkça sığmayan komutlar sondan başlayarak menüye geçer, genişleyince
  geri gelir; menüdeki komutlar düğmenin kendisini çalıştırır.
- Kartlar, sekmeli panellerin sayfaları ve yerleştirilmemiş bit alanları kartı yeniden beyaz (turuncu kesik çerçeve
  kalktı). Tüm listeler (ağaçlar, tablolar, dosya listeleri) aynı iki satır rengini kullanır; listenin boş kısmı beyaz.
- Listelerde alttaki tek *Düzenle* / *Sil* düğmeleri yerine her satırın sonunda silik kalem ve çöp kutusu ikonu
  (üzerine gelince belirginleşir): Register Map Editor'da registerlar, bit alanları, değer açıklamaları,
  yerleştirilmemiş alanlar (artık düzenlenebilir); Address Map Editor'da slave'ler; Interconnect Configurator'da
  master'lar; değer açıklaması penceresi.
- Register Map Editor: açılmış değer açıklaması tablosunun altındaki boşluk kaldırıldı.

## early-alpha 0.1.9 — tek üst bar, evrensel kayıt, bit alanı panelleri

| Bileşen | Sürüm |
|---|---|
| AXIL Studio (hub) | early-alpha 0.1.9 |
| Register Map Editor | early-alpha 0.5.0 |
| Slave Interface Wrapper | early-alpha 0.2.4 |
| Address Map Editor | early-alpha 0.2.2 |
| Interconnect Configurator | early-alpha 0.2.4 |
| UART-to-AXI Debug Bridge | early-alpha 0.0.3 |
| IP Library | early-alpha 0.2.1 |

- Tüm araçlar: başlık + açıklama satırı yerine tek üst bar (ikon, ad, sürüm, komutlar; sığmayanlar » menüsünde; sağda
  durum ve ana düğme). Alt çubuktaki doğrulamanın beyaz zemini kaldırıldı. Listelerde iki satır rengi de beyaz değil;
  listenin sonundaki boşluk satırı ayrı bir gri satır gibi görünmez.
- Register Map Editor: **Kaydet** / **Farklı kaydet…** (proje / kütüphane / `.regspec.json`); yer hatırlanır, kütüphaneden
  açılan IP kütüphaneye kaydedilir. *Projeye / Kütüphaneye / Dosyaya kaydet* düğmeleri kalktı, dosya açma *İçe aktar…*.
- Register Map Editor: yerleştirilmemiş bit alanları ayrı, kesik çizgili kartta; bit alanı listesinden sürükle-bırak
  (bit haritası, başka register, yerleştirilmemişler); düğmeler sayıyı ve hedefi yazar; toplu yeni registera
  yerleştirmede taşanlar için yeni registerlar, tek alanlı register alan adını alır; Düzenle solda, Sil sağda.
- Interconnect: *İçe aktar / Dışa aktar (.xbar.json)*; Wrapper: *İçe aktar…*; Debug Bridge config: *Kaydet* (son
  dosyaya) ve *Farklı kaydet…*.

## early-alpha 0.1.8 — yerleşik örnek kaldırıldı

- `axilgen.example_spec()` ve komut satırındaki `--example` seçeneği kaldırıldı; komut satırı bir `.regspec.json`
  dosyası ister. (Hub'daki *Örnek proje* `examples/` altındaki hazır dosyayı açar, örnek oluşturmaz.)

## early-alpha 0.1.7 — doğrulama alt çubukta, kaydetme pencereleri, arama

| Bileşen | Sürüm |
|---|---|
| AXIL Studio (hub) | early-alpha 0.1.7 |
| Register Map Editor | early-alpha 0.4.0 |
| Slave Interface Wrapper | early-alpha 0.2.3 |
| Address Map Editor | early-alpha 0.2.1 |
| Interconnect Configurator | early-alpha 0.2.3 |
| IP Library | early-alpha 0.2.0 |

- Tüm araçlarda doğrulama ayrı panel yerine pencerenin alt çubuğunda (üzerine gelince tüm hatalar, tıklayınca liste).
- Register Map Editor: *Örnek* düğmesi ve örnek yükleme kaldırıldı. Ayrı **IP** paneli (ad, sürüm, açıklama; açıklama
  ayrı pencerede düzenlenir; *IP açıklaması* sekmesi kalktı). Register arama kutusu (bit alanlarında da arar). Register,
  bit alanı ve yerleştirilmemiş listelerinin sonunda bırakma boşluğu; bit alanı listesine de bırakılabilir. Değer
  açıklamaları yalnızca kendi sütunundan açılır.
- **Kütüphaneye kaydet** her zaman kütüphane seçme penceresini açar (kütüphaneler, IP'ler, sürümler; sürüm düzenleme,
  yeni kütüphane). **Projeye kaydet** projedeki tüm IP'leri gösteren pencereyle. IP Library'nin kütüphaneye kaydetmesi
  de aynı pencereyi kullanır.
- Address Map Editor: slave ekleme düğmeleri tablonun altındaki Düzenle / Sil / sıralama satırında.
- Hub: IP Library araç kartı yerine Proje kartında **IP'leri yönet** düğmesi (Araçlar menüsünde de, Ctrl+6).

## early-alpha 0.1.6 — adres haritası ve register haritası düzenlemeleri

| Bileşen | Sürüm |
|---|---|
| AXIL Studio (hub) | early-alpha 0.1.6 |
| Register Map Editor | early-alpha 0.3.0 |
| Address Map Editor | early-alpha 0.2.0 |

- Address Map Editor: **Varsayılan pencere** ayarı (başlangıç adresi gibi); elle seçilen boyut o slave'de kilitli
  kalır, *varsayılan* ile açılır. "Bir kez daha ekle" kaldırıldı. *+ IP Kütüphanesinden slave ekle* ve *+ Harici slave
  ekle* yan yana. Örnek adları `<IP>_0`, `<IP>_1` … (harici: `EXT_0` …); IP yeniden adlandırılınca izlerler.
- Register Map Editor: sol panelde yalnızca registerlar; yerleştirilmemiş bit alanları ve *Port içe aktar* Bit
  alanları sekmesinin altında (üst çubuktan kaldırıldı). Yerleşmiş alanlar bit haritasında sürüklenerek taşınır,
  yerleştirilmemiş listesine sürüklenince geri alınır.
- Hata: bir araç penceresi kapandıktan sonra tema değiştirince `hub.py` 122. satırda "already deleted" hatası.
- Koyu tema lacivert yerine siyah / grafit.
- Örnek proje: IP `GPIO`, adres haritasında `GPIO_0` (config örnekleri de).

## early-alpha 0.1.5 — hata düzeltmeleri ve temizlik

| Bileşen | Sürüm |
|---|---|
| AXIL Studio (hub) | early-alpha 0.1.5 |
| Register Map Editor | early-alpha 0.2.1 |
| Slave Interface Wrapper | early-alpha 0.2.2 |
| Address Map Editor | early-alpha 0.1.1 |
| Interconnect Configurator | early-alpha 0.2.2 |
| IP Library | early-alpha 0.1.1 |

- Komut satırı `--xbar`: `--ns` kaldırıldı (slave'ler adres haritasından gelir); `--nm` artık gerçek master portları
  ekler. Önceden yedek portlar `0x0` adresinde çakışan ve yanıt vermeyen slave'ler üretiyordu.
- Proje başka klasöre kaydedilince kütüphane yolları yeni konuma göre yeniden hesaplanır (önceden kütüphane
  bulunamıyordu).
- `i_reset` / `o_irq` port adını üretecek register / alan adları doğrulamada reddedilir (HDL derlenmiyordu).
- Register Map Editor: başka araçtan gelen haritanın kaynağı yanlış yazılıyordu ("Slave", "Yeni"); kaynak artık
  kütüphane adı olarak ayrı taşınır.
- Port içe aktar: Verilog `function` / `task` bildirimlerinin girişleri port sayılmaz.
- `hdl/addrdecode.v`: tek satırlık, belgelenmiş uyumluluk yaması; tek slave'li interconnect iverilog'da derlenir.
- Register spesifikasyonundan `base` kaldırıldı; adres gerektiren çıktılar adresi parametre olarak alır, komut
  satırına `--base` eklendi. Eski dosyalardaki `base` yok sayılır.
- Temizlik: xbar modelinden EN (kapalı/yedek port) kalıntıları, blok diyagramındaki "kapalı port (EN=0)"
  açıklaması, kullanılmayan fonksiyonlar (`show_tool`, `remove_instance`, `library_of`, `get_address_ip`,
  `gpio_example_ip`, `uart_phy_example_ip`, `_sig`, `core_spec`, `_fhex`, `resize`, `sort_by_address`,
  `window_mask`).

## early-alpha 0.1.4 — port listesinden bit alanları

| Bileşen | Sürüm |
|---|---|
| AXIL Studio (hub) | early-alpha 0.1.4 |
| Register Map Editor | early-alpha 0.2.0 |

- Register Map Editor: **Port içe aktar** — IP çekirdeğinin Verilog / SystemVerilog ya da VHDL port listesi
  yapıştırılır, seçilen portlar yerleştirilmemiş bit alanı olur (girişler RW, çıkışlar RO; 32 bitten geniş sinyaller
  32 bitlik parçalara bölünür). Parça parça, birden çok blok için tekrarlanabilir.
- Yerleştirilmemiş bit alanları paneli: bit haritasına (önizlemeli) ya da registerlara sürükle-bırak, yeni registera
  koyma, silme, yerleşmiş alanı geri alma. Register spesifikasyonunda `unplaced` olarak saklanır, HDL'e girmez.
- Yeni çekirdek modül `axil_studio/core/portparse.py`.

## early-alpha 0.1.3 — VHDL yok, önizlemede dosya seçimi

| Bileşen | Sürüm |
|---|---|
| AXIL Studio (hub) | early-alpha 0.1.3 |
| Slave Interface Wrapper | early-alpha 0.2.1 |
| Interconnect Configurator | early-alpha 0.2.1 |

- VHDL component dosyaları (`<ip>_comp.vhd`, `<ip>_pk_comp.vhd`, hazır IP wrapper'ının `_comp.vhd`'si) artık üretilmiyor.
- Verilog önizlemede dosya seçilir. Slave: IP, HDL Designer wrapper'ı (`_pk.v`), örnekleme şablonları, testbench.
  Interconnect: `<ad>.v`, `<ad>_pk.v`, ZipCPU `axilxbar.v` (salt okunur).

## early-alpha 0.1.2 — ZipCPU yapısına sadık HDL

| Bileşen | Sürüm |
|---|---|
| AXIL Studio (hub) | early-alpha 0.1.2 |
| Slave Interface Wrapper | early-alpha 0.2.0 |
| Interconnect Configurator | early-alpha 0.2.0 |

- Slave: üretilen IP artık ZipCPU wb2axip `easyaxil.v`'nin kendisi (`hdl/easyaxil.v` şablonundan); yalnızca modül
  adı, adres genişliği, IP portları ve *AXI-lite register logic* bölümü değişir. `OPT_SKIDBUFFER` / `OPT_LOWPOWER`
  geri geldi. PULSE, W1C, irq, sync ve strobe'lar register logic bölümünde easyaxil üslubuyla. `localparam
  C_AXI_DATA_WIDTH` → `parameter` (Verilog-2005), okuma `case`'ine `default`, formal r0..r3 kontrolleri çıkarıldı.
- Interconnect: `axilxbar.v` yalnızca parametrelerle yapılandırılır. `<ad>.v` her portu ayrı AXI sinyalleriyle
  verir; `<ad>_pk.v` onu saran HDL Designer (REQ/RSP) wrapper'ı. `MASTER/SLAVE<j>_EN` generic'leri, kendi DECERR
  modülü ve düz vektör (`flat`) stili kaldırıldı.
- `hdl/` altına wb2axip'in `easyaxil.v`, `addrdecode.v`, `skidbuffer.v` dosyaları eklendi (değiştirilmeden).

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
