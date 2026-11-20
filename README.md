# AXIL Studio

AXI4-Lite slave IP'leri için register haritası tasarımı, HDL üretimi, adres haritası, interconnect ve
donanım üzerinde test araçlarını tek çatı altında toplayan hub (PySide6/Qt, Windows ve macOS'ta aynı
görünüm, açık/koyu tema):

| Araç | Ne yapar |
|---|---|
| **Register Map Editor** | Bir IP'nin registerlarını, bit alanlarını, veri tiplerini ve değer anlamlarını tasarlar. Adres içermez. |
| **Slave Interface Wrapper** | Register haritasından AXI4-Lite slave HDL, testbench, C header, doküman ve Excel üretir |
| **Address Map Editor** | IP'leri adres haritasına yerleştirir (aynı IP birden çok kez), otomatik / sabit adres, hizalama ve çakışma kontrolü |
| **Interconnect Configurator** | Adres haritası + master'lar → axilxbar crossbar wrapper'ı, blok diyagramı |
| **UART-to-AXI Debug Bridge** | Hexbus üzerinden karttaki registerları okur/yazar, bit alanlarını çözer |
| **IP Library** | Projedeki tüm IP'leri gösterir; IP kütüphaneleri oluşturur, kütüphanelerden projeye IP alır |

Ana pencere bir başlatıcıdır; her araç kendi penceresinde açılır (Ctrl+1 … Ctrl+6). Araçlar aynı projeyi
(`.axproj.json`) paylaşır ve birbirini açar:

- Register haritası nerede düzenlenirse düzenlensin (Wrapper, Address Map Editor, IP Library, Debug Bridge)
  **Register Map Editor** açılır.
- Adres nerede düzenlenirse düzenlensin (Interconnect, IP Library, Debug Bridge, Register Map Editor'daki
  *Adres haritasına ekle*) **Address Map Editor** açılır.
- IP seçilen / eklenen her yerde (Address Map Editor *IP ekle*, Register Map Editor *IP aç*, Wrapper *IP seç*)
  **IP Library** seçici olarak açılır; kütüphanedeki bir IP seçilirse önce projeye alınır.

```
pip install -r requirements.txt
python studio.py                                   # hub
python studio.py --tool addrmap                    # tek araç: regmap | wrapper | addrmap | interconnect | debug | library
python studio.py examples/gpio_demo.axproj.json    # projeyle aç
```

## Proje modeli

Proje dosyası (`.axproj.json`, biçim sürümü 2) iki ayrı şey tutar:

- **IP'ler** (`ips`): register haritaları (IP tanımları). Adres yoktur. Register Map Editor'da kaydedilen
  IP tam register spesifikasyonunu da (`regspec`) saklar, böylece Wrapper HDL'i yeniden üretebilir.
- **Adres haritası** (`instances`): IP örnekleri — ad, hangi IP, base adres, pencere boyutu, sabit/otomatik.
  Aynı IP birden çok kez yerleştirilebilir (ör. `UART0`, `UART1` ikisi de `axiluart` IP'si). Harici
  slave'ler (bellek, hazır IP) register haritası olmadan adres ve boyutla girilir. Liste sırası slave port
  numarasıdır.

Bir IP'nin register haritası değişince tüm örnekleri onu kullanır; IP büyürse pencereleri ve otomatik
adresler kendiliğinden güncellenir. IP yeniden adlandırılırsa örnekleri onu izler. Eski (sürüm 1) proje
dosyaları açılırken IP'ler ve interconnect slave listesi otomatik olarak IP + adres haritasına çevrilir.

## Register Map Editor

- Sol tarafta IP adı ve sürümü, register listesi; sağda 32 bitlik alan haritası (boş bite çift tık yeni alan
  açar), bit alanları ve değer açıklamaları (alan satırına tıklayınca alt tablo olarak açılır), IP açıklaması.
- Her alanın bir **veri tipi** vardır (`numfmt.py`): hex, unsigned, signed (2's complement), binary, octal,
  bool, ASCII, unsigned/signed fixed point (Q, kesir biti seçilir) ya da enum (değer anlamı). Reset ve değerler
  bu tipte gösterilir/girilir; Debug Bridge alanı varsayılan olarak bu tipte gösterir.
- **Projeye kaydet** (Ctrl+Shift+S) register haritasını projedeki IP olarak saklar; **IP aç** IP Library'den
  bir IP açar. `.regspec.json` dosyasına kaydedip açmak ve **Kütüphaneye kaydet** de mümkündür.
- **Adres haritasına ekle**: IP'yi projeye kaydeder, adres haritasına bir örnek ekler ve Address Map Editor'u açar.
  **Wrapper'da aç**: HDL üretimi için Slave Interface Wrapper'ı açar.
- Kısayollar: Ctrl+R yeni register, Ctrl+F yeni alan, Enter düzenle, Delete sil.

## Slave Interface Wrapper

easyaxil tabanlı AXI4-Lite slave wrapper'ını register haritasından üretir. **IP seç** (IP Library) ya da
**Dosyadan aç** (`.regspec.json`) ile kaynak seçilir; sağda register haritası özeti ile Verilog, C header ve
doküman önizlemesi vardır. Register haritasını değiştirmek için **Register haritasını düzenle** (ya da özette
bir registera çift tık) Register Map Editor'u açar; değişiklikler Wrapper'a anında yansır.

```
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

**Excel kütük haritası** (Wrapper'da tek IP, Address Map Editor'da tüm adres haritası için `Excel'e aktar…`):
- *Özet*: IP açıklaması ve register listesi (haritaya bağlantılı)
- *Kütük Haritası*: register ve alan satırları, ayrılmış bitler, reset değerleri, davranış, okuma/yazma anlamları
- *Bit Görünümü*: 32 bitlik yerleşim, tipe göre renkli

**Notlar**
- Adres çözümü yalnızca alt bitlerle yapılır; interconnect'in tam adresi bağlanabilir. Register haritasında ve Wrapper'da base adres yoktur; adresler yalnızca Address Map Editor'da verilir (Wrapper'ın Excel / `.axproj.json` çıktısı IP'nin adres haritasındaki ilk adresini kullanır). Excel'de Özet sayfasındaki sarı hücre değişince tüm adresler formülle güncellenir; header'daki `<IP>_BASE` `#ifndef` ile ezilebilir.
- Libero: `C_AXI_ADDR_WIDTH` parametresini interconnect slave adres genişliğine eşitle; BIF tipi AXI4, slave slotu AXI4-Lite.
- Tüm registerlar `S_AXI_ARESETN` ile resetlenir (PolarFire `initial` bloğunu yok sayar).
- Testbench: `iverilog -g2012 -o sim <ip>_tb.v <ip>.v && vvp sim` → `... 0 errors: PASSED`.



## Address Map Editor

- Tablodaki her satır bir slave portudur; sıra port numarasıdır (SLAVE0, SLAVE1 …). Satırlar sürükle-bırak ya da
  ↑/↓ ile sıralanır, **Adrese göre sırala** listeyi bir kez adres sırasına dizer.
- **IP ekle…** IP Library'yi açar; aynı IP istenildiği kadar eklenebilir, her örnek benzersiz bir ad alır
  (`GPIO0`, `GPIO0_1` …). Ad hücresine çift tıklayarak örnek yeniden adlandırılır. **Bir kez daha ekle** seçili
  IP'nin yeni bir örneğini ekler. **Sil** örneği haritadan çıkarır; IP tanımı projede kalır.
- **Adresler**: her örnek ya *otomatik* ya *sabit*tir. Otomatikler başlangıç adresinden itibaren liste sırasıyla
  ardışık ve boyutlarına hizalı adres alır; sabitlerin aralığını atlar. Adres hücresine yazılan değer sabitlenir
  (🔒), *Adresleme* hücresine tıklayınca otomatiğe döner.
- **Boyut**: örneğe ayrılan adres aralığı; varsayılan 4 KB, IP daha büyükse kendiliğinden büyür, hücredeki
  listeden büyütülebilir.
- **Harici slave**: projede register haritası olmayan slave'ler (bellek, hazır IP) ad, adres ve kullanılan alanla girilir.
- **Excel'e aktar…** tüm adres haritasını register ayrıntılarıyla `.xlsx` olarak yazar.

## Interconnect Configurator

- Slave portları adres haritasından gelir (salt okunur liste; satıra çift tık Address Map Editor'u açar).
  Burada crossbar'ın kendisi ayarlanır: modül adı, port stili ve master'lar (**+ Master / Sil**, ada çift tık).
- **Blok diyagramı**: solda master'lar, ortada crossbar, sağda slave'ler; pinler üretilen HDL'deki ad, genişlik ve yönlerle çizilir, bloklarda kendi taraflarındaki port adları (M_AXI_* / S_AXI_*, MASTER_AXI_* / SLAVE_AXI_*) yazar. Fare tekerleği imleç etrafında yakınlaştırır, boş alanı sürüklemek kaydırır, trackpad'de iki parmakla sıkıştırma da çalışır.
- **Verilog önizleme** sekmesi üretilecek wrapper'ı gösterir. Port stilleri: *HDL Designer (paketli)* ve *Ayrık AXI sinyalleri* (düz vektör stili yalnızca komut satırında).
- Ayarlar proje dosyasında (`.axproj.json` içindeki `interconnect`) saklanır. **Ayar dosyası aç** `.xbar.json`
  dosyasından modül adı, stil ve master'ları alır (slave'ler ve adresler adres haritasından gelir); **Ayar dosyası
  kaydet** slave'lerle birlikte tam ayarı yazar (komut satırı için).

## axilxbar wrapper

ZipCPU `axilxbar` portları düz vektörlerdir; wrapper her port için ayrı, isimli bir arayüz çıkarır:

- `MASTER0_AXI_*`, `MASTER1_AXI_*` … → master'lara
- `SLAVE0_AXI_*`, `SLAVE1_AXI_*` … → slave'lere

Port sayıları adres haritasına göre seçilir. Kullanılmayan portlar `EN=0` ile üretilir; böylece yedek port bırakılabilir.
Her port için generic'ler:

| Generic | Anlam |
|---|---|
| `MASTER<i>_EN` | 1: master portu kullanımda, 0: boşta bağlı |
| `SLAVE<j>_EN` | 1: slave portu kullanımda, 0: içeride DECERR cevaplayıcı |
| `SLAVE<j>_ADDR`, `SLAVE<j>_MASK` | `(addr & MASK) == ADDR` ise slave j seçilir |

HDL Designer'da slave eklemek ya da adres değiştirmek için generic'i değiştirip bundle çizmek yeterli.
Port sayısı yetmezse kapasiteyi artırıp yeniden üret ve sembolü güncelle. Kullanılmayan portlar bağlanmadan bırakılabilir.
Adres haritasının asıl kaynağı projedeki adres haritası (Address Map Editor) olsun; generic değiştirirsen projeyi de güncelle.

Slave listesi projenin adres haritasından alınır. Maske, IP'nin kullandığı alana ve min. pencereye göre hesaplanır.
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
- Hazır modüller için: Slave Interface Wrapper'da **Hazır IP için HDL Designer wrapper'ı…** ya da `python axilgen.py --pack axilgpio.v -o out`.
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

## UART-to-AXI Debug Bridge

- Adres haritası Address Map Editor'dan gelir: aynı IP birden çok kez yerleştirildiyse her örnek kendi adı ve adresiyle ayrı listelenir. Projede olmayan IP'ler yalnızca hata ayıklama için **Ek IP** ile bir `.regspec.json`, başka bir `.axproj.json` ya da IP kütüphanesinden eklenir (projede `debug` altında saklanır, adres haritasını ve interconnect'i etkilemez). **Ham adres erişimi** ile haritası olmayan herhangi bir adres okunup yazılır.
- Her bit alanının "Yazılacak" girişi tipine göre seçilir: tanımlı yazma değerleri varsa liste, bool / 1 bit için anahtar, diğerleri için alanın veri tipinde (uint, int, hex, binary, fixed point, ASCII …) sayı kutusu. **PULSE** alanları için *Darbe gönder*, **W1C** bayrakları için *Temizle* düğmesi anında yazar; RO / CONST salt okunurdur.
- Yazma read-modify-write'tır: okunan değer temel alınır, PULSE ve W1C bitleri 0 yazılır; salt yazılır registerlarda son yazılan değer temel alınır.
- Her bit alanının **⋯** menüsü (ya da sağ tık): **Özel değer yaz…** istenen veri tipinde değeri yazılacağa ekler ya da hemen yazar, **Register Map Editor'da düzenle…** IP'nin register haritasını o bit alanı seçili olarak açar. Ağaçta sağ tık: *Register haritasını düzenle* (Register Map Editor), *Adresini düzenle* (Address Map Editor). Register haritası ve proje adresleri Debug Bridge'de değiştirilmez.
- Sağ tarafta üstte sekmeler: **Register**, **Canlı izleme**, **Config**; **Trafik** altta, yüksekliği ayarlanabilir. Hangi ekrandan okunursa okunsun (register paneli, canlı izleme, config) son okunan değer ve saati her ekranda gösterilir.
- **Canlı izleme**: İzle düğmesi ya da ağaçta sağ tık ile eklenen registerlar seçilen periyotta (ms) okunur; değişen değerler vurgulanır. Register satırına tıklayınca bit alanları alt satırlarda değer ve anlamlarıyla açılır. Seçili registerın **geçmişi** (milisaniye zaman damgalı her okuma ya da yalnızca değişimler, değişen bit alanlarıyla) altta listelenir; tüm kayıt **CSV'ye aktar** ile dışa alınır.
- **Config**: Config'e ekle ile register panelindeki yazılacak değer listeye eklenir; satıra çift tıklayınca bit alanlarıyla düzenlenip güncellenir. Satıra tıklayınca bit alanları açılır; *Cihazda* sütunu son okunan değeri gösterir, config'ten farklı alanlar kırmızıdır. Sıralama, Cihazdan oku, Karşılaştır, Cihaza gönder (sırayla yazar ve geri okur), `.axcfg.json` aç / kaydet.
- UART terminali sonraki sürümlerde eklenecek.


## IP Library

- **Bu projedeki IP'ler**: projedeki tüm IP tanımları; altlarında adres haritasındaki örnekleri ve adresleri.
  Projedeki IP için: *Adres haritasına ekle*, *Register Map Editor'da aç*, *Wrapper'da aç*, *Kütüphaneye kaydet…*,
  *Projeden kaldır* (örnekleri de çıkar). Bir örneğe çift tık Address Map Editor'u açar.
- **Kütüphaneler**: IP kütüphaneleri `.axlib.json` dosyalarıdır; aynı anda birden fazla kütüphane açılabilir,
  açık kütüphaneler projeler arasında açık kalır. Her değişiklik dosyaya hemen yazılır. Kütüphanedeki IP
  *Projeye al* ile adres verilmeden projenin IP'lerine kopyalanır, *Adres haritasına ekle* ile ayrıca
  yerleştirilir. Projede aynı adlı IP varsa: projedekini kullan, kütüphanedekiyle değiştir ya da yeni adla al.
  Proje kullandığı kütüphaneleri `libraries` altında hatırlar ve açılınca onları da açar.
- Her IP'nin bir **sürümü** vardır (ör. `1.0.0`, `2.1.0-beta`); Verilog başlığına, C header'a (`<IP>_IP_VERSION`)
  ve dokümana yazılır. Kütüphane aynı IP'nin birden fazla sürümünü tutar; en yenisi üstte, önceki sürümler
  altında listelenir. Projeden kaydederken sürüm sorulur (öneri: en yeni sürümün bir sonrası).
- İçe aktarma: `.regspec.json` ya da başka bir `.axproj.json`.

## Ayrı pencereler

Ana pencere bir başlatıcıdır, her araç kendi penceresinde açılır. Araçların içindeki sekmeler de sekmeye çift
tıklayarak, sağ tık menüsünden ya da sağ üstteki **Ayrı pencere** düğmesiyle ayrı pencereye alınabilir; çoklu
monitörde yan yana kullanılabilir. Pencere kapatılınca (ya da **Pencereye geri al**) sekme yerine döner.

## Sürümler

Geliştirme aşaması: **early-alpha** (sonra alpha → beta → release). Program ve her araç kendi sürümünü taşır
(bkz. `CHANGELOG.md`). Kaydedilen proje dosyasına `generator` olarak yazan programın adı ve sürümü eklenir.

## Çekirdek modüller

| Dosya | İçerik |
|---|---|
| `axil_studio/` | Hub ve araçlar (PySide6) |
| `model.py` | Proje / IP / register / bit alanı modeli, adres haritası örnekleri, JSON kaydetme |
| `addrmap.py` | Adres haritası: otomatik adresleme, pencereler, doğrulama, interconnect slave'leri |
| `library.py` | IP kütüphanesi dosyaları (`.axlib.json`) |
| `hexbus.py` | Hexbus protokol sürücüsü + `SIMULATOR` portu (GPIO0 @0x60000000, loopback UART0 @0x60001000) |
| `axilgen.py` | AXI4-Lite wrapper üretici (komut satırı da var) |
| `axilpack.py` | Paketli AXI portu wrapper'ı (`_pk`, REQ/RSP) |
| `xbargen.py` | axilxbar wrapper üretici (isimli portlar) |
| `regmap_xlsx.py` | Kütük haritası Excel çıktısı |
| `numfmt.py` | Bit alanı veri tipleri ve gösterimleri |
| `uart_validate.py` / `uart_validate_sim.py` | axiluart_phy için otomatik donanım doğrulama scripti ve `--simulate` modeli |
| `examples/` | Örnek GPIO projesi ve config dosyaları |

Hexbus notları: her işlem `A<adres|1>W<veri>\n` veya `A<adres|1>R\n` olarak gönderilir (adres artırma kapalı);
yanıtlar `A` adres, `K` yazma onayı, `R` okuma verisi, `E` AXI bus hatası, `Z` idle. Hexbus'ta FIFO yoktur;
her isteğin yanıtı beklenerek sıralı çalışılır.

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
