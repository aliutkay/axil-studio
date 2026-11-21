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

Ana pencere bir başlatıcıdır; her araç kendi penceresinde açılır (Ctrl+1 … Ctrl+6). IP Library, Proje kartındaki
**IP'leri yönet** düğmesiyle açılır. Doğrulama sonuçları her aracın penceresinin alt çubuğunda gösterilir (yeşil: hata
yok; kırmızı: hata sayısı ve ilki — üzerine gelince hepsi, tıklayınca liste). Araçlar aynı projeyi
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
  Aynı IP birden çok kez yerleştirilebilir (ör. `UART_0`, `UART_1` ikisi de `UART` IP'si). Harici
  slave'ler (bellek, hazır IP) register haritası olmadan adres ve boyutla girilir. Liste sırası slave port
  numarasıdır.

Bir IP'nin register haritası değişince tüm örnekleri onu kullanır; IP büyürse pencereleri ve otomatik
adresler kendiliğinden güncellenir. IP yeniden adlandırılırsa örnekleri onu izler. Eski (sürüm 1) proje
dosyaları açılırken IP'ler ve interconnect slave listesi otomatik olarak IP + adres haritasına çevrilir.

## Register Map Editor

- Solda **IP** paneli (ad, sürüm, açıklama — açıklama *Düzenle…* ile ayrı pencerede yazılır) ve **Registerlar**
  paneli (arama kutusu: register ya da bit alanı adı / açıklaması; bit alanında bulunanlar register satırında
  gösterilir ve alan listesinde vurgulanır, Ctrl+K). Listelerin sonunda bırakma için boş alan vardır.
- Sağda 32 bitlik alan haritası (boş bite çift tık yeni alan
  açar), bit alanları ve değer açıklamaları (yalnızca *Değer açıklamaları* sütununa tıklayınca alt tablo olarak açılır).
- Her alanın bir **veri tipi** vardır (`axil_studio/core/numfmt.py`): hex, unsigned, signed (2's complement), binary, octal,
  bool, ASCII, unsigned/signed fixed point (Q, kesir biti seçilir) ya da enum (değer anlamı). Reset ve değerler
  bu tipte gösterilir/girilir; Debug Bridge alanı varsayılan olarak bu tipte gösterir.
- **Projeye kaydet** (Ctrl+Shift+S) projedeki tüm IP'leri gösteren bir pencere açar; ada göre yeni IP, güncelleme,
  yeniden adlandırma ya da başka bir IP'nin yerine geçme açıkça yazılır; **IP aç** IP Library'den
  bir IP açar. `.regspec.json` dosyasına kaydedip açmak da mümkündür. **Kütüphaneye kaydet** her zaman kütüphane seçme
  penceresini açar: açık kütüphaneler, içlerindeki IP'ler ve sürümleri; sürüm orada değiştirilebilir, yeni kütüphane
  oluşturulabilir ya da açılabilir (yeni IP / yeni sürüm / üzerine yazılacak bilgisi gösterilir).
- **Adres haritasına ekle**: IP'yi projeye kaydeder, adres haritasına bir örnek ekler ve Address Map Editor'u açar.
  **Wrapper'da aç**: HDL üretimi için Slave Interface Wrapper'ı açar.
- **Bit alanları** sekmesinin altında **yerleştirilmemiş bit alanları** bölümü ve **Port içe aktar…** düğmesi vardır
  (Ctrl+I). Sol panelde yalnızca registerlar listelenir.
- **Port içe aktar…**: register haritasının arkasındaki IP çekirdeğinin portları Verilog / SystemVerilog
  ya da VHDL olarak yapıştırılır (modülün / entity'nin tamamı, yalnızca port listesi ya da port satırları; dil
  kendiliğinden anlaşılır; modülün içindeki `function` / `task` bildirimleri port sayılmaz). Parametre / generic ifadeleri (`W-1`, `$clog2(N)`, `2**5`), `[15:8]` gibi aralıklar,
  çok boyutlu diziler, `integer range` çözülür; çözülemeyen genişlik tabloda elle yazılır. Çekirdeğin girişleri
  RW (registerdan sürülür), çıkışları RO (registerdan okunur) olarak gelir; tip satır başına değiştirilebilir. Saat
  ve reset portları baştan seçili gelmez. 32 bitten geniş sinyaller 32 bitlik parçalara bölünür
  (`TIMESTAMP_0` [31:0], `TIMESTAMP_1` [63:32]). İsteğe bağlı alan adı öneki ve `i_` / `o_` öneklerini atma vardır;
  her alanın açıklamasına kaynak yazılır (`my_core.o_timestamp[63:32]`).
- Seçilen portlar **yerleştirilmemiş bit alanları** listesine eklenir; içe aktarma istenildiği kadar, blok blok
  tekrarlanabilir. Alanlar bit haritasına sürüklenir (yeşil önizleme: yer uygun, kırmızı: dolu; birden çok alan yan
  yana yerleşir), bir registerın üstüne bırakılınca ilk boş bitlere, register listesinin boş alanına bırakılınca
  yeni bir registera girer. *Yerleştir* / çift tık seçili registera, *Yeni registera* yeni registera koyar; *Sil* /
  Delete istenmeyenleri siler.
- Yerleşmiş bir alan bit haritasında **sürüklenerek taşınır** (tutulan bit imlecin altında kalır; önizleme yeşilse
  yer boştur, kırmızıysa bırakılmaz). Yerleştirilmemiş listesine sürüklenince ya da sağ tık → *Yerleştirilmemişlere
  geri al* ile registerdan çıkıp listeye döner.
  Yerleştirilmemiş alanlar IP ile birlikte saklanır (proje, `.regspec.json`, kütüphane) ama HDL'e girmez.
- Kısayollar: Ctrl+R yeni register, Ctrl+F yeni alan, Ctrl+I port içe aktar, Enter düzenle, Delete sil.

## Slave Interface Wrapper

AXI4-Lite slave'i register haritasından üretir. Üretilen `<ip>.v`, ZipCPU wb2axip `hdl/easyaxil.v` dosyasının
kendisidir; yalnızca easyaxil'in kullanıcıya bıraktığı yerler değişir:

- modül adı, `C_AXI_ADDR_WIDTH` varsayılanı ve IP tarafı portları,
- *AXI-lite register logic* bölümü: r0..r3 yerine IP'nin registerları, easyaxil üslubuyla (register başına
  32 bitlik kelime, `apply_wstrb`, yazma ve okuma `case`'i; okuma `case`'ine tanımsız offset için `default` eklenir),
- parametre listesindeki `localparam C_AXI_DATA_WIDTH` Verilog-2005 için `parameter` yazılır (değiştirilmez),
- formal bölümünde yalnızca easyaxil'in r0..r3'e özel register kontrolleri çıkar (`faxil_slave` kontrolleri kalır).

AXI el sıkışması (skidbuffer'lı ve skidbuffer'sız iki dal, `OPT_SKIDBUFFER`, `OPT_LOWPOWER`) olduğu gibi kalır;
`OPT_SKIDBUFFER=1` için `hdl/skidbuffer.v` gerekir. Üretilen dosyayı `hdl/easyaxil.v` ile karşılaştırarak farklar
görülebilir. `FORMAL` bölümü yalnızca formal doğrulama araçlarında (SymbiYosys) derlenir; sentez ve simülasyon
onu görmez. **IP seç** (IP Library) ya da
**Dosyadan aç** (`.regspec.json`) ile kaynak seçilir; sağda register haritası özeti ile Verilog, C header ve
doküman önizlemesi vardır. Register haritasını değiştirmek için **Register haritasını düzenle** (ya da özette
bir registera çift tık) Register Map Editor'u açar; değişiklikler Wrapper'a anında yansır.

```
python -m axil_studio.core.axilgen my_ip.regspec.json -o out --project bench.axproj.json   # komut satırı
python -m axil_studio.core.axilgen my_ip.regspec.json -o out --base 0x40002000            # adresi vererek
```

VHDL dosyası üretilmez (ZipCPU kaynaklarının da VHDL karşılığı yok). **Verilog önizleme** sekmesinde *Dosya*
listesinden hangi dosyanın gösterileceği seçilir: IP (`<ip>.v`), HDL Designer wrapper'ı (`<ip>_pk.v`), örnekleme
şablonları ve testbench. Interconnect Configurator'da da aynı liste vardır: `<ad>.v`, `<ad>_pk.v` ve başvuru için
ZipCPU `axilxbar.v` (salt okunur).

**Alan tipleri**

| Tip | Port | Davranış |
|---|---|---|
| RW | `o_<reg>_<alan>` | Yazılır/okunur kontrol, reset değeri ayarlanır, WSTRB byte bazında |
| RO | `i_<reg>_<alan>` | Durum girişi, isteğe bağlı 2-FF senkronizör |
| PULSE | `o_<reg>_<alan>` | 1 yazınca 1 clock darbe, 0 okunur |
| W1C | `i_<reg>_<alan>` | Girişte 1 görünce set olur, 1 yazınca silinir (set öncelikli); isteğe bağlı `o_irq` |
| CONST | — | Sabit değer (ID/versiyon) |

Register seçenekleri: `o_<reg>_wr` / `o_<reg>_rd` strobe'ları (FIFO push/pop için).

**HDL Designer için paketli AXI wrapper'ı** (kutu işaretliyse): IP her zaman normal `S_AXI_*` portlu `<ip>.v` olarak üretilir; üstüne `<ip>_pk.v` HDL Designer bloğu (`SLAVE_AXI_REQ/RSP`, içinde normal IP) ve onun `_pk_inst.v` örnekleme şablonu eklenir. Hazır IP'ler için aynı blok **Hazır IP için HDL Designer wrapper'ı…** ile üretilir.

**Üretilen dosyalar:** `<ip>.v` (normal S_AXI_* portlu IP), `<ip>_pk.v` + `_pk_inst.v` (HDL Designer wrapper'ı, isteğe bağlı), `<ip>_tb.v` (kendini kontrol eden testbench),
`<ip>_inst.v`, `<ip>_regs.h`, `<ip>_regs.md`, `<ip>.regspec.json`, `<ip>.axproj.json`, `<ip>_regmap.xlsx`.

**Açıklamalar ve değer anlamları:** IP, register ve alan açıklamaları çok satırlı yazılabilir.
Her alana bir değer tablosu eklenebilir: her satırda değer, *okunursa anlamı* ve *yazılırsa etkisi* var.
Hangi sütunların kullanılacağı alan tipine bağlıdır: RO/CONST için sadece okuma, PULSE için sadece yazma,
RW/W1C için ikisi de. HDL ve C yorumlarına açıklamanın yalnızca ilk satırı yazılır.

**Excel kütük haritası** (Wrapper'da tek IP, Address Map Editor'da tüm adres haritası için `Excel'e aktar…`):
- *Özet*: IP açıklaması ve register listesi (haritaya bağlantılı)
- *Kütük Haritası*: register ve alan satırları, ayrılmış bitler, reset değerleri, davranış, okuma/yazma anlamları
- *Bit Görünümü*: 32 bitlik yerleşim, tipe göre renkli

**Notlar**
- Adres çözümü yalnızca alt bitlerle yapılır; interconnect'in tam adresi bağlanabilir. Register haritasında ve Wrapper'da base adres yoktur; adresler yalnızca Address Map Editor'da verilir (Wrapper'ın C header, testbench, Excel ve `.axproj.json` çıktıları IP'nin adres haritasındaki ilk adresini kullanır; komut satırında `--base` ile verilir, verilmezse IP adres haritasında sıradaki boş adrese yerleşir). Eski `.regspec.json` dosyalarındaki `base` okunurken yok sayılır. Excel'de Özet sayfasındaki sarı hücre değişince tüm adresler formülle güncellenir; header'daki `<IP>_BASE` `#ifndef` ile ezilebilir.
- `i_reset` ve `o_irq` port adları ayrılmıştır (easyaxil'in iç reset sinyali ve kesme çıkışı); bunları üretecek register / alan adları doğrulamada reddedilir (ör. `RESET` registerında `RESET` RO alanı).
- Libero: `C_AXI_ADDR_WIDTH` parametresini interconnect slave adres genişliğine eşitle; BIF tipi AXI4, slave slotu AXI4-Lite.
- Tüm registerlar `S_AXI_ARESETN` ile resetlenir (PolarFire `initial` bloğunu yok sayar).
- Testbench: `iverilog -g2012 -o sim <ip>_tb.v <ip>.v && vvp sim` → `... 0 errors: PASSED`.
- *Alan tipleri* register logic bölümünde easyaxil üslubuyla yazılır: RW register kelimesinde, PULSE bir clock'luk darbe kelimesinde, W1C bayrak kelimesinde (girişten set, 1 yazınca silinir, set öncelikli), `o_irq` irq işaretli W1C bayraklarının VEYA'sı.



## Address Map Editor

- Tablodaki her satır bir slave portudur; sıra port numarasıdır (SLAVE0, SLAVE1 …). Satırlar sürükle-bırak ya da
  ↑/↓ ile sıralanır, **Adrese göre sırala** listeyi bir kez adres sırasına dizer.
- Tablonun altındaki satırda **+ IP Kütüphanesinden slave ekle…**, **+ Harici slave ekle…**, Düzenle, Sil, sıralama
  düğmeleri bulunur. **+ IP Kütüphanesinden slave ekle…** IP Library'yi açar; aynı IP istenildiği kadar eklenebilir, örnekler IP adı
  ve 0'dan başlayan numarayla adlandırılır (`GPIO_0`, `GPIO_1` …; IP yeniden adlandırılırsa örnekler de izler).
  harici slave'ler `EXT_0` … adını alır. Ad hücresine çift tıklayarak örnek yeniden adlandırılır. **Sil**
  örneği haritadan çıkarır; IP tanımı projede kalır.
- **Adresler**: her örnek ya *otomatik* ya *sabit*tir. Otomatikler başlangıç adresinden itibaren liste sırasıyla
  ardışık ve boyutlarına hizalı adres alır; sabitlerin aralığını atlar. Adres hücresine yazılan değer sabitlenir
  (🔒), *Adresleme* hücresine tıklayınca otomatiğe döner.
- **Varsayılan pencere** (başlangıç adresinin altında): boyutu elle seçilmemiş her slave'in adres aralığı, 4 KB ile
  başlar. **Boyut** hücresinden elle seçilen boyut o slave'de kalır (🔒) ve varsayılan değişse de değişmez;
  listeden *varsayılan* seçilince yeniden bu ayarı izler. IP pencereden büyükse pencere kendiliğinden büyür.
- **Harici slave**: projede register haritası olmayan slave'ler (bellek, hazır IP) ad, adres ve kullanılan alanla girilir.
- **Excel'e aktar…** tüm adres haritasını register ayrıntılarıyla `.xlsx` olarak yazar.

## Interconnect Configurator

- Slave portları adres haritasından gelir (salt okunur liste; satıra çift tık Address Map Editor'u açar).
  Burada crossbar'ın kendisi ayarlanır: modül adı, port stili ve master'lar (**+ Master / Sil**, ada çift tık).
- **Blok diyagramı**: solda master'lar, ortada crossbar, sağda slave'ler; pinler üretilen HDL'deki ad, genişlik ve yönlerle çizilir, bloklarda kendi taraflarındaki port adları (M_AXI_* / S_AXI_*, MASTER_AXI_* / SLAVE_AXI_*) yazar. Fare tekerleği imleç etrafında yakınlaştırır, boş alanı sürüklemek kaydırır, trackpad'de iki parmakla sıkıştırma da çalışır.
- **Verilog önizleme** sekmesi üretilecek wrapper'ları gösterir. **Wrapper** seçimi: *Yalnızca ayrık AXI portları* (`<ad>.v`) ya da *Ayrık portlar + HDL Designer* (`<ad>.v` ve onu saran `<ad>_pk.v`).
- Ayarlar proje dosyasında (`.axproj.json` içindeki `interconnect`) saklanır. **Ayar dosyası aç** `.xbar.json`
  dosyasından modül adı, stil ve master'ları alır (slave'ler ve adresler adres haritasından gelir); **Ayar dosyası
  kaydet** slave'lerle birlikte tam ayarı yazar (komut satırı için).

## axilxbar wrapper

ZipCPU `axilxbar.v` (`hdl/axilxbar.v`) yapısına dokunulmadan kullanılır; yalnızca parametreleriyle
yapılandırılır: `NM`, `NS`, `SLAVE_ADDR`, `SLAVE_MASK`, `OPT_LOWPOWER`, `OPT_LINGER`, `LGMAXBURST`,
`C_AXI_DATA_WIDTH`, `C_AXI_ADDR_WIDTH`. wb2axip dosyalarında yalnızca iki tek satırlık, satırında
`AXIL Studio:` notuyla işaretli uyumluluk yaması vardır; mantık değişmez:

- `axilxbar.v`: parametre listesindeki `localparam AW` (yalnızca SystemVerilog'da geçerli) Verilog-2005 araçları
  için `parameter AW` yazılmıştır; `AW` dışarıdan verilmez.
- `addrdecode.v` (165. satır): `|prerequest[NS-1:1]` yerine eşdeğeri `|(prerequest >> 1)`. Tek slave'de (`NS=1`)
  çalışmayan bir dalda bile ters sıralı `[0:1]` dilimi oluşuyor ve iverilog elaborasyonda hata veriyordu.

axilxbar'ın portları düz vektörlerdir; üstüne iki wrapper üretilir, ikisi de mantık eklemez, yalnızca
sinyalleri adlandırır ve dilimler:

| Dosya | Portlar | Ne zaman |
|---|---|---|
| `<ad>.v` | Her port ayrı AXI4-Lite sinyalleri: `MASTER<i>_AXI_*` (master'ın `M_AXI_*`'ına), `SLAVE<j>_AXI_*` (slave'in `S_AXI_*`'ına) | Her zaman |
| `<ad>_pk.v` | HDL Designer için paketli: `MASTER<i>_AXI_REQ/RSP`, `SLAVE<j>_AXI_REQ/RSP`; içinde `<ad>.v` | *Ayrık portlar + HDL Designer* seçiliyse |

İki wrapper'da da slave başına `SLAVE<j>_ADDR` / `SLAVE<j>_MASK` generic'leri vardır ve axilxbar'ın
`SLAVE_ADDR` / `SLAVE_MASK` parametresine olduğu gibi geçer: `(adres & MASK) == ADDR` ise slave j seçilir.
Haritada olmayan adrese axilxbar kendisi DECERR döner. Port sayısı adres haritasından gelir; slave eklemek ya
da çıkarmak için Address Map Editor'da değiştirip yeniden üret. Generic'i HDL tarafında değiştirirsen projedeki
adres haritasını da güncelle.

```
python -m axil_studio.core.axilgen --xbar bench.axproj.json --nm 2 --xbar-name axil_xbar -o out   # 2 master
python -m axil_studio.core.axilgen --xbar out/axil_xbar.xbar.json -o out        # kayıtlı ayarlarla yeniden üret
python -m axil_studio.core.axilgen --xbar bench.axproj.json --xbar-style signals -o out   # _pk.v olmadan
```

HDL projesine `<ad>.v` (ve kullanılıyorsa `<ad>_pk.v`) ile `hdl/axilxbar.v`, `hdl/addrdecode.v`,
`hdl/skidbuffer.v` eklenir (wb2axip, Apache 2.0).


## Paketli AXI portu (REQ/RSP)

Blok diyagramda bir AXI4-Lite arayüzü 19 tel yerine 2 telle bağlanır:

| Vektör | Yön | İçerik |
|---|---|---|
| `*_AXI_REQ [110:0]` | master → slave | AWADDR, AWPROT, AWVALID, WDATA, WSTRB, WVALID, BREADY, ARADDR, ARPROT, ARVALID, RREADY |
| `*_AXI_RSP [40:0]` | slave → master | AWREADY, WREADY, BRESP, BVALID, ARREADY, RDATA, RRESP, RVALID |

Paketli bus her zaman 32 bit adres ve veri taşır; daha dar adresli çekirdekler wrapper içinde kesilir.

- Xbar'ın `_pk` wrapper'ında portlar `MASTER<i>_AXI_REQ/RSP` ve `SLAVE<j>_AXI_REQ/RSP` olur.
- Paketli IP'lerin üst modülü `SLAVE_AXI_REQ/RSP` portludur.
- Hazır modüller için: Slave Interface Wrapper'da **Hazır IP için HDL Designer wrapper'ı…** ya da `python -m axil_studio.core.axilgen --pack axilgpio.v -o out`.
  ANSI başlıklı her modülde çalışır; `S_AXI_*` grubu `SLAVE_AXI_REQ/RSP`, `M_AXI_*` grubu `MASTER_AXI_REQ/RSP` olur, diğer portlar ve parametreler aynen geçer.
- `hdl/` içinde `axilgpio_pk.v`, `axiluart_phy_pk.v`, `hexbus_axil_top_pk.v` hazır.

HDL Designer'da: hexbus `MASTER_AXI_REQ/RSP` ↔ xbar `_pk` `MASTER0_AXI_REQ/RSP`; xbar `SLAVE0_AXI_REQ/RSP` ↔ IP `SLAVE_AXI_REQ/RSP`.


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

**Her commit programın sürümünü 0.0.1 artırır** (`axil_studio/__init__.py` → `APP_VERSION`). Bunu
`.githooks/pre-commit` kendiliğinden yapar; depoyu klonladıktan sonra bir kez etkinleştir:

```
git config core.hooksPath .githooks
```

Sürüm elle değiştirilmiş bir commit'te (ör. yeni özellik için 0.2.0) hook dokunmaz. Elle artırmak için:
`python scripts/bump_version.py`.

## Dosya düzeni

| Yer | İçerik |
|---|---|
| `studio.py` | Başlatıcı (`python studio.py`) |
| `axil_studio/` | Hub ve ortak arayüz parçaları (PySide6): `hub.py`, `ui.py`, `dialogs.py`, `theme.py`, `icons.py`, `project.py` |
| `axil_studio/tools/` | Araçlar: `regmap.py`, `wrapper.py`, `address_map.py`, `interconnect.py` (+ `xbar_diagram.py`), `debug_bridge.py`, `ip_library.py` |
| `axil_studio/core/` | Arayüzden bağımsız çekirdek (Qt yok), aşağıda |
| `scripts/` | Komut satırı araçları: `uart_validate.py` (+ `uart_validate_sim.py`) |
| `hdl/` | wb2axip dosyaları (`axilxbar.v`, `addrdecode.v`, `skidbuffer.v`, slave şablonu `easyaxil.v`) ve paketli `_pk` örnekleri |
| `examples/` | Örnek GPIO projesi ve config dosyaları |
| `.githooks/` | Commit başına sürüm artıran git hook'u (bkz. Sürümler) |

`axil_studio/core/`:

| Modül | İçerik |
|---|---|
| `model.py` | Proje / IP / register / bit alanı modeli, adres haritası örnekleri, JSON kaydetme |
| `addrmap.py` | Adres haritası: otomatik adresleme, pencereler, doğrulama, interconnect slave'leri |
| `library.py` | IP kütüphanesi dosyaları (`.axlib.json`) |
| `hexbus.py` | Hexbus protokol sürücüsü + `SIMULATOR` portu (GPIO0 @0x60000000, loopback UART0 @0x60001000) |
| `axilgen.py` | AXI4-Lite wrapper üretici (komut satırı: `python -m axil_studio.core.axilgen`) |
| `axilpack.py` | Paketli AXI portu wrapper'ı (`_pk`, REQ/RSP) |
| `xbargen.py` | axilxbar wrapper üretici (isimli portlar) |
| `regmap_xlsx.py` | Kütük haritası Excel çıktısı |
| `numfmt.py` | Bit alanı veri tipleri ve gösterimleri |
| `portparse.py` | Verilog / SystemVerilog / VHDL port listesi çözümleyici (Port içe aktar) |

Hexbus notları: her işlem `A<adres|1>W<veri>\n` veya `A<adres|1>R\n` olarak gönderilir (adres artırma kapalı);
yanıtlar `A` adres, `K` yazma onayı, `R` okuma verisi, `E` AXI bus hatası, `Z` idle. Hexbus'ta FIFO yoktur;
her isteğin yanıtı beklenerek sıralı çalışılır.

## Donanım doğrulama (`scripts/uart_validate.py`)
```
python scripts/uart_validate.py --hexbus COM5 --line COM7 --line-type rs485-4w --lead-us 10
python scripts/uart_validate.py --config examples/bench_rs485_4w.json          # ayarlar dosyadan
python scripts/uart_validate.py --config bench.json --bauds 460800,921600      # komut satırı dosyayı ezer
python scripts/uart_validate.py --hexbus COM5 --line COM7 --save-config bench.json   # ayarları kaydet
python scripts/uart_validate.py --simulate                                     # donanımsız deneme
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
