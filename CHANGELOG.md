# Değişiklik günlüğü

Geliştirme aşamaları: **early-alpha → alpha → beta → release**. AXIL Studio'nun tek bir sürümü vardır
(`axil_studio/__init__.py` → `APP_VERSION`). Eski bölümlerdeki araç sürümü tabloları tarihçe olarak duruyor.

**Sürüm kuralı**
- Uygulamanın tek bir sürümü vardır (0.1.19'dan itibaren araçların ayrı sürümü yok).
- Her commit sürümü 0.0.1 artırır; `.githooks/pre-commit` bunu kendiliğinden yapar
  (`git config core.hooksPath .githooks` ile etkinleştir).
- `0.0.x`: düzeltme ve küçük değişiklik · `0.x.0`: yeni özellik · aşama değişimi (alpha, beta …) ayrıca duyurulur.
- Her sürüm için bu dosyaya bir bölüm eklenir ve `early-alpha-<hub sürümü>` etiketi atılır.
- early-alpha boyunca eski dosya biçimleriyle geriye dönük uyumluluk yoktur; alpha'dan itibaren sağlanır.

## early-alpha 0.1.33 — axilgpio 32 bit adresli sarmalayıcı
- `hdl/axilgpio_a32.v`: axilgpio'nun adres portları 32 bit (çekirdeğe `[4:0]` gider). Libero
  SmartDesign bus interface'i gibi eşit port genişliği isteyen araçlarda doğrudan bağlanır.
- LED örneğinin testbench'i artık bu sarmalayıcı üzerinden, tam 32 bit adresle çalışıyor.

## early-alpha 0.1.32 — Örnek: PCS ile iki LED'in yumuşak geçişi
- `examples/cmd_seq_led_fade/`: `led_fade.s`, cmd_seq'in axilgpio'daki iki LED'i yazılım PWM'i ile
  sürdüğü program (biri tam parlakken öteki kapalı, sonra yer değiştirerek yumuşak geçiş, döngü).
  Geçişler TOGGLE'a tek yazma, kenarlar `WAITT` ile; `axilgpio.axlib.json` (8 register),
  `led_fade.axproj.json` (SEQ0 + GPIO), `sim/run_sim.py` (gerçek RTL ile dalga şekli kontrolü).
- `hdl/axilgpio.v`: ZipCPU axilgpio çekirdeği (Apache-2.0); `axilgpio_pk.v` artık repo içinde derlenir.
- `tests/test_led_fade_example.py`: proje, kütüphane, Studio penceresiyle derleme, RTL simülasyonu.

## early-alpha 0.1.31 — Debug Bridge: komut sıralayıcı satırı
- Debug Bridge ağacında komut sıralayıcı subsystem'i tıklanamayan bir grup başlığı olarak
  çiziliyordu; kendi registerları ve arayüz düğmesi yoktu. Artık seçilebilir bir IP satırı:
  altında kendi registerları ve subsystem'in slave'leri var, çift tıklama ya da bir registerı
  seçince çıkan **Komut sıralayıcı** düğmesi arayüzü açar. Test eklendi.

## early-alpha 0.1.30 — cmd_seq: indeksli D-mem, DMEM_AW düzeltmesi

- **Indeksli D-mem:** `LDD Ra, D[n+Rb]`, `STD D[n+Rb], Ra`, `RD D[n+Rx], [Rb+imm]`. İndeks çalışma anında
  denetleniyor; D-mem dışına çıkarsa hata 7. Dizi doldurma ve kanal tarama artık döngüyle yazılabiliyor.
- **Düzeltme:** `DMEM_AW > 8` iken D indeksi 8 bitlik alanın dışındaki bitleri seçiyordu (simülasyonda X). Artık
  sıfırla genişletiliyor; 256'dan sonraki kelimelere indeksle ya da master'dan erişiliyor.
- `SEL_LSB` parametresi kaldırıldı: kendi pencere çözümü yalnızca 16 ile çalışıyordu. Kendi pencere 64 KB sabit.
- README notları:
  - SNAP_EN = 1 iken master'ın D-mem yazmaları yayın bankına gidiyor (parametreleri SNAP_EN = 0 iken yazın);
  - AW ve W kanalları birlikte kabul ediliyor;
  - yalnızca 32 bit yazma destekleniyor;
  - `CTRL = 0x51` açılımı.
- Testler: rastgele üreticide indeksli erişim, yönlendirilmiş testte `index.s`. Formal ispatlar yeniden geçti.

## early-alpha 0.1.28 — Komut sıralayıcı alt sistem türü

- **Alt sistem türü:** alt sistem eklerken / düzenlerken *Slave grubu* ya da *Komut sıralayıcı (cmd_seq)*
  seçilir; her alt sistem komut sıralayıcı değildir.
  - Komut sıralayıcıda pencerenin ilk 64 KB'ı cmd_seq registerlarıdır (kilitli kendi penceresi), geri kalanı
    içindekilere iletilir.
  - Ayarlar: I-mem / D-mem adres bitleri, zaman aşımları.
  - Kurallar: taban 64 KB hizalı, pencere ≤ 16 MB, `DMEM_AW` ≤ 13. cmd_seq slave olarak eklenirse hata verir.
- **Register haritası:** cmd_seq'in registerları alt sistemin adıyla Debug Bridge'de, C header'da ve servis
  isteklerinde görünür. Adres haritası ağacı, blok diyagramı ve şerit komut sıralayıcıyı ayrıca gösterir.
- **Interconnect Configurator:** her komut sıralayıcı için parametreleri doldurulmuş cmd_seq instance şablonu
  üretir (`<alt sistem>_cmd_seq_inst.v`). `hdl/cmd_seq_pk.v`: HDL Designer sarmalayıcısı.
- **Address Map Editor → Assembler include…:** adres haritasını assembler için `.equ` satırları olarak yazar.
  `seqasm.py` artık `.include` yönergesini destekliyor.
- **Servis:** yeni `address_map` isteği (SDK: `AxilClient.address_map()`) adres haritasının tüm girişlerini register
  haritalarıyla verir.
- **Komut sıralayıcı arayüzü** (`interfaces/cmd_seq/`):
  - program editörü ve derleme; projenin adresleri adıyla kullanılır, hatalı satır işaretlenir, listing gösterilir;
  - kilitli yükleme ve IMEM_CNT / IMEM_SUM doğrulaması;
  - çalıştır, durdur, adım, reset, satıra kadar çalıştır;
  - PC'nin kaynakta vurgulanması, bayraklar, hata açıklaması, R0–R15 ve D-mem.
- `examples/cmd_seq`: cmd_seq kütüphanesi ve örnek proje. Testler: `tests/test_cmd_seq_studio.py` (arayüz, cmd_seq
  davranışlı sahte kartla test ediliyor).
- Test paketi: oturum sonunda açık kalan Qt pencereleri QApplication kapanmadan siliniyor (`tests/conftest.py`).
  Önceden pytest testler geçse de çıkışta zaman zaman bellek hatasıyla kapanıyordu.

## early-alpha 0.1.27 — cmd_seq testleri

- `tests/test_cmd_seq.py` ve `run_formal.py` bitwuzla paketini test sürecine import etmiyor (yalnızca varlığına
  bakıyor; çözücü ayrı süreçte çalışıyor). Paket Qt ile aynı süreçte yüklenince çıkışta bellek hatası olabiliyordu.

## early-alpha 0.1.26 — cmd_seq: Verilog, düzeltmeler ve doğrulama ortamı

- Programlanabilir komut sıralayıcı `hdl/cmd_seq` olarak eklendi (ilk VHDL sürümünden Verilog'a çevrildi;
  `S_AXI_*` / `M_AXI_*` portları, PROT da aynen iletiliyor).
- Düzeltmeler:
  - LDD bir önceki D-mem erişiminin kelimesini okuyordu.
  - `BR LT` yalnızca N'ye bakıyordu; artık işaretli (N ≠ V). C / V bayrakları ve LO, HS, MI, PL koşulları
    eklendi; tanımsız koşul kodu geçersiz komut.
  - Terk edilen (RESET) transferin geç gelen cevabı sonraki isteğe gidebiliyordu.
  - Zaman aşımına uğrayan pass-through yazması sonradan gönderilebiliyordu; hiç cevap vermeyen alt slave üst
    yazma kanalını kilitliyordu.
  - Yığın taşması / boşalması ve PC'nin I-mem dışına çıkması algılanmıyordu (hata 4 ve 6).
  - KILL kodları 0x10'dan küçükse sistem hata kodlarıyla çakışıyordu.
  - IRQ_STAT'a aynı anda gelen W1C yeni olayı siliyordu.
  - Sıralayıcı transferinde zaman aşımı (hata 5). I-mem / D-mem yükleyicileri sonu geçen yazmayı ve
    duraklatılmış programa yüklemeyi reddediyor; kendi penceresine yazmada tam WSTRB isteniyor.
- `sw/seqasm.py`: yeni koşullar, KILL ve LOOP R0 denetimi, `.word64`; `sw/examples`: dört örnek program.
- Doğrulama:
  - Formal (Yosys + yosys-smtbmc; BMC, k-tümevarım ve cover): üst modül ve çekirdek ayrı ayrı ispatlanıyor.
  - Rastgele programlarla Python referans modeline karşı eş-simülasyon.
  - Yönlendirilmiş testbench (105 kontrol).
  - Hepsi `hdl/cmd_seq/verif/run.py` ile tek komutta çalışıyor; testler `tests/test_cmd_seq.py`'de.

## early-alpha 0.1.25 — axiluart_phy RTL düzeltmeleri

- `patches/axiluart_v2.patch` artık verici örneğini de değiştiriyor (`txuart` → `txuart_v2`); önceden bu satır
  bağlam olarak kaldığı için patch orijinal `axiluart.v`'ye uygulandığında parity düzeltmesi gelmiyordu. Patch,
  `axiluart_v2.v`'ye geri alınıp yeniden uygulanınca dosyanın aynısını veriyor (testli).
- `uart_phy_adapter.v`: PHY_CTRL'nin reset değeri donanıma göre sınırlanıyor (HAS_DE yoksa DE düşük, HAS_TX / HAS_RX
  yoksa TX_EN / RX_EN kapalı, ECHO yalnızca HALF ile, DE_MODE 3 hiç). Önceden HAS_DE=0 ve varsayılan DEF_CTRL=0xC2
  ile okunan değer DE_MODE=2 gösteriyor, geri yazılınca CFG_ERR kalkıyordu. Tam donanımlı varsayılan davranış aynı.
- Yeni testbench `tb/tb_adapter_defaults.v` (yalnızca adapter, ZipCPU dosyası gerekmez) ve test paketinde iki RTL testi.

## early-alpha 0.1.24 — C header adres haritasından, UART 4 telli çift

- **C header Address Map Editor'a taşındı.** Register haritasının adresi olmadığı için C header artık adres
  haritasından üretilir (*C header…*, `<proje>_map.h`): IP tanımı başına offset / shift / mask ve sürüm; örnek başına
  `_BASE`, `_SIZE` ve register adresleri; harici slave ve alt sistemler. Komut satırı:
  `python -m axil_studio.core.cheader`. Slave Interface Wrapper'dan C header önizlemesi ve `<ip>_regs.h` çıktısı
  kaldırıldı.
- **UART arayüzü: 4 telli çift.** Aynı projedeki iki axiluart_phy örneği eşleştirilir (çift iki IP'nin ayarlarına
  kaydedilir); biri TX, diğeri RX olur, TX'te DE sürekli açık ya da gönderirken açık seçilir. *Yönleri değiştir*
  rolleri güvenli sırayla takas eder. Format ve zamanlama iki IP'ye yazılır; durum ve terminal çifte göre çalışır.
- **Doğrulama çift modunda:** PC bağlantısı tek COM (4 telli adaptör), her bus'ta bir COM ya da PC'siz dış loopback;
  iki IP'nin register testleri, ardından hat testleri ve isteğe bağlı olarak ters yönde tekrar.
- Servis protokolüne `peers` isteği ve eş IP için `settings_get` / `settings_set` eklendi.
- Örnek proje `examples/uart_phy/uart_phy_demo.axproj.json` artık iki UART içerir (UART0, UART1).

## early-alpha 0.1.23 — IP arayüzleri ayrı uygulama

- **IP arayüzleri artık AXIL Studio'nun dışında çalışan ayrı uygulamalar.** Debug Bridge arayüzü bir süreç olarak
  başlatır; arayüz bilgisayar içindeki yerel bir servise (127.0.0.1, oturum anahtarlı, satır başına JSON) bağlanır ve
  Debug Bridge'in bağlantısını ve istek kuyruğunu kullanır: register okuma/yazma, toplu erişim, FIFO boşaltma,
  bağlantıyı tutma (kilit), Debug Bridge'de register gösterme, config listesine ekleme, projede ayar saklama; bağlantı,
  IP, tema ve kapanma olayları. Arayüz zaten açıksa penceresi öne gelir; Studio kapanınca arayüzler de kapanır.
- Arayüzler `axil-interface.json` tanıtım dosyalarıyla bulunur: önce *Debug → IP arayüzü klasörleri…* ayarındaki
  klasörler, sonra açık IP kütüphanelerinin klasörleri, en son `interfaces/`. Bulunamayan arayüz için Debug Bridge
  aranan kimliği yazar ve klasör ayarını açar. Register Map Editor'daki *IP arayüzü* alanı bulunan kimlikleri listeler,
  kurulu olmayan bir kimlik de yazılabilir.
- Python istemcisi `interfaces/sdk/axil_client.py` (tek dosya, standart kütüphane); Studio başlattığı Python
  arayüzlerinin yoluna ekler. Protokol: `interfaces/README.md`.
- **UART arayüzü** `interfaces/axiluart_phy/` altında ayrı bir uygulama oldu (kendi çevirileri ve AXIL Studio'dan
  aldığı temayla); yalnızca Debug Bridge üzerinden çalışır. `axil_studio/panels/` ve `axil_studio/core/uart_*.py`
  kaldırıldı; doğrulama testleri arayüzün klasörüne taşındı.
- Komut satırı doğrulama aracı (`scripts/uart_validate.py`) kaldırıldı: doğrulama UART arayüzünün Doğrulama
  sekmesinden çalışır.

## early-alpha 0.1.22 — IP arayüzleri, axiluart_phy ve UART arayüzü

- **IP arayüzü:** bir IP'nin register haritası artık kendi Python arayüzünü adlandırabilir (Register Map Editor → *IP
  arayüzü*; `IPBlock.panel` / regspec `"panel"`, projede ve IP kütüphanesinde IP ile birlikte saklanır). Debug Bridge'de
  böyle bir IP ağaçta simgeyle görünür; register panelindeki düğme, çift tık ya da sağ tık arayüzü açar. Arayüz Debug
  Bridge'in bağlantısını ve istek kuyruğunu paylaşır, okuduklarını Debug Bridge'in ekranlarına yazar ve *Debug Bridge'de
  göster* ile geri döner. IP Library ipuçları arayüzü olan IP'leri belirtir. Kayıt sistemi: `axil_studio/panels/`.
- **axiluart_phy (hdl/uart_phy):** RS-232 / RS-422 / RS-485 hat arayüzlü AXI4-Lite UART'ın RTL'i (`axiluart_phy.v`,
  `uart_phy_adapter.v`, ZipCPU tabanlı `axiluart_v2.v` ve `txuart_v2.v`), testbench'leri ve ZipCPU yamaları eklendi
  (ZipCPU dosyaları GPLv3). Register haritası alan türleri ve değer anlamlarıyla `examples/uart_phy/uart_phy.axlib.json`
  kütüphanesinde; örnek proje, hat ön ayarları ve doğrulama ayarları `examples/uart_phy/` altında.
- **UART arayüzü** (axiluart_phy için): *Hat ayarları* (baud / format → SETUP, hat tipi, DE modu, half duplex, echo,
  polarite, DE lead / tail → PHY_CTRL / PHY_TIMING, PHY_STATUS ve FIFO durumu, ön ayarlar), *Terminal* (eski UART
  konsolunun ASCII / HEX terminali) ve *Doğrulama* (eski `uart_validate` testlerinin grafik arayüzü: canlı sonuç tablosu,
  Durdur, JSON rapor; çalışırken Debug Bridge'in canlı izlemesi durur, sonunda register'lar geri yüklenir).
- Doğrulama testleri `axil_studio/core/uart_validate.py`'ye taşındı (iptal edilebilir, sonuçları geri çağırmayla
  bildirir); `scripts/uart_validate.py` artık onu çalıştıran ince bir başlatıcı. Simülasyon modu (`--simulate`,
  `uart_validate_sim.py`) kaldırıldı. Yeni hat tipi `rs485-2w-echo`.
- `examples/bench_rs485_4w.json` → `examples/uart_phy/bench_rs485_4w.json`.

## early-alpha 0.1.20 — Alt sistem (Subsystem), sadeleşen komutlar, yeni araç açıklamaları

- **Sub-bus Bridge → Alt sistem (Subsystem):** adres haritasında slave'leri tek bir pencerede toplayan kayıt artık
  *alt sistem* adını taşıyor (yeni adlar `SUB_0`, `SUB_1` …; proje dosyası biçimi değişmedi). **Crossbar yalnızca
  gerektiğinde üretilir:** birden fazla slave'i ya da ek master'ı olan alt sistemin kendi axilxbar'ı olur; tek
  slave'li alt sistemde crossbar üretilmez, üst bus'taki SLAVE portu doğrudan o slave'e bağlanır. Address Map Editor'da
  tür sütunu *Alt sistem · crossbar* / *Alt sistem · doğrudan* yazar; Interconnect Configurator'da tek slave'li alt
  sistemler listede *(doğrudan)* görünür, seçilince nedenini açıklar ve tüm sistem diyagramını gösterir; sistem
  diyagramı doğrudan bağlantıyı crossbar'sız çizer. Üretim, HDL spec'ler, `axilgen --xbar` ve modül adı
  çakışma denetimi yalnızca crossbar'ları kapsar.
- **Excel'e aktar** yalnızca Register Map Editor (tek IP) ve Address Map Editor'da (tüm harita). Slave Interface
  Wrapper'dan Excel düğmesi ve menüsü kaldırıldı, **Üret…** artık Excel yazmaz (komut satırı `axilgen` yazmaya devam
  eder).
- Register Map Editor'dan **Adres haritasına ekle** kaldırıldı; IP adres haritasına Address Map Editor'da yerleştirilir.
- Hub'dan **Örnek proje** düğmesi kaldırıldı.
- Sekme köşesindeki **Ayrı pencere** düğmesinin çerçevesi kaldırıldı.
- Hub kartlarındaki ve araç başlıklarındaki açıklamalar ile **İş akışı** adımları yeniden yazıldı: aracın ne
  sunduğunu anlatan kısa, ürün dili ("adres içermez" gibi olumsuz notlar yok).
- Testler: tek slave'li alt sistemin crossbar'sız kalması ve slave ya da master eklenince crossbar'a dönüşmesi;
  kademeli simülasyonda crossbar'sız alt sistemden geçen yazma/okumalar; Excel komutlarının yalnızca iki editörde
  olması.

## early-alpha 0.1.19 — İngilizce arayüz, çeviri altyapısı, geri al / yinele, simge

- **Tek sürüm:** araçların ayrı sürümleri kaldırıldı (hub kartları, araç başlıkları, Hakkında); yalnızca uygulama
  sürümü gösterilir.
- **Arayüz dili:** varsayılan İngilizce. Çeviri altyapısı (`axil_studio/i18n/`): koddaki metinler `tr()` ile çevrilir,
  diller JSON kataloglarıdır; Türkçe katalog (`tr.json`) mevcut Türkçe metinlerle eklendi. **View → Language** dili
  seçer (yeniden başlatmayı önerir); Qt'nin kendi pencereleri de dile uyar. `AXIL_STUDIO_LANG` ortam değişkeni
  seçimi geçersiz kılar. `scripts/i18n_check.py` eksik / kullanılmayan çevirileri ve yer tutucu uyuşmazlıklarını bulur.
  Üretilen Excel, C header ve doküman da arayüz dilini izler; Verilog'un içeriği dilden bağımsızdır.
- **Uygulama simgesi:** bir crossbar (master'lardan slave'lere) çizen yeni simge; pencere başlıklarında, görev
  çubuğunda ve hub'da. `assets/` altında SVG, PNG ve Windows için ICO.
- **Yardım metinleri:** panellerdeki uzun paragraflar kaldırıldı; başlıkların yanında **?** düğmesi açıklamayı
  gösterir. Notlar tek satıra indi (Address Map Editor, Interconnect Configurator, port içe aktarma, Sub-bus Bridge
  penceresi).
- **Düğmeler:** ikincil (yalnızca metinli) düğmelerin ince bir çerçevesi var, düz metinden ayırt ediliyor; devre dışı
  düğmeler kesikli çerçeveyle soluk. Debug Bridge'deki bağlantı durumu düğmeye benzemeyen bir durum etiketi oldu.
- **Geri al / yinele:** Düzen menüsü (Ctrl+Z, Ctrl+Y / Ctrl+Shift+Z). Projede her değişiklik geri alınır; Register
  Map Editor'da kaydedilmemiş register haritasının kendi geçmişi vardır. Kaydedilen noktaya dönülünce "kaydedilmemiş"
  işareti kalkar.
- **Hata yakalayıcı:** beklenmeyen hatalar (arayüz olayları ve arka plan işleri) pencerede gösterilir ve
  `logs/axil_studio.log`'a yazılır; **Yardım → Log klasörünü aç**.
- Testler: çeviri denetimi, iki dilde tüm pencerelerin açılması, geri al / yinele, hata yakalayıcı, simge.

## early-alpha 0.1.18 — HDL spec: Verilog'un tek girdisi

| Bileşen | Sürüm |
|---|---|
| AXIL Studio (hub) | early-alpha 0.1.18 |
| Register Map Editor | early-alpha 0.5.5 |
| Slave Interface Wrapper | early-alpha 0.2.7 |
| Address Map Editor | early-alpha 0.3.5 |
| Interconnect Configurator | early-alpha 0.3.6 |
| UART-to-AXI Debug Bridge | early-alpha 0.0.5 |
| IP Library | early-alpha 0.2.1 |

- **HDL spec** (`<modül>.hdlspec.json`, `core/hdlspec.py`): Verilog'u üreten koda giden verinin tamamı ve yalnızca koda
  etki eden kısmı. IP için modül adı, HDL Designer wrapper'ı, registerlar ve bit alanları (ad, offset, konum, genişlik,
  tür, reset, irq, sync, strobe'lar); interconnect için modül adı, master sayısı, slave adres ve maskeleri, HDL
  Designer wrapper'ı, axilxbar parametreleri. Açıklamalar, değer anlamları, sürüm, port adları ve adres içinde yok.
- Verilog dosyaları yalnızca HDL spec'ten üretilir (araçlarda, `axilgen`'de ve yeni `python -m axil_studio.core.hdlspec
  <dosya> -o out` komutunda aynı yol). Her üretim HDL spec'i de çıktı klasörüne yazar; interconnect çıktısındaki
  `.xbar.json` yerine `.hdlspec.json` gelir (ayarlar için Dışa aktar `.xbar.json` yazmaya devam eder).
- Spec sekmeleri / penceresi ve `--dump-spec` artık HDL spec'i gösterir (önceki sürümdeki "regspec + base" görünümünün
  yerine).
- Üretilen Verilog'un mantığı değişmedi. Yorum satırlarından HDL spec dışındaki bilgiler çıktı (IP sürümü ve açıklaması,
  alan açıklamaları, master / slave adları); IP testbench'i IP'nin adresine bağlı değil, sabit bir bus adresi
  (`0xA5A5A000`) kullanır. IP sürümü ve açıklamalar C header, doküman ve Excel'de kalır.
- Testler: Parça 1'in beklenen çıktısı `tests/data/hdl` altındaki HDL spec dosyaları; Parça 2 yalnızca bu dosyalardan
  başlar.

## early-alpha 0.1.17 — iki parçalı yapı: Spec görünümleri, testler

| Bileşen | Sürüm |
|---|---|
| AXIL Studio (hub) | early-alpha 0.1.17 |
| Register Map Editor | early-alpha 0.5.4 |
| Slave Interface Wrapper | early-alpha 0.2.6 |
| Address Map Editor | early-alpha 0.3.5 |
| Interconnect Configurator | early-alpha 0.3.5 |
| UART-to-AXI Debug Bridge | early-alpha 0.0.5 |
| IP Library | early-alpha 0.2.1 |

- Üretim iki ayrı parça: **girdi → spec** (araçlar, komut satırı) ve **spec → HDL** (üreticiler). Sınır
  `core/specs.py`'de: üretimin tam girdisi.
- **Spec** görünümleri (salt okunur): Slave Interface Wrapper'da ve Interconnect Configurator'da *Spec* sekmesi
  (seçili interconnect ya da hepsi), Register Map Editor'da üst bardaki *Spec* (ayrı pencere, düzenledikçe
  güncellenir). Kopyala ve Kaydet. Register spesifikasyonu adres içermez; IP'nin adresi ayrı bir üretim girdisi
  (`base`) olarak yanında gösterilir.
- Komut satırı: `axilgen --dump-spec <proje | .regspec.json | .xbar.json>` aynı girdiyi JSON olarak yazar, dosya
  üretmez.
- Üretici yalnızca spec'i görür: `generate_all()` artık adres haritası hesaplamaz ve çıktı klasörüne `.axproj.json`
  yazmaz; `base` verilmezse 0'dır (komut satırında `--base`, varsayılan 0). Üretilen HDL değişmedi.
- Testler (`tests/`, pytest): Parça 1 için adres haritası kuralları, projeden spec'lere, araç işlemlerinden spec'lere,
  araç ile komut satırının aynı spec'i ve aynı dosyaları vermesi; Parça 2 için altın dosyalar (`tests/golden`),
  üretilen testbench'lerin iverilog'da geçmesi, wrapper derlemesi ve üç seviyeli Sub-bus Bridge zincirinde adres
  yönlendirme simülasyonu.
- GitHub Actions: testler her push ve pull request'te çalışır (`.github/workflows/tests.yml`).

## early-alpha 0.1.16 — hata düzeltmeleri (kod incelemesi)

| Bileşen | Sürüm |
|---|---|
| AXIL Studio (hub) | early-alpha 0.1.16 |
| Register Map Editor | early-alpha 0.5.3 |
| Slave Interface Wrapper | early-alpha 0.2.5 |
| Address Map Editor | early-alpha 0.3.5 |
| Interconnect Configurator | early-alpha 0.3.4 |
| UART-to-AXI Debug Bridge | early-alpha 0.0.5 |
| IP Library | early-alpha 0.2.1 |

- Address Map Editor: sığmadığı için reddedilen bir taşıma artık her adresi ve kilidi olduğu gibi bırakır (önceden
  kilitli bir slave yeni, yanlış bir adreste kilitli kalabiliyordu).
- Address Map Editor: bir Sub-bus Bridge'e elle adres yazınca (tabloda ya da düzenleme penceresinde) ve boyutu
  değişip adresi hizalanınca içindekiler de onunla taşınır; kilitli çocuklar köprü penceresinin dışında kalmaz.
- Address Map Editor: düzenleme penceresinde ad ve boyut birlikte değişip sığmazsa ad değişikliği de geri alınır.
- Address Map Editor: adres/boyut kilidini açma, tüm kilitleri aç/kapat, yukarı/aşağı taşıma ve adrese göre
  sıralama da elle boyutlanmış bir Sub-bus Bridge'i taşıracaksa reddedilir (diğer düzenlemeler gibi).
- Address Map Editor: bir bridge'in son öğesi silinince seçim bridge'e geçer.
- Adres haritası: 32 bite sığmayan bir yeniden adresleme haritayı yarım güncellenmiş bırakmaz; boş bir Sub-bus
  Bridge ve boş adres haritası Address Map Editor'da da hata olarak gösterilir.
- Modül adı çakışmaları hata olarak gösterilir: aynı adı alan interconnect'ler, IP'ler ve `_pk` wrapper'ları;
  ZipCPU modül adları (`axilxbar`, `addrdecode`, `skidbuffer`, `easyaxil`) IP ya da interconnect adı olamaz.
- Komut satırı `axilgen --xbar <proje>` artık arayüzle aynı dosyaları üretir: her interconnect (Sub-bus Bridge'lerin
  arkasındakiler dahil), projedeki modül adları, master'lar ve port stiliyle; önce adres haritasını denetler.
- Proje, IP kütüphanesi, register spesifikasyonu ve .xbar.json dosyaları tek adımda yazılır (yarıda kalan bir yazma
  dosyayı bozmaz). Farklı kaydet başarısız olursa proje eski dosyasında kalır; kütüphaneye yazılamazsa kütüphane
  değişmez ve hata gösterilir (Register Map Editor).
- Slave Interface Wrapper: hazır IP için HDL Designer wrapper'ı yazılamazsa hata gösterilir.
- UART-to-AXI Debug Bridge: seri port koparsa (ör. USB çıkarıldı) bağlantı kapalı görünür ve canlı izleme durur;
  sonraki erişimler zaman aşımı yerine "bağlantı koptu" der.
- Port içe aktarma: genişlik ifadeleri `eval` olmadan hesaplanır; `2**64**64` gibi aşırı büyük ifadeler uygulamayı
  dondurmaz, çözülemedi olarak işaretlenir.

## early-alpha 0.1.15 — diyagram gösterim seviyeleri, kilit hizası

| Bileşen | Sürüm |
|---|---|
| AXIL Studio (hub) | early-alpha 0.1.15 |
| Register Map Editor | early-alpha 0.5.2 |
| Slave Interface Wrapper | early-alpha 0.2.4 |
| Address Map Editor | early-alpha 0.3.4 |
| Interconnect Configurator | early-alpha 0.3.3 |
| UART-to-AXI Debug Bridge | early-alpha 0.0.4 |
| IP Library | early-alpha 0.2.1 |

- Interconnect Configurator: *Tüm sinyalleri göster* kalktı; **Gösterim** seçimi dört seviyeli: *Master / slave*
  (tek ok, varsayılan; görünüm varsayılan olarak hiyerarşik *Tüm sistem*), *AXI Lite (Basitleştirilmiş Görünüm)*,
  *AXI Lite (Detaylı Görünüm)* (tüm pinler; tüm sistem görünümünde de) ve *HDL Designer (REQ / RSP)*. Her iki
  görünümde ve PDF / draw.io çıktısında geçerli.
- Address Map Editor: satırlardaki adres ve boyut kilitleri sağa, değerin hemen yanına yaslı, solda boşlukla;
  başlıktaki kilit ikonları onlarla aynı hizada.

## early-alpha 0.1.14 — kilitler başlıkta, başlangıç adresi 0

| Bileşen | Sürüm |
|---|---|
| AXIL Studio (hub) | early-alpha 0.1.14 |
| Register Map Editor | early-alpha 0.5.2 |
| Slave Interface Wrapper | early-alpha 0.2.4 |
| Address Map Editor | early-alpha 0.3.3 |
| Interconnect Configurator | early-alpha 0.3.2 |
| UART-to-AXI Debug Bridge | early-alpha 0.0.4 |
| IP Library | early-alpha 0.2.1 |

- Address Map Editor: alttaki *Tüm adresleri kilitle* / *Tüm boyutları kilitle* düğmeleri kalktı; yerlerine tablo
  başlığında *Adres* ve *Boyut* yazılarının önünde yalnızca ikon (üzerine gelince açıklama, tıklayınca hepsi).
  Boyut kilidi de boyutun önünde; kilitler dar sütunda, değerlerinin hemen yanında.
- Varsayılan başlangıç adresi `0x00000000` (adres haritasında başlangıç adresi kaydedilmiş projeler kendi
  değerini korur). Debug Bridge'de ham adres erişimi ve ek IP adresi de 0'dan başlar.

## early-alpha 0.1.13 — kilit düğmesi düzeltmesi

| Bileşen | Sürüm |
|---|---|
| AXIL Studio (hub) | early-alpha 0.1.13 |
| Register Map Editor | early-alpha 0.5.2 |
| Slave Interface Wrapper | early-alpha 0.2.4 |
| Address Map Editor | early-alpha 0.3.2 |
| Interconnect Configurator | early-alpha 0.3.2 |
| UART-to-AXI Debug Bridge | early-alpha 0.0.4 |
| IP Library | early-alpha 0.2.1 |

- Düzeltme: Address Map Editor'da kilit düğmeleri `'bool' object has no attribute 'window'` hatası veriyordu (Qt'nin
  `clicked(bool)` değeri örnek parametresine gidiyordu). Aynı hata sağ tık → Taşı menüsünde, son açılan projeler
  menüsünde, Araçlar menüsünde ve satır ikonlarında da önlendi.
- Address Map Editor: adres kilidi adresin önünde.

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
