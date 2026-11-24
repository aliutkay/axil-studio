# cmd_seq ile iki LED'in yumuşak geçişi (axilgpio)

PCS (`cmd_seq`), ZipCPU `axilgpio`'nun iki çıkışına bağlı iki LED'i yazılımla PWM'ler:

1. LED A tam parlak, LED B kapalı (0,5 s)
2. B yavaş yavaş aydınlanırken A söner (1,5 s, 100 adım)
3. B tam parlak, A kapalı (0,5 s)
4. A aydınlanırken B söner (1,5 s), sonra 1'e döner; sonsuz döngü.

Süreler 100 MHz saat içindir.

| Dosya | İçerik |
|---|---|
| `led_fade.s` | PCS programı: iki LED yumuşak geçiş (47 komut) |
| `led_show.s` | PCS programı: 8 LED şovu (179 komut), aşağıda |
| `axilgpio.axlib.json` | axilgpio IP kütüphanesi: 8 register (LOAD, SET, CLEAR, TOGGLE, IN_DATA, CHANGED, INT_MASK, INT_ACTIVE) |
| `led_fade.axproj.json` | Proje: `SEQ0` komut sıralayıcı (0x0300_0000), altında `GPIO` (0x0301_0000) |
| `sim/tb_led_fade.v`, `sim/run_sim.py` | cmd_seq + gerçek `hdl/v2005/gpio/axilgpio.v` ile simülasyon ve dalga şekli kontrolü |

RTL (`hdl/v2005`): `gpio/axilgpio.v` (ZipCPU wb2axip, Apache-2.0), `common/skidbuffer.v`; 32 bit
adresli sarmalayıcı `gpio/axilgpio_a32.v`; HDL Designer için `gpio/axilgpio_pk.v`. Bağlantı: `o_gpio[0]` →
LED A, `o_gpio[1]` → LED B.

## Libero'da
Libero'nun bus definition listesinde AXI4-Lite yok; **AXI4** tanımı ID, LEN, SIZE, BURST, LAST
sinyallerini istiyor. Bunun için iki tam AXI4 sarmalayıcı var; ikisi de tek-beat aktarım yapar:

| Modül | Bus interface (AXI4) | Not |
|---|---|---|
| `hdl/cmd_seq/rtl/cmd_seq_axi4.v` | `S_AXI_*` = **slave**, `M_AXI_*` = **master** | Master: LEN = 0, SIZE = 4 bayt, INCR, WLAST = 1, ID = 0. Slave: isteğin ID'si BID / RID ile geri döner, RLAST = 1; yukarıdaki master burst göndermemeli |
| `hdl/v2005/gpio/axilgpio_axi4.v` | `S_AXI_*` = **slave** | 32 bit adres, çekirdeğe `[4:0]`; her yönde bir işlem bitmeden yenisini almaz, ID'ler doğru döner |

- Bağlantı: cmd_seq_axi4 `M_AXI` (master) → axilgpio_axi4 `S_AXI` (slave) doğrudan, ya da
  CoreAXI4Interconnect'in giriş portuna (mirrored master). Mirrored roller interconnect içindir,
  kendi çekirdeklerinde kullanma.
- Eşlemede her sinyal aynı adlı bus sinyaline gider (`S_AXI_AWLEN` → AWLEN …); `S_AXI_ACLK` /
  `S_AXI_ARESETN` interface'e girmez. USER sinyalleri yok.
- AXI4-Lite tanımı olan araçlar için `cmd_seq.v` ve `axilgpio_a32.v` (yalnızca Lite sinyalleri).
- Tüm dosyalar Verilog-2001 ile derlenir (ZipCPU'nun `axilgpio.v`'sindeki parametre listesi
  `localparam`'ı `parameter` yapıldı; değeri 32 kalmalı).
- `sim/run_sim.py --axi4` bu iki sarmalayıcıyla aynı dalga şekli kontrolünü yapar.

## Nasıl çalışıyor
axilgpio'da donanım PWM'i yok; PWM'i program üretiyor. Her PWM periyodu (`PERIOD` = 8000 çevrim
= 80 µs, 12,5 kHz) ikiye bölünür: ilk `D` çevrim B yanar, kalan `PERIOD − D` çevrim A yanar.
Yani B'nin parlaklığı `D / PERIOD`, A'nınki `1 − D / PERIOD`. `D`, `STEP` (80) adımlarla
0 → PERIOD → 0 gider; her seviye `REP` (188) periyot sürer.

- Kenarlar `WAITT` ile serbest sayan TIMER'a göre konur: periyot bus gecikmesine bağlı değildir
  ve kaymaz.
- Her geçiş **TOGGLE** register'ına iki LED bitiyle tek yazmadır: A ve B aynı saat çevriminde
  değişir, ikisi asla birlikte yanmaz ya da sönmez, diğer GPIO çıkışlarına dokunulmaz.
- Hız ve süre programın başındaki `.equ` satırlarından değişir. Kurallar: `PERIOD`, `STEP`'in
  katı olmalı; `STEP` en az 64 çevrim olmalı (program iki kenar arasında ~40 çevrim harcıyor).

## Studio'da çalıştırma
1. `led_fade.axproj.json`'u açın (kütüphaneyi kendisi yükler).
2. Debug Bridge → bağlanın → `SEQ0` satırına çift tıklayın: **Komut sıralayıcı** penceresi açılır.
3. Program sekmesine `led_fade.s`'i yapıştırın → *Assemble* → *Load* → *Run*.

Kendi tasarımınızda GPIO başka adresteyse yalnızca `LED_GPIO` satırını değiştirin (projedeki
`GPIO_BASE` ile aynı değer). Komut satırından: `python3 hdl/cmd_seq/sw/seqasm.py led_fade.s`.

## LED şovu (`led_show.s`)
8 LED'de (o_gpio[7:0]) PCS ile, HDL'de her biri ayrı sayaç ve durum makinesi isteyecek bir şov:

| Efekt | Ne yapıyor | PCS özelliği |
|---|---|---|
| Kuyruklu yıldız | Parlak bir nokta iki uç arasında gidip gelir, arkasında sönen kuyruk (15 7 3 1) | indeksli `STD D[FB+R6]`, `CALL fade` |
| Dalga | Sinüs benzeri bir dalga LED'ler boyunca kayar | D-mem'de tablo, `LDD R1, D[TAB+R2]` |
| Parıltı | Rastgele LED'ler parlayıp söner | 16 bit LFSR (`XOR`, `SHR`), alt program |
| Nefes | Hepsi birlikte yavaşça yanıp söner | tablo + döngü |

- **Her LED'de 16 kademe parlaklık, aynı anda:** bit-plane modülasyonu. Bir kare 15 zaman birimi;
  LED i, parlaklığı B[i]'nin k. biti 1 ise k. planda (1, 2, 4, 8 birim) yanar. Planlar GPIO LOAD
  register'ına yazılır, kenarlar `WAITT` ile kesin. 100 MHz'de bir kare 150 µs (6,7 kHz, titreşim yok).
- **Çalışırken PC'den kontrol** (Debug Bridge, D-mem penceresi, SNAP_EN = 0):
  - `D[16]` SPEED: adım başına kare (0 = varsayılan 200 → adım 30 ms; küçük = hızlı)
  - `D[17]` MODE: 0 = sırayla hepsi, 1 kuyruklu yıldız, 2 dalga, 3 parıltı, 4 nefes (efekt bitince geçer)
  - `D[18]` NOW: şu an çalışan efekt (okumak için)
  - SEQ0 base 0x0000_0000 iken: `0x0000_0444` ← 2 yalnızca dalga; `0x0000_0440` ← 50 dört kat hızlı.
- `LED_GPIO` = 0x0001_0000 (GPIO'nun adresi). LOAD tüm çıkışları yazar: o_gpio'nun 8 bitten yukarısı 0 olur.
  LED'lerin aktif-düşükse (0 = yanık) plan yazmalarından önce `XOR R10, 0xFF` gibi ters çevirmek gerekir.
- Bir sonraki kare 8 birimlik son planın sonunda hesaplanır; o plan ~570 çevrim uzar (100 MHz'de
  8000 yerine ~8570), yani en yüksek bit ~%4 daha ağır. Gözle fark edilmez.

Simülasyon (`sim/run_show.py`, `tests/test_led_fade_example.py`): cmd_seq + gerçek axilgpio, kısa zaman
birimiyle; GPIO'ya yazılan her plandan her LED'in parlaklığı çözülür ve programın Python modeliyle
kare kare karşılaştırılır (sırayla hepsi, MODE = 2 ve 3: ~230'ar kare, 0 fark); plan süreleri tam
1, 2, 4 birim.

```
python3 examples/cmd_seq_led_fade/sim/run_show.py
```

## Simülasyon
```
python3 examples/cmd_seq_led_fade/sim/run_sim.py          # kısa sabitlerle 2 tam döngü (~1 s)
python3 examples/cmd_seq_led_fade/sim/run_sim.py --real   # programın PERIOD / STEP değeri (~25 s)
```
Her periyotta B'nin `D`, A'nın `PERIOD − D` çevrim yandığını, periyotların kaymadığını ve iki
LED'in hiç aynı seviyede kalmadığını kontrol eder. İki çalıştırmada da sapma 0 çevrim.

## axilgpio notu
`INT_MASK`: RTL'de `o_int = |(INT_MASK & CHANGED)`, yani **1 = kesme açık**. axilgpio.v'nin baş
yorumu tersini söylüyor; kütüphane RTL'e göre yazıldı.
