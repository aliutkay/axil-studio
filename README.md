# AXIL Studio

Üç AXI4-Lite aracını tek çatı altında toplayan hub (PySide6/Qt, Windows ve macOS'ta aynı görünüm, açık/koyu tema):

| Araç | Ne yapar |
|---|---|
| **Slave Interface Wrapper** | Register/alan tanımından AXI4-Lite slave HDL, testbench, header, dokümantasyon üretir; IP'yi projeye ekler |
| **Address Map & Interconnect Configurator** | Projedeki IP'lerden slave pencerelerini hesaplar, doğrular, axilxbar wrapper'ı üretir |
| **UART-to-AXI Debug Bridge** | Hexbus üzerinden karttaki registerları okur/yazar, bit alanlarını çözer |

Ana pencere bir başlatıcıdır; her araç kendi penceresinde açılır (Ctrl+1/2/3). Araçlar ayrı çalışır ama aynı projeyi (`.axproj.json`) paylaşır: Wrapper'ın eklediği IP anında Interconnect haritasında ve Debug Bridge ağacında görünür.

```
pip install -r requirements.txt
python studio.py                                   # hub
python studio.py --tool debug                      # tek araç: wrapper | interconnect | debug
python studio.py examples/gpio_demo.axproj.json    # projeyle aç
```

Kod `axil_studio/` altında; çekirdek modüller (`axilgen`, `xbargen`, `hexbus`, `model` …) değişmedi.
Slave Interface Wrapper tamamen taşındı: register/alan ekleme-düzenleme (bit haritasında boş bite çift tık
yeni alan açar), değer anlamları, IP açıklaması, Verilog önizleme, Excel, iverilog testbench, hazır IP paketleme.
Her alanın bir **veri tipi** vardır (`numfmt.py`): hex, unsigned, signed (2's complement), binary, octal, bool,
ASCII, unsigned/signed fixed point (Q, kesir biti seçilir) ya da enum (değer anlamı). Reset ve değerler bu tipte
gösterilir/girilir; tip `.regspec.json` ve `.axproj.json` dosyalarına `fmt` olarak yazılır, UART-to-AXI Debug Bridge
alanı varsayılan olarak bu tipte gösterir ("Gösterim" ile değiştirilebilir). Değer açıklamaları bit alanı satırına tıklayınca
alt satırlar olarak açılır.
Kısayollar: Ctrl+R yeni register, Ctrl+F yeni alan, Enter düzenle, Delete sil.
Henüz taşınmamış özellikler (xbar port düzenleme, canlı izleme, config, terminal) için ilgili aracın
**Klasik arayüz** düğmesi eski Tkinter aracını açar.

---

# AXI4-Lite UART Console (klasik arayüz)

ZipCPU hexbus (UART → AXI4-Lite) köprüsü üzerinden AXI4-Lite IP'leri okuyup yazmak için Tkinter arayüzü.

## Kurulum
```
pip install pyserial openpyxl
python app.py
```
Python 3.8+ gerekir (Tkinter, Python ile birlikte gelir). `openpyxl` yalnızca Excel çıktısı için gerekir.

## Dosyalar
| Dosya | İçerik |
|---|---|
| `app.py` | Arayüz |
| `hexbus.py` | Hexbus protokol sürücüsü + `SIMULATOR` portu (GPIO0 @0x60000000, loopback UART0 @0x60001000) |
| `model.py` | Proje / IP / register / bit alanı modeli, JSON kaydetme |
| `terminal.py` | UART IP'leri için ASCII/HEX terminal sekmesi |
| `axilgen.py` / `wrapgen_gui.py` | AXI4-Lite wrapper üretici (çekirdek / arayüz) |
| `axilpack.py` | Paketli AXI portu wrapper'ı (`_pk`, REQ/RSP) |
| `xbargen.py` | axilxbar wrapper üretici (S0/M0 isimli portlar) |
| `regmap_xlsx.py` | Kütük haritası Excel çıktısı |
| `widgets.py` | Ortak Tk bileşenleri (çok satırlı açıklama, değer tablosu) |
| `uart_validate.py` | axiluart_phy için otomatik donanım doğrulama scripti |
| `uart_validate_sim.py` | `--simulate` için hat ve UART modeli |
| `examples/` | Örnek GPIO projesi ve config dosyaları |

## Kullanım
1. **Port** seç (`SIMULATOR` donanımsız çalışır), baud'u FPGA'daki `BAUD_RATE` ile aynı yap, **Bağlan**.
2. Sol ağaçta IP / register ekle (`+ IP`, `+ Register`, çift tık ile düzenle). Bit alanları register penceresinde tanımlanır.
3. **Register** sekmesi: değer oku/yaz, 1-bit alanlar checkbox, çok bitli alanlar değer kutusu.
   RW registerlarda alan yazma read-modify-write yapar; WO registerlarda son yazılan değer baz alınır.
4. **Canlı İzleme**: izlemeye eklenen registerlar seçilen periyotla okunur.
5. **Config**: sıralı register=değer listesi oluştur, `.axcfg.json` olarak kaydet/yükle, **Cihaza gönder**.
6. **Terminal**: UART IP seç, **Alımı başlat**, ASCII ya da HEX gönder.
   - Gönderirken: **Sonuna ekle** ile CR, LF ya da CR+LF otomatik eklenir; kaçış dizileri `\r \n \t \xNN`.
   - Alırken: **RX satır sonu** Otomatik (CR, LF ve CR+LF her biri bir satır), LF, CR ya da CR+LF.
   - Gösterim ASCII, HEX ya da ASCII+HEX. Parity/frame hatası ile gelen byte'lar kırmızı zeminle işaretlenir (axiluart_v2 RXREG bit 13/14); break ve overflow ayrıca bildirilir.
7. **Proje → Kaydet** ile IP haritası `.axproj.json` dosyasına yazılır.

## Notlar
- Her işlem `A<adres|1>W<veri>\n` veya `A<adres|1>R\n` olarak gönderilir (adres artırma kapalı).
- Yanıtlar: `A` adres, `K` yazma onayı, `R` okuma verisi, `E` AXI bus hatası, `Z` idle.
- Hexbus'ta FIFO yoktur; uygulama her isteğin yanıtını bekleyerek sıralı çalışır.

## Donanım doğrulama (`uart_validate.py`)
```
python uart_validate.py --hexbus COM5 --line COM7 --line-type rs485-4w --lead-us 10
python uart_validate.py --config examples/bench_rs485_4w.json          # ayarlar dosyadan
python uart_validate.py --config bench.json --bauds 460800,921600      # komut satırı dosyayı ezer
python uart_validate.py --hexbus COM5 --line COM7 --save-config bench.json   # ayarları kaydet
python uart_validate.py --simulate                                     # donanımsız deneme
```
Hat ayarları (PHY_CTRL / PHY_TIMING) testten önce script tarafından yazılır, kartta o an
yazılı olan değerler kullanılmaz. Test bitince kartın register'ları eski hâline döner.

| Seçenek | Anlamı |
|---|---|
| `--config` / `--save-config` | Ayarları JSON dosyasından oku / dosyaya yaz |
| `--hexbus`, `--line` | Hexbus debug portu, UART hattındaki PC adaptörü |
| `--loopback` | Adaptör yok, UART TX kendi RX'ine bağlı |
| `--base`, `--clk` | UART base adresi, ACLK frekansı (varsayılan 0x80000000, 50 MHz) |
| `--line-type` | `ttl`, `rs232` (DE 0), `rs422` (DE 1), `rs485-4w` (DE gönderirken), `rs485-2w` (DE gönderirken + half duplex, echo testleri) |
| `--de` | Hat tipinin DE modunu ez: `low`, `high`, `tx` |
| `--lead-us` | DE açılması → start biti, µs (varsayılan 10; transceiver tZH'nin en az 2 katı) |
| `--tail-us` | Stop biti → DE kapanması, µs ya da `auto` = her baud'da 1 bit süresi (varsayılan) |
| `--bauds`, `--formats` | Test matrisi. 5–6 bit için ör. `--formats 5N1,5E1,6O1,6N2` |
| `--count`, `--quick`, `--burst-all` | Byte sayısı, hata enjeksiyonunu atla, burst testini her formatta çalıştır |
| `--report sonuc.json` | Sonuçları JSON olarak kaydet |
| `--sweep lead` / `--sweep tail` | Normal testler yerine: son `--bauds` değerinde, DE gönderirken açılırken hatasız çalışan en küçük lead/tail değerini bulur ve önerilen değeri (2× pay) DEF_TIMING olarak yazar |
| `--sweep-mode` | `smart` (varsayılan): 8 uç byte ile değeri ikiye katlayarak aralığı bulur, ikiye bölerek daraltır, sonucu tam veriyle doğrular. `linear`: sabit listeyi her adımda tam veriyle dener |
| `--sweep-resolution` | Smart modda daraltmanın duracağı aralık, % (varsayılan 5) |
| `--sweep-trials` | Son doğrulamada gereken hatasız deneme sayısı (varsayılan 3) |

Testler: register erişimi (WSTRB dahil), PHY_CTRL/TIMING, geçersiz ayar reddi, FIFO derinliği;
her baud × format için TX ve RX veri bütünlüğü; FIFO kapasitesinde kesintisiz burst alımı;
TX parity bitinin doğruluğu (5–7 bit formatlarda PC bir fazla data biti okur); parity hatası
(karşı parity), frame hatası (PC'de fazladan parity), break, overflow, TX_EN/RX_EN, DE zamanlaması
ve DE modları; `rs485-2w` ile echo bastırma. Hata varsa çıkış kodu 1'dir.


## AXI4-Lite wrapper üretici (`wrapgen_gui.py`, `axilgen.py`)

easyaxil tabanlı AXI4-Lite slave wrapper'ı register/alan tanımından üretir.

```
python wrapgen_gui.py                                   # arayüz
python axilgen.py my_ip.regspec.json -o out --project bench.axproj.json   # komut satırı
```

**Alan tipleri**

| Tip | Port | Davranış |
|---|---|---|
| RW | `o_<reg>_<alan>` | Yazılır/okunur kontrol, reset değeri ayarlanır, WSTRB byte bazında |
| RO | `i_<reg>_<alan>` | Durum girişi, isteğe bağlı 2-FF senkronizör |
| PULSE | `o_<reg>_<alan>` | 1 yazınca 1 clock darbe, 0 okunur |
| W1C | `i_<reg>_<alan>` | Girişte 1 görünce set olur, 1 yazınca silinir (set öncelikli); isteğe bağlı `o_irq` |
| CONST | — | Sabit değer (ID/versiyon) |

Register seçenekleri: `o_<reg>_wr` / `o_<reg>_rd` strobe'ları (FIFO push/pop için).

**HDL Designer için paketli AXI wrapper'ı** (kutu işaretliyse): IP her zaman normal `S_AXI_*` portlu `<ip>.v` olarak üretilir; üstüne `<ip>_pk.v` HDL Designer bloğu (`SLAVE_AXI_REQ/RSP`, içinde normal IP) ve onun `_pk_inst.v` / `_pk_comp.vhd` şablonları eklenir. Hazır IP'ler için aynı blok **Hazır IP için HDL Designer wrapper'ı…** ile üretilir.

**Üretilen dosyalar:** `<ip>.v` (normal S_AXI_* portlu IP), `<ip>_pk.v` + `_pk_inst.v` + `_pk_comp.vhd` (HDL Designer wrapper'ı, isteğe bağlı), `<ip>_tb.v` (kendini kontrol eden testbench),
`<ip>_inst.v`, `<ip>_comp.vhd`, `<ip>_regs.h`, `<ip>_regs.md`, `<ip>.regspec.json`, `<ip>.axproj.json`, `<ip>_regmap.xlsx`.

**Açıklamalar ve değer anlamları:** IP, register ve alan açıklamaları çok satırlı yazılabilir.
Her alana bir değer tablosu eklenebilir: her satırda değer, *okunursa anlamı* ve *yazılırsa etkisi* var.
Hangi sütunların kullanılacağı alan tipine bağlıdır: RO/CONST için sadece okuma, PULSE için sadece yazma,
RW/W1C için ikisi de. HDL ve C yorumlarına açıklamanın yalnızca ilk satırı yazılır.

**Excel kütük haritası** (`Excel'e aktar…`, debug uygulamasında da *Proje* menüsü ve IP sayfasında var):
- *Özet*: IP açıklaması ve register listesi (haritaya bağlantılı)
- *Kütük Haritası*: register ve alan satırları, ayrılmış bitler, reset değerleri, davranış, okuma/yazma anlamları
- *Bit Görünümü*: 32 bitlik yerleşim, tipe göre renkli

**Debug uygulamasına aktarma:** `<ip>.axproj.json` dosyasını doğrudan aç ya da
"Debug projesine ekle" ile mevcut projeye ekle (aynı isimli IP güncellenir). Uygulama alan tiplerini
bilir: W1C/PULSE bitleri RMW sırasında geri yazılmaz, RO/CONST alanlar salt okunur gösterilir.
Register sayfasında okunan her alanın anlamı yanında gösterilir ("Loopback" gibi). Yazma anlamı tanımlı alanlar için
"Hazır değer yaz" listesinden seçim yapınca o değer yazılır. Altta, alan açıklamalarının ve değer tablolarının tamamı listelenir.

**Notlar**
- Adres çözümü yalnızca alt bitlerle yapılır; interconnect'in tam adresi bağlanabilir. Base adres wrapper'da seçilmez: projeye eklerken (Interconnect'te değiştirilebilir) ve Excel'e aktarırken sorulur. Excel'de Özet sayfasındaki sarı hücre değişince tüm adresler formülle güncellenir; header'daki `<IP>_BASE` `#ifndef` ile ezilebilir.
- Libero: `C_AXI_ADDR_WIDTH` parametresini interconnect slave adres genişliğine eşitle; BIF tipi AXI4, slave slotu AXI4-Lite.
- Tüm registerlar `S_AXI_ARESETN` ile resetlenir (PolarFire `initial` bloğunu yok sayar).
- Testbench: `iverilog -g2012 -o sim <ip>_tb.v <ip>.v && vvp sim` → `... 0 errors: PASSED`.


## Ayrı pencereler

Ana pencere bir başlatıcıdır, her araç kendi penceresinde açılır. Araçların içindeki sekmeler de (Wrapper: Bit alanları / IP açıklaması / Verilog önizleme; Interconnect: Adres haritası / Blok diyagramı / Verilog önizleme; Debug: Register / Canlı izleme / Config) sekmeye çift tıklayarak, sağ tık menüsünden ya da sağ üstteki **Ayrı pencere** düğmesiyle ayrı pencereye alınabilir; çoklu monitörde yan yana kullanılabilir. Pencere kapatılınca (ya da **Pencereye geri al**) sekme yerine döner.

## Sürümler

Geliştirme aşaması: **early-alpha** (sonra alpha → beta → release). Program ve her araç kendi sürümünü taşır; şu an hepsi `early-alpha 0.0.1`. Kaydedilen proje dosyasına `generator` olarak yazan programın adı ve sürümü eklenir. early-alpha'da eski dosya biçimleriyle geriye dönük uyumluluk yoktur; alpha'dan itibaren sağlanacaktır.

## UART-to-AXI Debug Bridge (AXIL Studio)

- Adres haritası ve register haritaları projeden gelir; projede olmayan IP'ler **IP ekle** ile bir `.regspec.json` (base adres sorulur) ya da başka bir `.axproj.json` dosyasından eklenir (projede `debug` altında saklanır, interconnect'i etkilemez). **Ham adres erişimi** ile haritası olmayan herhangi bir adres okunup yazılır.
- Her bit alanının "Yazılacak" girişi tipine göre seçilir: tanımlı yazma değerleri varsa liste, bool / 1 bit için anahtar, diğerleri için alanın veri tipinde (uint, int, hex, binary, fixed point, ASCII …) sayı kutusu. **PULSE** alanları için *Darbe gönder*, **W1C** bayrakları için *Temizle* düğmesi anında yazar; RO / CONST salt okunurdur.
- Yazma read-modify-write'tır: okunan değer temel alınır, PULSE ve W1C bitleri 0 yazılır; salt yazılır registerlarda son yazılan değer temel alınır.
- Her bit alanının **⋯** menüsü (ya da sağ tık): **Özel değer yaz…** istenen veri tipinde değeri yazılacağa ekler ya da hemen yazar, istenirse register haritasına yeni değer açıklaması olarak kaydeder; **Veri tipi ve değer açıklamaları…** projedeki IP'nin register haritasını düzenler (Wrapper'ın `.regspec.json` dosyası ayrıdır).
- Sağ tarafta üstte sekmeler: **Register**, **Canlı izleme**, **Config**; **Trafik** altta, yüksekliği ayarlanabilir. Hangi ekrandan okunursa okunsun (register paneli, canlı izleme, config) son okunan değer ve saati her ekranda gösterilir.
- **Canlı izleme**: İzle düğmesi ya da ağaçta sağ tık ile eklenen registerlar seçilen periyotta (ms) okunur; değişen değerler vurgulanır. Register satırına tıklayınca bit alanları alt satırlarda değer ve anlamlarıyla açılır. Seçili registerın **geçmişi** (milisaniye zaman damgalı her okuma ya da yalnızca değişimler, değişen bit alanlarıyla) altta listelenir; tüm kayıt **CSV'ye aktar** ile dışa alınır.
- **Config**: Config'e ekle ile register panelindeki yazılacak değer listeye eklenir; satıra çift tıklayınca bit alanlarıyla düzenlenip güncellenir. Satıra tıklayınca bit alanları açılır; *Cihazda* sütunu son okunan değeri gösterir, config'ten farklı alanlar kırmızıdır. Sıralama, Cihazdan oku, Karşılaştır, Cihaza gönder (sırayla yazar ve geri okur), `.axcfg.json` aç / kaydet.
- UART terminali sonraki sürümlerde eklenecek.

## IP Library (AXIL Studio)

- Her IP'nin bir **sürümü** vardır (Wrapper'da *Sürüm* alanı, ör. `1.0.0`, `2.1.0-beta`); Verilog başlığına, C header'a (`<IP>_IP_VERSION`) ve dokümana yazılır. Kütüphane aynı IP'nin birden fazla sürümünü tutar; en yenisi üstte, önceki sürümler altında listelenir ve istenen sürüm projeye eklenir. Projeden kaydederken sürüm sorulur (öneri: en yeni sürümün bir sonrası).
- IP kütüphaneleri `.axlib.json` dosyalarıdır; aynı anda birden fazla kütüphane açılabilir, açık kütüphaneler projeler arasında açık kalır. Her değişiklik dosyaya hemen yazılır.
- Kütüphanedeki IP **Projeye ekle…** ile bir base adresle projeye eklenir (aynı adlı IP varsa ikinci kopya ya da değiştirme); proje bu kütüphaneyi `libraries` altında hatırlar ve açılınca kütüphaneyi de açar. Projedeki IP **Kütüphaneye kaydet…** ile kütüphaneye yeni sürüm olarak kaydedilir.
- Wrapper'daki **Kütüphaneye kaydet…** register spesifikasyonunu da saklar; kütüphanede **Wrapper'da aç** ile HDL yeniden üretilebilir. İçe aktarma: `.regspec.json` ya da başka bir `.axproj.json`.
- Debug Bridge'de **IP ekle → IP kütüphanesinden…** ile projede olmayan bir kütüphane IP'si debug'a eklenir.

## Address Map & Interconnect Configurator (AXIL Studio)

- Listedeki her slave kullanılan bir porttur (EN=1); liste sırası port numarasıdır (SLAVE0, SLAVE1 …). Satırlar sürükle-bırak ya da ↑/↓ ile sıralanır.
- **Adresler**: her slave ya *otomatik* ya *sabit*tir. Otomatikler başlangıç adresinden itibaren liste sırasıyla ardışık ve boyutlarına hizalı adres alır; sıra, boyut ya da liste değişince adresler hemen güncellenir. Adres hücresine yazılan değer sabitlenir (🔒), *Adresleme* hücresine tıklayınca otomatiğe döner. Otomatikler sabitlerin aralığını atlar.
- **Boyut**: her slave'e ayrılan adres aralığı; varsayılan 4 KB, IP daha büyükse kendiliğinden büyür, hücredeki listeden büyütülebilir.
- **Adrese göre sırala** listeyi bir kez adres sırasına dizer.
- Slave'ler projedeki IP'leri izler; yeni IP listenin sonuna eklenir. **Sil** IP'yi bu interconnect'ten çıkarır (proje IP'si projede kalır, **IP bağla…** ile geri eklenir). **Harici slave**: projede register tanımı olmayan slave'ler (bellek, hazır IP).
- Master'lar **+ Master / Sil** ile eklenir, adlarına çift tıklayarak değiştirilir.
- **Blok diyagramı**: solda master'lar, ortada crossbar, sağda slave'ler; pinler üretilen HDL'deki ad, genişlik ve yönlerle çizilir, bloklarda kendi taraflarındaki port adları (M_AXI_* / S_AXI_*, MASTER_AXI_* / SLAVE_AXI_*) yazar. Fare tekerleği imleç etrafında yakınlaştırır, boş alanı sürüklemek kaydırır, trackpad'de iki parmakla sıkıştırma da çalışır.
- **Verilog önizleme** sekmesi üretilecek wrapper'ı gösterir. Port stilleri: *HDL Designer (paketli)* ve *Ayrık AXI sinyalleri* (düz vektör stili yalnızca komut satırında).
- Ayarlar proje dosyasında (`.axproj.json` içindeki `interconnect`) saklanır; `.xbar.json` olarak dışa/içe aktarılabilir.

## axilxbar wrapper (`Araçlar → axilxbar wrapper üret`)

ZipCPU `axilxbar` portları düz vektörlerdir; wrapper her port için ayrı, isimli bir arayüz çıkarır:

- `MASTER0_AXI_*`, `MASTER1_AXI_*` … → master'lara
- `SLAVE0_AXI_*`, `SLAVE1_AXI_*` … → slave'lere

Port sayıları projeye göre seçilir. Kullanılmayan portlar `EN=0` ile üretilir; böylece yedek port bırakılabilir.
Her port için generic'ler:

| Generic | Anlam |
|---|---|
| `MASTER<i>_EN` | 1: master portu kullanımda, 0: boşta bağlı |
| `SLAVE<j>_EN` | 1: slave portu kullanımda, 0: içeride DECERR cevaplayıcı |
| `SLAVE<j>_ADDR`, `SLAVE<j>_MASK` | `(addr & MASK) == ADDR` ise slave j seçilir |

HDL Designer'da slave eklemek ya da adres değiştirmek için generic'i değiştirip bundle çizmek yeterli.
Port sayısı yetmezse kapasiteyi artırıp yeniden üret ve sembolü güncelle. Kullanılmayan portlar bağlanmadan bırakılabilir.
Adres haritasının asıl kaynağı debug projesi olsun; generic değiştirirsen projeyi de güncelle.

Slave listesi debug projesinden alınır. Maske, IP'nin kullandığı alana ve min. pencereye göre hesaplanır.
Hizalama, çakışma ve sığma kontrol edilir. Ayarlar `.xbar.json` olarak kaydedilir.

```
python axilgen.py --xbar bench.axproj.json --nm 2 --ns 8 --xbar-name axil_xbar -o out
python axilgen.py --xbar out/axil_xbar.xbar.json -o out        # kayıtlı ayarlarla yeniden üret
```

Projeye `hdl/axilxbar.v` (Verilog-2005 için yamalı: parametre listesindeki `localparam AW` → `parameter`),
wb2axip'ten de `addrdecode.v` ve `skidbuffer.v` dosyalarını ekle. Haritada olmayan adres DECERR döner.


## Paketli AXI portu (REQ/RSP)

Blok diyagramda bir AXI4-Lite arayüzü 19 tel yerine 2 telle bağlanır:

| Vektör | Yön | İçerik |
|---|---|---|
| `*_AXI_REQ [110:0]` | master → slave | AWADDR, AWPROT, AWVALID, WDATA, WSTRB, WVALID, BREADY, ARADDR, ARPROT, ARVALID, RREADY |
| `*_AXI_RSP [40:0]` | slave → master | AWREADY, WREADY, BRESP, BVALID, ARREADY, RDATA, RRESP, RVALID |

Paketli bus her zaman 32 bit adres ve veri taşır; daha dar adresli çekirdekler wrapper içinde kesilir.

- Xbar paketli stilde portlar `MASTER<i>_AXI_REQ/RSP` ve `SLAVE<j>_AXI_REQ/RSP` olur.
- Paketli IP'lerin üst modülü `SLAVE_AXI_REQ/RSP` portludur.
- Hazır modüller için: **Araçlar → Hazır IP'yi paketle** ya da `python axilgen.py --pack axilgpio.v -o out`.
  ANSI başlıklı her modülde çalışır; `S_AXI_*` grubu `SLAVE_AXI_REQ/RSP`, `M_AXI_*` grubu `MASTER_AXI_REQ/RSP` olur, diğer portlar ve parametreler aynen geçer.
- `hdl/` içinde `axilgpio_pk.v`, `axiluart_phy_pk.v`, `hexbus_axil_top_pk.v` hazır.

HDL Designer'da: hexbus `MASTER_AXI_REQ/RSP` ↔ xbar `MASTER0_AXI_REQ/RSP`; xbar `SLAVE0_AXI_REQ/RSP` ↔ IP `SLAVE_AXI_REQ/RSP`.


## Xbar port stilleri

| Stil | Portlar | Port sayısı değişince |
|---|---|---|
| Paketli, port başına | `MASTER0_AXI_REQ/RSP`, `SLAVE0_AXI_REQ/RSP` … + `MASTER<i>_EN`, `SLAVE<j>_EN/ADDR/MASK` | Yedek port varsa generic, yoksa yeniden üret |
| Paketli düz vektör | `MASTERS_AXI_REQ[NM*111-1:0]`, `SLAVES_AXI_REQ[NS*111-1:0]` … + `NM`, `NS`, `SLAVE_ADDR`, `SLAVE_MASK` | Sadece generic (yeniden üretim yok) |
| Ayrık sinyaller | `MASTER0_AXI_AWADDR`, `SLAVE0_AXI_AWADDR` … (19 sinyal/port) | Yedek port varsa generic, yoksa yeniden üret |

Düz vektörde slave j, `SLAVES_AXI_REQ[j*111 +: 111]` / `SLAVES_AXI_RSP[j*41 +: 41]` dilimidir; `SLAVE_ADDR` içinde slave 0 en sağdadır (LSB).
Komut satırı: `--xbar-style packed|flat|signals`.
