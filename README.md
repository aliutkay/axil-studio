# AXIL Studio

AXI4-Lite slave IP'leri için register haritası tasarımı, HDL üretimi, adres haritası, interconnect ve
donanım üzerinde test araçlarını tek çatı altında toplayan hub (PySide6/Qt, Windows ve macOS'ta aynı
görünüm, açık/koyu tema):

| Araç | Ne yapar |
|---|---|
| **Register Map Editor** | AXI4-Lite IP'ler için register haritası tasarımı: registerlar, bit alanları, erişim türleri, reset değerleri ve değer kodlamaları; Verilog / VHDL'den port aktarma ve Excel'e aktarma |
| **Slave Interface Wrapper** | Register haritasından AXI4-Lite slave üretimi: sentezlenebilir Verilog, kendini denetleyen testbench ve register dokümanı; hazır modüller için HDL Designer wrapper'ları |
| **Address Map Editor** | Sistem adres haritası: bus hiyerarşisinde IP örnekleri, harici slave'ler ve alt sistemler; otomatik / sabit taban adres, pencere boyutlandırma, hizalama ve çakışma denetimi, Excel'e aktarma |
| **Interconnect Configurator** | Adres haritasındaki her bus için crossbar yapılandırması (master'lar, modül adları, port stili), sistem ve crossbar blok diyagramları, ZipCPU axilxbar wrapper'ları |
| **UART-to-AXI Debug Bridge** | UART hexbus köprüsü üzerinden donanımda register erişimi: çözümlenmiş register görünümleri, periyodik okuma, yerleşik simülatör; kendi arayüzü olan IP'ler için **IP arayüzü** (ör. axiluart_phy'nin UART arayüzü) |
| **IP Library** | Proje ve paylaşılan IP kütüphaneleri için IP yönetimi: sürümlü IP'ler, adres haritasındaki örnekleri, projeler ile kütüphaneler arasında aktarım |

Ana pencere bir başlatıcıdır; her araç kendi penceresinde açılır (Ctrl+1 … Ctrl+6). Araç pencerelerinin üstünde tek
bir bar vardır: solda ikon ve ad (sürüm pencere başlığında: *AXIL Studio Register Map Editor early-alpha … | proje*),
hemen yanında aracın komutları (sığmayanlar » menüsüne geçer), sağda durum
ve ana düğme (**Kaydet** mavi kalır). Uzun açıklamalar panellerde paragraf olarak değil, başlıkların yanındaki **?**
düğmesinde durur (üzerine gelince ya da tıklayınca görünür). İkincil düğmelerin ince bir çerçevesi vardır, devre dışı
olanlar kesikli çerçeveyle soluk görünür. Sekme köşesindeki **Ayrı pencere** düğmesi çerçevesizdir. Kartlar ve sekme sayfaları beyazdır; tüm listelerde satırlar aynı iki renkle
dönüşümlü boyanır, listenin zemini satırların açık tonudur. Listedeki her satırın sonunda silik bir kalem (düzenle) ve çöp
kutusu (sil) ikonu vardır; üzerine gelince belirginleşir. IP Library, Proje kartındaki
**IP'leri yönet** düğmesiyle açılır. Doğrulama sonuçları her aracın penceresinin alt çubuğunda gösterilir (yeşil: hata
yok; kırmızı: hata sayısı ve ilki — üzerine gelince hepsi, tıklayınca liste). Araçlar aynı projeyi
(`.axproj.json`) paylaşır ve birbirini açar:

- Register haritası nerede düzenlenirse düzenlensin (Wrapper, Address Map Editor, IP Library, Debug Bridge)
  **Register Map Editor** açılır.
- Adres nerede düzenlenirse düzenlensin (Interconnect, IP Library, Debug Bridge) **Address Map Editor** açılır.
- IP seçilen / eklenen her yerde (Address Map Editor *IP ekle*, Register Map Editor *IP aç*, Wrapper *IP seç*)
  **IP Library** seçici olarak açılır; kütüphanedeki bir IP seçilirse önce projeye alınır.

Arayüz varsayılan olarak **İngilizce**dir; **View → Language** menüsünden **Türkçe** seçilebilir (bkz.
[Arayüz dili ve çeviriler](#arayüz-dili-ve-çeviriler)). Bu belgede düğme ve menü adları Türkçe arayüzdeki
karşılıklarıyla yazılmıştır.

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
  numarasıdır. **Alt sistem** (Subsystem) kaydı (`"kind": "bridge"`, dosya biçimi değişmedi) kendi penceresini
  (`self_window`), crossbar ayarlarını (`xbar`) ve içindeki slave'leri (`children`, aynı biçimde, iç içe) tutar.

Bir IP'nin register haritası değişince tüm örnekleri onu kullanır; IP büyürse pencereleri ve otomatik
adresler kendiliğinden güncellenir. IP yeniden adlandırılırsa örnekleri onu izler. Eski (sürüm 1) proje
dosyaları açılırken IP'ler ve interconnect slave listesi otomatik olarak IP + adres haritasına çevrilir.

## Register Map Editor

- Solda **IP** paneli (ad, sürüm, açıklama — açıklama *Düzenle…* ile ayrı pencerede yazılır; **IP arayüzü**: IP'nin
  Debug Bridge'den açılan kendi Python arayüzü, ör. *UART arayüzü*) ve **Registerlar**
  paneli (arama kutusu: register ya da bit alanı adı / açıklaması; bit alanında bulunanlar register satırında
  gösterilir ve alan listesinde vurgulanır, Ctrl+K). Listelerin sonunda bırakma için boş alan vardır.
- Sağda 32 bitlik alan haritası (boş bite çift tık yeni alan
  açar), bit alanları ve değer açıklamaları (yalnızca *Değer açıklamaları* sütununa tıklayınca alt tablo olarak açılır).
- Her alanın bir **veri tipi** vardır (`axil_studio/core/numfmt.py`): hex, unsigned, signed (2's complement), binary, octal,
  bool, ASCII, unsigned/signed fixed point (Q, kesir biti seçilir) ya da enum (değer anlamı). Reset ve değerler
  bu tipte gösterilir/girilir; Debug Bridge alanı varsayılan olarak bu tipte gösterir.
- **Kaydet** (Ctrl+Shift+S) register haritasını son kaydedildiği yere yazar: projeye, bir IP kütüphanesine ya da bir
  `.regspec.json` dosyasına (üst bardaki etiket yeri gösterir, ● kaydedilmemiş değişiklik). İlk kayıtta ya da
  **Farklı kaydet…** ile yer seçilir: *Proje* sekmesi projedeki tüm IP'leri, *Kütüphane* sekmesi kütüphaneleri, içlerindeki
  IP'leri ve sürümleri, *Dosya* sekmesi dosya yolunu gösterir; ne olacağı (yeni / güncelleme / üzerine yazma) yazılır ve
  seçilen yer sonraki Kaydet'lerde kullanılır. Kütüphaneden açılan IP'de Kaydet o kütüphaneye yazar. **İçe aktar…** bir
  `.regspec.json` açar. (Eski *Projeye kaydet* / *Kütüphaneye kaydet* / *Dosyaya kaydet* düğmeleri bunlarla değişti.)
- **IP aç** IP Library'den bir IP açar.
- **Wrapper'da aç**: HDL üretimi için Slave Interface Wrapper'ı açar. IP adres haritasına Address Map Editor'da
  (ya da IP Library'de) yerleştirilir.
- **Excel'e aktar…** register haritasını `.xlsx` olarak yazar (IP projede yerleşmişse ilk adresi, değilse 0 base
  olarak; Özet sayfasındaki sarı hücreden değiştirilebilir).
- Sağda üstte seçili registerın kartı (bit haritası ve bit alanları), altında ayrı **Yerleştirilmemiş bit alanları**
  kartı ve **Port içe aktar…** düğmesi (Ctrl+I). Sol panelde yalnızca registerlar listelenir. Registerlar, bit alanları,
  değer açıklamaları ve yerleştirilmemiş alanlar satırdaki kalem / çöp ikonlarıyla düzenlenir ve silinir.
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
  yeni registerlara girer; bit alanı listesine de bırakılabilir. Düğmeler ne yapacaklarını yazar (ör. *5 bit alanını CTRL
  registerındaki boş bitlere yerleştir*, *5 bit alanını yeni registera yerleştir*). Yeni registera yerleştirmede bir
  registera sığmayanlar için sırayla yenileri açılır; tek alanlı register alanın adını alır (32 bitlik bir alan kendi
  adlı registerına), diğerleri `REG<n>` olur. Satırdaki çöp ikonu ya da Delete istenmeyenleri siler.
- Yerleşmiş bir alan bit haritasından ya da bit alanı listesinden **sürüklenir**: bit haritasında başka bitlere
  (önizleme yeşilse yer boştur, kırmızıysa bırakılmaz), register listesinde başka bir registera (yer yoksa yerinde
  kalır) ya da listenin boş alanına (yeni register), yerleştirilmemiş kartına (registerdan çıkar). Sağ tık →
  *Yerleştirilmemişlere geri al* da aynısını yapar.
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
**Dosyadan aç** (`.regspec.json`) ile kaynak seçilir; sağda register haritası özeti ile Verilog ve
doküman önizlemesi vardır (C header adres haritasından üretilir: Address Map Editor → *C header…*). Register haritasını değiştirmek için **Register haritasını düzenle** (ya da özette
bir registera çift tık) Register Map Editor'u açar; değişiklikler Wrapper'a anında yansır.

```
python -m axil_studio.core.axilgen my_ip.regspec.json -o out --project bench.axproj.json   # komut satırı
python -m axil_studio.core.axilgen my_ip.regspec.json -o out --base 0x40002000            # adresi vererek
python -m axil_studio.core.axilgen --dump-spec my_ip.regspec.json --base 0x40002000      # girdiyi göster, yazma
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
`<ip>_inst.v`, `<ip>_regs.md`, `<ip>.hdlspec.json` (Verilog dosyalarının tek girdisi),
`<ip>.regspec.json` (register haritasının tamamı, yeniden açmak için). Excel register haritası Register Map Editor'dan
(ya da tüm harita için Address Map Editor'dan) aktarılır; komut satırı `axilgen` ayrıca `<ip>_regmap.xlsx` yazar.

**Spec sekmesi** (salt okunur): IP'nin **HDL spec**'ini gösterir, yani Verilog'u üreten koda giden verinin tamamını
ve yalnızca onu: modül adı, HDL Designer wrapper'ı, registerlar (ad, offset, strobe'lar) ve bit alanları (ad, konum,
genişlik, tür, reset, irq, sync). Açıklamalar, değer anlamları, gösterim biçimleri, sürüm ve adres koda etki etmediği
için içinde yoktur (bunlar doküman, Excel ve adres haritasının C header'ına gider). **Kopyala** ve **Kaydet (.hdlspec.json)** vardır.
Register Map Editor'da üst bardaki **Spec** aynı görünümü ayrı bir pencerede açar, düzenledikçe güncellenir.
Bkz. [İki parçalı yapı ve testler](#iki-parçalı-yapı-ve-testler).

**Açıklamalar ve değer anlamları:** IP, register ve alan açıklamaları çok satırlı yazılabilir.
Her alana bir değer tablosu eklenebilir: her satırda değer, *okunursa anlamı* ve *yazılırsa etkisi* var.
Hangi sütunların kullanılacağı alan tipine bağlıdır: RO/CONST için sadece okuma, PULSE için sadece yazma,
RW/W1C için ikisi de. HDL ve C yorumlarına açıklamanın yalnızca ilk satırı yazılır.

**Excel kütük haritası** (Register Map Editor'da tek IP, Address Map Editor'da tüm adres haritası için `Excel'e aktar…`):
- *Özet*: IP açıklaması ve register listesi (haritaya bağlantılı)
- *Kütük Haritası*: register ve alan satırları, ayrılmış bitler, reset değerleri, davranış, okuma/yazma anlamları
- *Bit Görünümü*: 32 bitlik yerleşim, tipe göre renkli

**Notlar**
- Adres çözümü yalnızca alt bitlerle yapılır; interconnect'in tam adresi bağlanabilir. Register haritasında ve Wrapper'da base adres yoktur; adresler yalnızca Address Map Editor'da verilir (Wrapper'ın testbench çıktısı ile Excel IP'nin adres haritasındaki ilk adresini kullanır, yerleşmemişse 0; komut satırında `--base` ile verilir, varsayılan 0). Eski `.regspec.json` dosyalarındaki `base` okunurken yok sayılır. Excel'de Özet sayfasındaki sarı hücre değişince tüm adresler formülle güncellenir.
- `i_reset` ve `o_irq` port adları ayrılmıştır (easyaxil'in iç reset sinyali ve kesme çıkışı); bunları üretecek register / alan adları doğrulamada reddedilir (ör. `RESET` registerında `RESET` RO alanı).
- Libero: `C_AXI_ADDR_WIDTH` parametresini interconnect slave adres genişliğine eşitle; BIF tipi AXI4, slave slotu AXI4-Lite.
- Tüm registerlar `S_AXI_ARESETN` ile resetlenir (PolarFire `initial` bloğunu yok sayar).
- Testbench: `iverilog -g2012 -o sim <ip>_tb.v <ip>.v && vvp sim` → `... 0 errors: PASSED`.
- *Alan tipleri* register logic bölümünde easyaxil üslubuyla yazılır: RW register kelimesinde, PULSE bir clock'luk darbe kelimesinde, W1C bayrak kelimesinde (girişten set, 1 yazınca silinir, set öncelikli), `o_irq` irq işaretli W1C bayraklarının VEYA'sı.



## Address Map Editor

- Adres haritası bir **ağaçtır**: üst seviyedeki her satır üst interconnect'in bir slave portudur, sıra port
  numarasıdır (SLAVE0, SLAVE1 …). Bir **alt sistemin** (Subsystem) altındaki satırlar onun slave'leridir (her bus
  kendi SLAVE0'ından başlar). Derinlik sınırsızdır.
- Tablonun altında **+ IP Kütüphanesinden slave ekle…**, **+ Harici slave ekle…**, **+ Alt sistem ekle…** ve
  sıralama düğmeleri bulunur. Ekleme seçili alt sistemin içine (seçili satır bir alt sistemin içindeyse aynı bus'a),
  seçim yoksa üst bus'a yapılır. Her satırın sonundaki kalem ad / adres / boyutu düzenler, çöp kutusu satırı çıkarır
  (alt sistem içindekilerle birlikte; IP tanımları projede kalır). Aynı IP istenildiği kadar eklenebilir; örnekler IP adı
  ve 0'dan başlayan numarayla adlandırılır (`GPIO_0`, `GPIO_1` …; IP yeniden adlandırılırsa örnekler de izler),
  harici slave'ler `EXT_0` …, alt sistemler `SUB_0` … adını alır. Ad hücresine çift tıklayarak yeniden adlandırılır.
- **Taşıma**: satırlar sürükle-bırak ile bus içinde sıralanır ya da başka bir bus'a taşınır (bir alt sistem
  satırının üstüne bırakılan satır onun içine girer); sağ tık → **Taşı** da aynı işi yapar, ↑/↓ bus içinde kaydırır.
  Başka bir bus'a taşınan satırın sabit adresi otomatiğe döner; taşınan bir alt sistem içindekilerle gider, içindeki
  sabit adresler alt sisteme göre yerlerini korur. Bir alt sistem kendi içine taşınamaz.
- **Adresler globaldir**: her interconnect adresin tamamını çözer, alt sistem adresi değiştirmeden iletir; bir
  IP'nin adresi her yerde (C header, Debug Bridge, dokümanlar) aynıdır. Her satır ya *otomatik* ya *sabit*tir.
  Otomatikler, üst bus'ta başlangıç adresinden, bir alt sistemin içinde onun kendi penceresinden sonra liste
  sırasıyla ardışık ve boyutlarına hizalı adres alır; sabitlerin aralığını atlar. Adres hücresine yazılan değer
  kilitlenir; adresin yanındaki **kilit düğmesi** adresi kilitler (sabit) ya da otomatiğe döndürür. Bir alt sistemin
  içindekiler her zaman onun penceresinin iletilen kısmında olmalıdır (değilse hata).
- **Alt sistem**: slave'leri bus'ındaki tek bir pencerede toplar; penceresinin başı isteğe bağlı olarak alt sistemin
  kendisine ayrılır (**kendi penceresi**: yok, 256 B … 256 MB; register haritası bağlanmaz), geri kalanı içindekilere
  iletilir. **Birden fazla slave'i (ya da ek master'ı) varsa kendi crossbar'ı üretilir**: üst bus'taki SLAVE portu
  bu crossbar'ın MASTER0'ına bağlanır. **Tek slave'i varsa crossbar üretilmez**: üst bus'taki SLAVE portu doğrudan o
  slave'e bağlanır (Type / IP sütununda *Alt sistem · doğrudan* yazar). İkinci bir slave ya da ek master eklenince
  crossbar kendiliğinden oluşur. **Pencere** *otomatik* ise kendi penceresini ve içindekileri kapsayan en küçük 2'nin
  kuvvetidir; içerik büyüdükçe büyür ve ondan sonraki otomatik adresler yeniden dizilir. Elle seçilen (kilitli) pencere
  sabit kalır; sığmayan bir ekleme, taşıma ya da boyut değişikliği yapılmaz ve hata gösterilir. Tek slave'li alt
  sistemde adres çözülmediği için penceresinin slave'in penceresinden büyük olan kısmı da o slave'e gider.
- **Alt sistem türü**: *Slave grubu* (yukarıdaki gibi) ya da **Komut sıralayıcı (cmd_seq)**. Her alt sistem komut
  sıralayıcı değildir; tür alt sistem eklenirken ya da düzenlenirken seçilir. Komut sıralayıcı alt sistemde
  üst bus'taki SLAVE portu ile alt sistemin MASTER0'ı (ya da tek slave'i) arasında `hdl/cmd_seq` bloğu durur:
  pencerenin ilk **64 KB**'ı (kilitli kendi penceresi) cmd_seq'in registerlarıdır (CTRL, STATUS, R0–R15, D-mem),
  geri kalanı değişmeden içindekilere iletilir. Ayarları: I-mem / D-mem adres bitleri, pass-through ve sıralayıcı
  zaman aşımları. `SELF_ID` tabanın [23:16] bitlerinden gelir; kurallar: taban 64 KB'ye hizalı, pencere en fazla
  16 MB, `DMEM_AW` ≤ 13. cmd_seq'in registerları alt sistemin adıyla Debug Bridge'de, C header'da ve Excel'de görünür;
  Debug Bridge'den **Komut sıralayıcı** arayüzü açılır. cmd_seq IP kütüphanesine slave olarak eklenmez (hata verir).
  Örnek proje: `examples/cmd_seq/cmd_seq_demo.axproj.json`.
- Üstteki şerit haritayı adres sırasıyla çizer; alt sistemler çerçeveli blok olarak, kendi penceresi taralı, içindekiler
  içinde gösterilir. Renkler blok diyagramıyla aynıdır: IP mavi, harici slave gri, alt sistem şeftali.
- **Varsayılan pencere** (başlangıç adresinin altında): boyutu elle seçilmemiş her slave'in adres aralığı, 4 KB ile
  başlar. **Boyut** hücresinden seçilen boyut ya da boyutun yanındaki **kilit düğmesi** o slave'in
  boyutunu kilitler, varsayılan değişse de değişmez; kilit açılınca (ya da *varsayılan* seçilince) yeniden bu ayarı
  izler. Tablo başlığında *Adres* ve *Boyut* yazılarının önündeki kilit ikonları tüm adres / tüm boyut
  kilitlerini birden kapatır ya da açar (üzerine gelince açıklar). Satırlardaki kilitler kendi sütunlarında sağa,
  değerin hemen yanına yaslıdır; başlıktaki ikon onlarla aynı hizadadır. IP pencereden büyükse pencere kendiliğinden büyür.
- **Harici slave**: projede register haritası olmayan slave'ler (bellek, hazır IP) ad, adres ve kullanılan alanla girilir.
- **Excel'e aktar…** tüm adres haritasını register ayrıntılarıyla `.xlsx` olarak yazar (alt sistem içindeki IP'lerin
  bus'ı Özet sayfasında yazar).

- **C header…** adres haritasının C header'ını yazar (`<proje>_map.h`): her IP tanımı için bir kez register
  offset'leri, bit alanlarının shift / mask tanımları ve IP sürümü; her örnek için `<ÖRNEK>_BASE`, `<ÖRNEK>_SIZE` ve
  register adresleri (`UART0_PHY_CTRL = UART0_BASE + AXILUART_PHY_PHY_CTRL_OFFSET`); harici slave'ler ve alt sistemler
  için taban ve pencere. Register haritasının kendi adresi olmadığı için (bir IP birden çok yerde olabilir) header
  Wrapper'da değil burada üretilir. Komut satırı: `python -m axil_studio.core.cheader proje.axproj.json -o map.h`.
- **Assembler include…** aynı adları cmd_seq assembler'ı için `.equ` satırları olarak yazar (`<proje>_map.inc`;
  register adresleri mutlak, bit alanları `_SHIFT` / `_MASK`). Programda `.include "<proje>_map.inc"`. Komut
  sıralayıcı arayüzü bu adları dosyaya gerek kalmadan doğrudan projeden alır.

## Interconnect Configurator

- Crossbar gereken her bus bir interconnect'tir: üst bus ve birden fazla slave'i ya da ek master'ı olan her alt
  sistem. Solda **Interconnect'ler** listesi hiyerarşiyi gösterir; tek slave'li alt sistemler listede soluk ve
  *(doğrudan)* olarak görünür, seçilince crossbar üretilmediği ve slave'in doğrudan bağlandığı yazar (modül adı
  kapalıdır, blok diyagramı tüm sistemi gösterir). Seçilen interconnect'in **modül adı** (alt sistemlerde varsayılan
  `axil_xbar_<alt sistem>`) ve **master'ları** ayarlanır. Bir alt sistemin crossbar'ında MASTER0 her zaman üst bus'tan
  gelen isteklerdir; ek master'lar (ör. yerel DMA) eklenebilir ve yalnızca o crossbar'ın slave'lerine erişir (tek
  slave'li alt sisteme master eklemek ona crossbar kazandırır). **Wrapper** stili tüm interconnect'ler için ortaktır.
- **Komut sıralayıcı alt sistemlerde** MASTER0'ı cmd_seq bloğu sürer (üst bus'tan gelen istekler cmd_seq'ten
  geçer). Üretilen dosyalara her biri için parametreleri doldurulmuş bir cmd_seq instance şablonu eklenir
  (`<alt sistem>_cmd_seq_inst.v`: `SELF_ID`, `IMEM_AW`, `DMEM_AW`, zaman aşımları; S_AXI üst bus'taki SLAVE portuna,
  M_AXI alt sistemin MASTER0'ına ya da tek slave'ine). RTL `hdl/cmd_seq/rtl`, HDL Designer için `hdl/cmd_seq_pk.v`.
- Slave portları adres haritasından gelir (salt okunur liste; satıra çift tık Address Map Editor'u açar, alt sistem
  satırına çift tık o alt sistemi seçer). Master'larda satırdaki kalem adı değiştirir, çöp kutusu siler;
  ada çift tık da olur.
- **Blok diyagramı**: *Tüm sistem* görünümü bütün hiyerarşiyi soldan sağa çizer: master'lar → interconnect →
  slave'ler; alt sistem ayrı bir bloktur (üst bus'ın slave'i; kendi crossbar'ının MASTER0'ı ya da tek slave'ine
  doğrudan bağlı), ek master'lar onun altında durur. Bir interconnect'e ya da master'a tıklamak onu seçer, alt
  sisteme çift tık onu seçer. *Seçili interconnect (pinlerle)* görünümü tek crossbar'ı üretilen HDL'deki pin adları,
  genişlikleri ve yönleriyle çizer. Bloklar türüne göre pastel renklidir (master yeşil, interconnect lila, IP mavi,
  harici slave gri, alt sistem şeftali), zemin beyazdır. **Gösterim** seçimi bağlantıların ayrıntısını belirler: *Master / slave*
  (bağlantı başına tek ok, varsayılan), *AXI Lite (Basitleştirilmiş Görünüm)* (tüm sistemde AW / W / B / AR / R
  kanalları, tek crossbar'da yöne göre gruplu sinyaller), *AXI Lite (Detaylı Görünüm)* (tüm pinler) ve *HDL
  Designer (REQ / RSP)*; üretilen dosyaları değiştirmez. **PDF…** ve **draw.io…** gösterilen diyagramı o anki görünüm
  ve gösterimle, açık renklerde ve beyaz zeminde kaydeder; draw.io dosyasında her blok, yazı ve bağlantı ayrı,
  düzenlenebilir bir şekildir. Fare tekerleği imleç etrafında yakınlaştırır, boş alanı sürüklemek kaydırır,
  trackpad'de iki parmakla sıkıştırma da çalışır.
- **Spec** sekmesi (salt okunur) seçili interconnect'in ya da hepsinin **HDL spec**'ini gösterir: wrapper Verilog'una giden verinin tamamı ve yalnızca o: modül adı, master sayısı, her slave'in adresi ve maskesi, HDL Designer wrapper'ı, axilxbar parametreleri (`OPT_LOWPOWER`, `OPT_LINGER`, `LGMAXBURST`). Port adları koda etki etmediği için yoktur. **Kopyala**, **Kaydet (.hdlspec.json)** (hepsi seçiliyse her biri ayrı dosya).
- **Verilog önizleme** sekmesi her interconnect'in üretilecek wrapper'larını gösterir. **Wrapper** seçimi:
  *Yalnızca ayrık AXI portları* (`<ad>.v`) ya da *Ayrık portlar + HDL Designer* (`<ad>.v` ve onu saran `<ad>_pk.v`).
- **Üret…** her crossbar için ayrı dosyalar yazar (`axil_xbar.v`, `axil_xbar_SUB_0.v` …, `.md`, `.hdlspec.json`);
  tek slave'li alt sistemler ve sistem bağlantısı için dosya üretilmez.
- Üst interconnect'in ayarları proje dosyasında (`.axproj.json` içindeki `interconnect`), bir alt sistemin crossbar
  ayarları alt sistemin kaydında (`xbar`) saklanır. **İçe aktar** `.xbar.json` dosyasından seçili interconnect'in modül
  adını, stilini ve master'larını alır (slave'ler ve adresler adres haritasından gelir); **Dışa aktar** seçili
  interconnect'in tam ayarını yazar (komut satırı için).

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

Komut satırı arayüzün **Üret…** düğmesiyle aynı dosyaları üretir (arayüz olmadan doğrulamak için): projedeki
her crossbar (üst bus ve birden fazla slave'i ya da ek master'ı olan her alt sistem), projedeki modül adları, master'lar ve port stiliyle;
önce adres haritası ve interconnect'ler denetlenir, hata varsa hiçbir dosya yazılmaz. `--xbar-name`, `--nm` ve
`--xbar-style` yalnızca verildiklerinde projedeki ayarın yerine geçer (`--xbar-name`, `--nm`: üst interconnect).

```
python -m axil_studio.core.axilgen --xbar bench.axproj.json -o out             # tüm interconnect'ler, proje ayarlarıyla
python -m axil_studio.core.axilgen --xbar bench.axproj.json --nm 2 -o out      # üst interconnect'te 2 master
python -m axil_studio.core.axilgen --xbar out/axil_xbar.xbar.json -o out        # tek interconnect, kayıtlı ayarlarla
python -m axil_studio.core.axilgen --xbar bench.axproj.json --xbar-style signals -o out   # _pk.v olmadan
```

Modül adları tüm tasarımda tek olmalı: interconnect'ler, IP'ler ve `_pk` wrapper'ları aynı adı alamaz, ZipCPU
modüllerinin adları (`axilxbar`, `addrdecode`, `skidbuffer`, `easyaxil`) kullanılamaz.

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
- `hdl/` içinde `axilgpio_pk.v`, `axiluart_phy_pk.v`, `hexbus_axil_top_pk.v` hazır; `axilgpio.v` çekirdeğin kendisi (ZipCPU, Apache-2.0), `axilgpio_a32.v` onun 32 bit adresli sarmalayıcısı (Libero bus interface gibi port genişliklerinin eşit olmasını isteyen araçlar için).
- `examples/cmd_seq_led_fade/`: komut sıralayıcının axilgpio'daki iki LED'i PWM ile yumuşak geçişle sürdüğü örnek (program, axilgpio kütüphanesi, proje, RTL simülasyonu).

HDL Designer'da: hexbus `MASTER_AXI_REQ/RSP` ↔ xbar `_pk` `MASTER0_AXI_REQ/RSP`; xbar `SLAVE0_AXI_REQ/RSP` ↔ IP `SLAVE_AXI_REQ/RSP`.


## UART-to-AXI Debug Bridge

- Adres haritası Address Map Editor'dan gelir: aynı IP birden çok kez yerleştirildiyse her örnek kendi adı ve adresiyle ayrı listelenir. Projede olmayan IP'ler yalnızca hata ayıklama için **Ek IP** ile bir `.regspec.json`, başka bir `.axproj.json` ya da IP kütüphanesinden eklenir (projede `debug` altında saklanır, adres haritasını ve interconnect'i etkilemez). **Ham adres erişimi** ile haritası olmayan herhangi bir adres okunup yazılır.
- Her bit alanının "Yazılacak" girişi tipine göre seçilir: tanımlı yazma değerleri varsa liste, bool / 1 bit için anahtar, diğerleri için alanın veri tipinde (uint, int, hex, binary, fixed point, ASCII …) sayı kutusu. **PULSE** alanları için *Darbe gönder*, **W1C** bayrakları için *Temizle* düğmesi anında yazar; RO / CONST salt okunurdur.
- Yazma read-modify-write'tır: okunan değer temel alınır, PULSE ve W1C bitleri 0 yazılır; salt yazılır registerlarda son yazılan değer temel alınır.
- Her bit alanının **⋯** menüsü (ya da sağ tık): **Özel değer yaz…** istenen veri tipinde değeri yazılacağa ekler ya da hemen yazar, **Register Map Editor'da düzenle…** IP'nin register haritasını o bit alanı seçili olarak açar. Ağaçta sağ tık: *Register haritasını düzenle* (Register Map Editor), *Adresini düzenle* (Address Map Editor). Register haritası ve proje adresleri Debug Bridge'de değiştirilmez.
- Sağ tarafta üstte sekmeler: **Register**, **Canlı izleme**, **Config**; **Trafik** altta, yüksekliği ayarlanabilir. Hangi ekrandan okunursa okunsun (register paneli, canlı izleme, config) son okunan değer ve saati her ekranda gösterilir.
- **Canlı izleme**: İzle düğmesi ya da ağaçta sağ tık ile eklenen registerlar seçilen periyotta (ms) okunur; değişen değerler vurgulanır. Register satırına tıklayınca bit alanları alt satırlarda değer ve anlamlarıyla açılır. Seçili registerın **geçmişi** (milisaniye zaman damgalı her okuma ya da yalnızca değişimler, değişen bit alanlarıyla) altta listelenir; tüm kayıt **CSV'ye aktar** ile dışa alınır.
- **Config**: Config'e ekle ile register panelindeki yazılacak değer listeye eklenir; satıra çift tıklayınca bit alanlarıyla düzenlenip güncellenir. Satıra tıklayınca bit alanları açılır; *Cihazda* sütunu son okunan değeri gösterir, config'ten farklı alanlar kırmızıdır. Sıralama, Cihazdan oku, Karşılaştır, Cihaza gönder (sırayla yazar ve geri okur), `.axcfg.json` aç / kaydet.
- **IP arayüzü:** register haritası bir arayüz kimliği taşıyan IP'ler (Register Map Editor → *IP arayüzü*; projede
  ve kütüphanede IP ile birlikte saklanır) ağaçta simgeyle görünür. IP seçilince register panelinin üstünde arayüzün
  düğmesi çıkar; IP'ye çift tık ya da sağ tık da açar. Arayüz **ayrı bir uygulamadır**: Debug Bridge onu başlatır,
  arayüz de Debug Bridge'e yerel bir servisle bağlanır ve **aynı bağlantıyı ve istek kuyruğunu** kullanır (port
  ikinci kez açılmaz). Okuduğu değerler Debug Bridge'in ekranlarına ve trafik günlüğüne düşer, *Debug Bridge'de
  göster* ile aynı IP'nin register görünümüne dönülür. Arayüzler AXIL Studio'nun dışında da durabilir:
  *Debug* menüsü → *IP arayüzü klasörleri…* ile eklenen klasörlerde, açık IP kütüphanelerinin yanında ve
  `interfaces/` altında aranır. Tanıtım dosyası, protokol ve Python istemcisi: [`interfaces/README.md`](interfaces/README.md).

### UART arayüzü (axiluart_phy)

`hdl/uart_phy` altındaki axiluart_phy IP'sinin arayüzü (`interfaces/axiluart_phy/`, AXIL Studio ile gelen ayrı
uygulama); IP `examples/uart_phy/uart_phy.axlib.json` kütüphanesinde, örnek proje
`examples/uart_phy/uart_phy_demo.axproj.json`. Üstte ACLK frekansı (baud bölücüleri ve DE zamanlaması buna göre
hesaplanır; projede saklanır). Pencere AXIL Studio'nun temasını ve dilini kullanır.

- **Çalışma modu:** *Tek IP (2 telli)* ya da *4 telli çift*. Çift, aynı projede bu arayüzü kullanan iki axiluart_phy
  örneğinden oluşur: biri gönderir (TX: DE sürekli açık ya da gönderirken açık, alıcı kapalı), diğeri alır (RX: DE
  kapalı, alıcı açık). Eş IP, bu IP'nin rolü ve TX'teki DE seçilip **Eşleştir** ile çift iki IP'nin ayarlarına birden
  kaydedilir (projede; iki IP'nin arayüzü de aynı çifti gösterir, Debug Bridge ağacının ipucu da belirtir).
  **Yönleri değiştir** rolleri güvenli sırayla takas eder (önce yeni alıcı ayarlanır, iki sürücü aynı anda açık
  kalmaz) ve iki FIFO'yu sıfırlar. Karakter formatı ve zamanlama iki IP'ye birlikte yazılır; durum kartı iki IP'nin
  DE, /RE ve FIFO değerlerini gösterir; terminal TX IP'sinden gönderir, RX IP'sinden alır. Eş IP adres haritasından
  çıkarılırsa arayüz tek IP moduna döner.

- **Hat ayarları:** baud, veri biti, parity, stop biti ve RTS/CTS → SETUP (bölücü ve gerçek baud hatası gösterilir);
  hat tipi (TTL / RS-232, RS-422, RS-485 4 telli, 2 telli, echo'lu), DE modu, half duplex, echo, polarite, TX/RX açık,
  DE lead / tail (µs; tail istenirse 1 bit süresi) → PHY_CTRL / PHY_TIMING. Durum: PHY_STATUS pinleri ve hat durumu,
  CFG_ERR, RX/TX FIFO doluluğu; CFG_ERR temizle, RX / TX reset. Ön ayarlar Debug Bridge config dosyalarıdır
  (`.axcfg.json`): forma yüklenir, formdan kaydedilir ya da Debug Bridge config listesine eklenir.
- **Terminal:** ASCII / HEX gönderme (sonuna CR, LF ya da CR+LF; `\r \n \t \xNN` kaçışları; ↑ ↓ geçmiş; dosya gönderme),
  RXREG'in periyodik okunmasıyla alım; ASCII, HEX ya da ASCII+HEX gösterim, RX satır sonu seçimi, zaman damgası.
  Parity / frame hatasıyla gelen byte'lar kırmızı zeminle işaretlenir; break ve overflow ayrıca bildirilir.
- **Doğrulama:** aşağıdaki donanım testlerinin arayüzü. Çift modunda PC bağlantısı seçilir: *iki yön için tek COM*
  (4 telli adaptör), *her bus'ta bir COM* (iki 2 telli adaptör) ya da *PC yok* (iki bus dışarıda birbirine bağlı).
  Önce iki IP'nin register testleri çalışır, sonra hat testleri; istenirse yönler değiştirilip tekrarlanır (tek 4
  telli adaptörle ters yön atlanır: adaptörün çiftleri sabittir). Hat adaptörü portu (ya da loopback), test edilen hat, test
  matrisi ya da DE lead / tail taraması seçilir; **Başlat** ile testler arka planda çalışır, sonuçlar canlı tabloya ve
  günlüğe düşer, **Durdur** testi keser. Çalışırken Debug Bridge'in canlı izlemesi durur ve bağlantısı kilitlenir; test
  bitince (durdurulsa da) kartın register'ları eski değerlerine döner. Ayarlar ve rapor JSON olarak kaydedilir.


### Komut sıralayıcı arayüzü (cmd_seq)

Komut sıralayıcı alt sistemlerin arayüzü (`interfaces/cmd_seq/`). Başlıkta kartın I-mem / D-mem boyutu (CAPS),
çalışma durumu ve I-mem'deki programın editördeki programla aynı olup olmadığı (IMEM_CNT / IMEM_SUM) görünür.

- **Program:** editör (yeni, aç, kaydet), **Derle** (F5): hatalı satır işaretlenir, sağda listing (PC, 64 bit kelime,
  disassembly, kaynak). Programda projenin adresleri adıyla kullanılır (`UART0_PHY_CTRL`, `ADC_BASE+4`, alan
  `_SHIFT` / `_MASK`), `.include` da çalışır. **I-mem'e yükle**: bağlantı kilitlenir, sıralayıcı durdurulup
  resetlenir, program yazılır ve IMEM_CNT / IMEM_SUM / IMEM_PHASE ile doğrulanır; **Yükle ve çalıştır** ardından
  başlatır. Program dosyasının yolu ve son yüklenen programın toplamı projede saklanır.
- **Çalıştır:** Çalıştır, Durdur, Adım, Reset, **Satıra kadar çalıştır** (editördeki imleç satırına kadar adım adım;
  donanımda breakpoint yok), ENTRY; CTRL ayarları (ERR_HALT, SNAP_EN, PT_WR_RUN, IRQ_EN). Durum: PC ve kaynak satırı
  (editörde ve listing'de vurgulanır), bayraklar, hata kodu açıklamasıyla, ERR_PC / ERR_ADDR, IRQ_STAT (temizle),
  SNAP_CNT, TIMER, terk edilmiş transfer; R0–R15. **İzle** seçiliyken 250 ms'de bir okunur (Debug Bridge
  günlüğüne yazılmaz).
- **D-mem:** tüm D-mem penceresi (SNAP_EN açıksa yayınlanan bank); değere çift tıklayıp yazılır.

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

Geliştirme aşaması: **early-alpha** (sonra alpha → beta → release). Uygulamanın **tek bir sürümü** vardır
(`axil_studio/__init__.py` → `APP_VERSION`); hub'da, pencere başlıklarında ve **Yardım → Hakkında**'da gösterilir.
Araçların ayrı sürümü yoktur. Kaydedilen proje dosyasına `generator` olarak yazan programın adı ve sürümü eklenir.

**Her commit sürümü 0.0.1 artırır**. Bunu `.githooks/pre-commit` kendiliğinden yapar; depoyu klonladıktan sonra bir
kez etkinleştir:

```
git config core.hooksPath .githooks
```

Sürüm elle değiştirilmiş bir commit'te (ör. yeni özellik için 0.2.0) hook dokunmaz. Elle artırmak için:
`python scripts/bump_version.py`.

## Arayüz dili ve çeviriler

Kaynak dil İngilizcedir: koddaki her kullanıcı metni İngilizce yazılır ve `tr()` ile çevrilir
(`axil_studio/i18n/`). Her dil, İngilizce metinden çeviriye bir JSON kataloğudur (`tr.json`); katalogda olmayan
metin İngilizce kalır. Değer içeren metinler numaralı yer tutucu kullanır: `tr("{0} registers").format(n)`.

- **View → Language** dili seçer; seçim bir sonraki açılışta geçerli olur (uygulama yeniden başlatmayı önerir).
  Qt'nin kendi pencereleri (dosya seçici, Evet/Hayır) de seçilen dile geçer.
- Komut satırı ve testler için `AXIL_STUDIO_LANG=tr` ortam değişkeni seçimi geçersiz kılar.
- Ayar dosyası: Windows `%APPDATA%\AXIL Studio\settings.json`, macOS `~/Library/Application Support/AXIL Studio/`,
  Linux `~/.config/AXIL Studio/`.
- **Yeni dil eklemek:** `tr.json`'u `<kod>.json` olarak kopyala, değerleri çevir, kodu `i18n.LANGUAGES`'a ekle.
- `python scripts/i18n_check.py` koddaki her `tr()` metnini kataloglarla karşılaştırır: eksik çeviriler, kullanılmayan
  girdiler (`--prune` siler) ve yer tutucu uyuşmazlıkları. Testler de bunu çalıştırır.

## Geri al / yinele ve hata kaydı

- **Düzen → Geri al** (Ctrl+Z) / **Yinele** (Ctrl+Y, Ctrl+Shift+Z) projedeki her değişikliği geri alır: IP ekleme,
  adres, boyut, kilit, taşıma, interconnect ayarları… Register Map Editor'da ise henüz kaydedilmemiş register
  haritasındaki değişiklikleri (register, bit alanı, değer açıklaması…) geri alır; proje bundan etkilenmez.
  Kaydedilen noktaya geri dönülünce "kaydedilmemiş" işareti kalkar. Metin kutularında Ctrl+Z kutunun kendi geri
  almasıdır.
- **Beklenmeyen hatalar** bir pencerede gösterilir (kısa mesaj, ayrıntılar, **Ayrıntıları kopyala**) ve log dosyasına
  yazılır: ayar klasöründe `logs/axil_studio.log` (1 MB × 3). **Yardım → Log klasörünü aç** klasörü açar. Arka plan
  işlerindeki (seri port, testbench) hatalar da yakalanır; Qt uyarıları da log'a gider.

## İki parçalı yapı ve testler

Verilog üretimi iki ayrı parçadır ve ikisi ayrı doğrulanır. Aralarındaki tek veri **HDL spec** dosyasıdır
(`<modül>.hdlspec.json`, `axil_studio/core/hdlspec.py`): Verilog'u üreten koda giden verinin tamamı ve yalnızca koda
etki eden kısmı.

| Parça | Ne yapar | Nerede |
|---|---|---|
| **1. Girdi → HDL spec** | Araçlarda ya da komut satırında tasarlanan IP'den / adres haritasından HDL spec'ler çıkar: IP başına bir, interconnect başına bir | araçlar, `model.py`, `addrmap.py`, `hdlspec.py` (`ip_hdlspec`, `xbar_hdlspec`) |
| **2. HDL spec → Verilog** | Yalnızca HDL spec okunur; proje, adres haritası, isimler, açıklamalar görülmez | `hdlspec.py` (`verilog`, `generate`), `axilgen.py`, `xbargen.py`, `axilpack.py` |

| HDL spec | İçinde olanlar | Olmayanlar (koda etki etmez) |
|---|---|---|
| IP (`axil-ip-hdlspec`) | `module`, `hdl_designer_wrapper`, `registers[]`: `name`, `offset`, `wr_strobe`, `rd_strobe`, `fields[]`: `name`, `lsb`, `width`, `kind`, `reset`, `irq`, `sync` | açıklamalar, değer anlamları, gösterim biçimi, IP sürümü, yerleştirilmemiş alanlar, adres |
| Interconnect (`axil-xbar-hdlspec`) | `module`, `hdl_designer_wrapper`, `masters` (sayı), `slaves[]`: `addr`, `mask`, `parameters`: `OPT_LOWPOWER`, `OPT_LINGER`, `LGMAXBURST` | master / slave adları, IP'lerin kullandığı alan, kilitler |

Register ve alan adları IP spec'inde vardır, çünkü port ve sinyal adları onlardan oluşur. Üretilen Verilog'un yorum
satırlarında da yalnızca HDL spec'teki bilgiler bulunur; testbench IP'nin adresine bağlı değildir (üst adres
bitlerinin yok sayıldığını sabit bir adresle dener).

HDL spec'i görmek: Wrapper'ın ve Interconnect Configurator'ın **Spec** sekmesi, Register Map Editor'daki **Spec**
penceresi (hepsi salt okunur); her üretimde çıktı klasörüne de yazılır. Komut satırı:

```
python -m axil_studio.core.axilgen --dump-spec proje.axproj.json   # projedeki tüm IP ve interconnect HDL spec'leri
python -m axil_studio.core.axilgen --dump-spec ip.regspec.json
python -m axil_studio.core.hdlspec my_ip.hdlspec.json -o out        # Parça 2 tek başına: HDL spec'ten Verilog
python -m axil_studio.core.hdlspec my_ip.hdlspec.json --check       # yalnızca doğrula
```

Bir hata çıkınca yeri böylece ayrılır: HDL spec yanlışsa Parça 1, HDL spec doğru ama Verilog yanlışsa Parça 2.

**Testler** (`tests/`, pytest):

- `tests/part1/` (girdi → HDL spec): `tests/data/ip` (register haritaları) ve `tests/data/xbar` (crossbar ayarları)
  ile örnek bir hiyerarşik projeden çıkan HDL spec'lerin `tests/data/hdl` altındaki beklenen dosyalarla aynı olması;
  açıklama, isim gibi verilerin HDL spec'i değiştirmemesi; adres haritası kuralları (kilitler, alt sistemler, crossbar gerekip gerekmediği, taşıma,
  çakışmalar, rastgele haritalar); araçlarda yapılan işlemlerin (register ekleme, adres yazma, taşıma…) beklenen HDL
  spec'i vermesi; araçlardaki Spec görünümleri, `--dump-spec` ve üretilen dosyaların birebir aynı olması.
- `tests/part2/` (HDL spec → Verilog): yalnızca `tests/data/hdl` altındaki HDL spec dosyalarından başlar: üretilen
  Verilog'un `tests/golden` altındaki kayıtlı dosyalarla aynı olması; `hdlspec` komutunun aynı dosyaları yazması;
  hatalı HDL spec'lerin reddedilmesi; IP testbench'lerinin iverilog'da geçmesi; her dosyanın derlenmesi; üç seviyeli
  bir alt sistem zincirinde (biri crossbar'sız, tek slave'li) yazma/okumaların doğru IP'ye ulaşması ve haritada
  olmayan adreslerin DECERR alması.

```
pip install pytest
python -m pytest                    # hepsi (iverilog yoksa simülasyon testleri atlanır)
python -m pytest tests/part2        # yalnızca spec → HDL
python -m pytest --update-golden    # üreticideki bilinçli bir değişiklikten sonra altın dosyaları yenile
```

`--update-golden` sonrası farkı git'te incele: üretilen HDL'deki her değişiklik orada görünür. GitHub'da her push
ve pull request'te aynı testler çalışır (`.github/workflows/tests.yml`: Ubuntu, Python 3.12, iverilog); sonuç
commit'in yanında görünür.

## Dosya düzeni

| Yer | İçerik |
|---|---|
| `studio.py` | Başlatıcı (`python studio.py`) |
| `axil_studio/` | Hub ve ortak arayüz parçaları (PySide6): `hub.py`, `ui.py`, `dialogs.py`, `theme.py`, `icons.py`, `project.py`; `history.py` (geri al / yinele), `errors.py` (hata yakalayıcı ve log) |
| `axil_studio/tools/` | Araçlar: `regmap.py`, `wrapper.py`, `address_map.py`, `interconnect.py` (+ `xbar_diagram.py`), `debug_bridge.py`, `ip_library.py` |
| `axil_studio/interfaces/` | IP arayüzleri için Studio tarafı: arama (`__init__.py`), yerel servis, başlatıcı ve klasör ayarları (`host.py`) |
| `interfaces/` | IP arayüzü uygulamaları (Studio'nun dışında çalışır): Python istemcisi `sdk/axil_client.py`, UART arayüzü `axiluart_phy/`, komut sıralayıcı arayüzü `cmd_seq/`; protokol `interfaces/README.md` |
| `axil_studio/core/` | Arayüzden bağımsız çekirdek (Qt yok), aşağıda |
| `scripts/` | Komut satırı araçları: `i18n_check.py` (çeviri denetimi), `bump_version.py` |
| `hdl/` | wb2axip dosyaları (`axilxbar.v`, `addrdecode.v`, `skidbuffer.v`, slave şablonu `easyaxil.v`) ve paketli `_pk` örnekleri; `uart_phy/`: axiluart_phy RTL'i, testbench'leri ve ZipCPU yamaları (bkz. `hdl/uart_phy/README.md`); `cmd_seq/`: programlanabilir komut sıralayıcı, assembler'ı ve doğrulama ortamı (formal, referans model, rastgele ve yönlendirilmiş testler; bkz. `hdl/cmd_seq/README.md`) |
| `examples/` | Örnek GPIO projesi ve config dosyaları; `uart_phy/`: axiluart_phy kütüphanesi, örnek proje, hat ön ayarları ve doğrulama ayarları; `cmd_seq/`: cmd_seq register haritası kütüphanesi ve komut sıralayıcılı örnek proje |
| `.githooks/` | Commit başına sürüm artıran git hook'u (bkz. Sürümler) |
| `axil_studio/i18n/` | Çeviri altyapısı ve dil katalogları (`tr.json`) |
| `axil_studio/assets/` | Uygulama simgesi (`axil_studio.svg`; kurulum paketleri için `.png` ve `.ico`) |
| `tests/` | pytest testleri: `part1` (girdi → HDL spec), `part2` (HDL spec → Verilog), `data` (örnek girdiler ve beklenen HDL spec'ler), `golden` (beklenen Verilog) |
| `.github/workflows/` | GitHub Actions: her push'ta testler |

`axil_studio/core/`:

| Modül | İçerik |
|---|---|
| `model.py` | Proje / IP / register / bit alanı modeli, adres haritası örnekleri, JSON kaydetme |
| `addrmap.py` | Adres haritası: otomatik adresleme, pencereler, doğrulama, interconnect slave'leri |
| `library.py` | IP kütüphanesi dosyaları (`.axlib.json`) |
| `hexbus.py` | Hexbus protokol sürücüsü + `SIMULATOR` portu (GPIO0 @0x60000000, loopback UART0 @0x60001000) |
| `hdlspec.py` | HDL spec: Verilog'un tek girdisi; projeden / register haritasından çıkarma, doğrulama, Verilog üretimi (komut satırı: `python -m axil_studio.core.hdlspec`) |
| `axilgen.py` | AXI4-Lite wrapper üretici (komut satırı: `python -m axil_studio.core.axilgen`) |
| `axilpack.py` | Paketli AXI portu wrapper'ı (`_pk`, REQ/RSP) |
| `xbargen.py` | axilxbar wrapper üretici (isimli portlar) |
| `regmap_xlsx.py` | Kütük haritası Excel çıktısı |
| `numfmt.py` | Bit alanı veri tipleri ve gösterimleri |
| `portparse.py` | Verilog / SystemVerilog / VHDL port listesi çözümleyici (Port içe aktar) |
| `cmdseq.py` | Komut sıralayıcı alt sistem türü: cmd_seq register haritası, ayarlar, kurallar, instance şablonu |
| `cheader.py` | Adres haritasının C header'ı ve assembler include dosyası |

Hexbus notları: her işlem `A<adres|1>W<veri>\n` veya `A<adres|1>R\n` olarak gönderilir (adres artırma kapalı);
yanıtlar `A` adres, `K` yazma onayı, `R` okuma verisi, `E` AXI bus hatası, `Z` idle. Hexbus'ta FIFO yoktur;
her isteğin yanıtı beklenerek sıralı çalışılır.

## Donanım doğrulama (axiluart_phy)

UART arayüzünün **Doğrulama** sekmesi (`interfaces/axiluart_phy/uart_validate.py`). Ayarlar sekmedeki alanlardır;
JSON olarak kaydedilip açılabilir (ör. `examples/uart_phy/bench_rs485_4w.json`). Hat ayarları (PHY_CTRL /
PHY_TIMING) testten önce yazılır, kartta o an yazılı olan değerler kullanılmaz; test bitince (durdurulsa da) kartın
register'ları eski hâline döner.

| Ayar | Anlamı |
|---|---|
| Hat adaptörü / loopback | UART hattındaki PC adaptörü (PC hattın karşı ucu olur) ya da adaptörsüz TX→RX bağlantısı |
| Hat tipi | `ttl`, `rs232` (DE 0), `rs422` (DE 1), `rs485-4w` (DE gönderirken), `rs485-2w` (DE gönderirken + half duplex, echo testleri), `rs485-2w-echo` |
| DE modu | Hat tipinin DE modunu ezer: düşük, yüksek, gönderirken |
| DE lead / tail | DE açılması → start biti; stop biti → DE kapanması (µs; tail istenirse 1 bit süresi) |
| Baud hızları, formatlar | Test matrisi; 5–6 bit için ör. `5N1, 5E1, 6O1, 6N2` |
| Test başına byte, enjeksiyonu atla, burst her formatta | Veri miktarı ve kapsam |
| DE lead / tail taraması | Normal testler yerine: son baud hızında, DE gönderirken açılırken hatasız çalışan en küçük lead/tail değerini bulur ve önerilen değeri (2× pay) DEF_TIMING olarak yazar. *Akıllı*: 8 uç byte ile değeri ikiye katlayarak aralığı bulur, ikiye bölerek daraltır, sonucu tam veriyle doğrular; *Doğrusal*: sabit listeyi her adımda tam veriyle dener |

Testler: register erişimi (WSTRB dahil), PHY_CTRL/TIMING, geçersiz ayar reddi, FIFO derinliği;
her baud × format için TX ve RX veri bütünlüğü; FIFO kapasitesinde kesintisiz burst alımı;
TX parity bitinin doğruluğu (5–7 bit formatlarda PC bir fazla data biti okur); parity hatası
(karşı parity), frame hatası (PC'de fazladan parity), break, overflow, TX_EN/RX_EN, DE zamanlaması
ve DE modları; `rs485-2w` ile echo bastırma.
