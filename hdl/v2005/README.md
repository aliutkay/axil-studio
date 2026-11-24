# hdl/v2005: localparam'sız Verilog-2005 dosya seti

UART'lı hexbus (PC → AXI4-Lite master), interconnect (axilxbar), UART PHY ve axilgpio için
gereken tüm dosyalar; **hiçbirinde `localparam` yok**, hepsi Verilog-2001 / 2005. HDL Designer'a,
Libero / Synplify'a Verilog modunda olduğu gibi eklenebilir. Tek kopya: aynı modül iki klasörde
yok, klasörün tamamı birlikte derlenir.

**Bu dosyaların repodaki tek kopyası burası.** Eski yerlerindeki kopyalar (`hdl/axilxbar.v`,
`hdl/addrdecode.v`, `hdl/skidbuffer.v`, `hdl/axilgpio*.v`, `hdl/*_pk.v`, `hdl/hexbus/`,
`hdl/uart_phy/rtl/`) kaldırıldı; Studio (Interconnect Configurator), testler ve örnekler buradakileri
kullanıyor. `hdl/uart_phy/` altında yalnızca testbench'ler, ZipCPU yamaları ve belge kaldı. Studio'nun
ürettiği interconnect sarmalayıcıları da artık `localparam` içermiyor.

Orijinallerden tek fark: `localparam` → `parameter` (değerler aynı). Mantığın değişmediği her
modül için Yosys eşdeğerlik kontrolüyle ispatlandı (aşağıda).

## Klasörler

| Klasör | Dosyalar | Kaynak | Lisans |
|---|---|---|---|
| `hexbus/` | `hexbus_axil_top.v` (UART'lı üst modül), `hexbus_axil_top_pk.v` (HDL Designer paketli), `hbdechex.v` `hbpack.v` `hbexecaxi.v` `hbints.v` `hbidle.v` `hbdeword.v` `hbgenhex.v` `hbnewline.v`; ayrıca ZipCPU'nun kendi üst modülleri `hbaxil.v` (UART'sız AXI-Lite), `hbbus.v` / `hbconsole.v` / `hbexec.v` (Wishbone), `console.v` | üst modül: bu proje; hb*: ZipCPU dbgbus `a1043274` | hb*, console: LGPL-3.0 |
| `wbuart32/` | `rxuartlite.v` `txuartlite.v` (hexbus'ın UART'ı), `rxuart.v` `ufifo.v` (UART PHY'nin alıcısı ve FIFO'su) | ZipCPU wbuart32 `f43a6b8` | GPL-3.0 |
| `uart_phy/` | `axiluart_phy.v` (üst modül), `axiluart_phy_pk.v`, `axiluart_v2.v`, `txuart_v2.v`, `uart_phy_adapter.v` | AXIL Studio (axiluart_v2 / txuart_v2: wbuart32 + `hdl/uart_phy/patches`) | axiluart_v2 / txuart_v2: GPL-3.0 |
| `interconnect/` | `axilxbar.v`, `addrdecode.v` | `hdl/` (ZipCPU wb2axip) | Apache-2.0 |
| `gpio/` | `axilgpio.v`, `axilgpio_a32.v` (32 bit adres), `axilgpio_axi4.v` (tam AXI4), `axilgpio_pk.v` | `hdl/` (ZipCPU wb2axip + sarmalayıcılar) | Apache-2.0 |
| `common/` | `skidbuffer.v` (xbar, gpio ve UART ortak kullanır) | ZipCPU wb2axip | Apache-2.0 |
| `example/` | `system_top.v`, `axil_xbar.v`, `tb_system.v` | bu proje | — |

Hangi blok için hangi dosyalar:
- **hexbus:** `hexbus/*` + `wbuart32/rxuartlite.v` + `wbuart32/txuartlite.v`
- **UART PHY:** `uart_phy/*` + `wbuart32/rxuart.v` + `wbuart32/ufifo.v` + `common/skidbuffer.v`
- **interconnect:** `interconnect/*` + `common/skidbuffer.v` + Studio'nun ürettiği `axil_xbar.v`
- **GPIO:** `gpio/axilgpio.v` + seçtiğin sarmalayıcı + `common/skidbuffer.v`

## Değişenler

| Dosya | `localparam` → `parameter` | Not |
|---|---|---|
| `wbuart32/rxuartlite.v` | 18 | |
| `axilxbar.v` | 11 | biri `ifdef FORMAL` içindeki `generate` bloğunda: sabit `wire` oldu |
| `uart_phy/txuart_v2.v` | 10 | |
| `uart_phy/axiluart_v2.v`, `uart_phy_adapter.v` | 5'er | |
| `gpio/axilgpio.v` | 3 | |
| `hexbus/hexbus_axil_top.v`, `wbuart32/txuartlite.v` | 2'şer | |
| `wbuart32/rxuart.v`, `ufifo.v`, `addrdecode.v` | 1'er | `rxuart.v`: ayrıca `break_condition` `reg` yerine `wire` (orijinal `reg`'i `assign` ile sürüyor; bu yalnızca SystemVerilog'da geçerli) |

Değişen her dosyanın başında not var. **Eski localparam'lara örnek bağlarken değer verme**
(`DW`, `AW`, `LCLLGFLEN` …); yalnızca gerçek parametreleri ayarla.

## Doğrulama
- `no_localparam.py --check`: klasörde `localparam` kalmadı.
- Icarus Verilog `-g2001` ve `-g2005`: her üst modül (`hexbus_axil_top_pk`, `axiluart_phy_pk`,
  `axilxbar`, `axilgpio_pk`, `axilgpio_a32`, `axilgpio_axi4`, `system_top`) hatasız; Yosys tüm
  klasörü `-sv` olmadan okuyor.
- Yosys eşdeğerlik (`equiv_make` / `equiv_simple` / `equiv_induct`), değiştirilmemiş orijinallere
  (SystemVerilog olarak okunmuş) karşı, ispatlanan / toplam nokta:

  | Modül | Nokta | Modül | Nokta |
  |---|---|---|---|
  | hexbus_axil_top_pk | 1643 / 1643 | rxuart | 197 / 197 |
  | axiluart_phy_pk | 2333 / 2333 | rxuartlite | 56 / 56 |
  | axilxbar | 7792 / 7792 | txuartlite | 40 / 40 |
  | axil_xbar (örnek, önceki çeviri) | 2730 / 2730 | ufifo | 214 / 214 |
  | axilgpio_pk | 960 / 960 | txuart_v2 | 147 / 147 |
  | axilgpio_axi4 | 722 / 722 | uart_phy_adapter | 331 / 331 |
  | addrdecode | 141 / 141 | | |

- Sistem simülasyonu (`example/tb_system.v`, `tests/test_v2005.py`): debug UART'tan hexbus
  komutlarıyla GPIO'ya yazma ve geri okuma, UART PHY register okuma, UART PHY'den bayt gönderme,
  boş adreste DECERR (`E`).
- UART PHY'nin kendi testbench'leri (`hdl/uart_phy/tb`, `tests/test_uart_rtl.py`) bu dosyalarla:
  0 hata, 6 formatta 100 baytlık kayıpsız burst, parite standarda uygun.
- Studio'nun ürettiği interconnect (`xbargen`): `localparam` yok, buradaki çekirdeklerle `-g2005`
  derleniyor (`tests/test_v2005.py`), interconnect simülasyon testleri (`tests/part2`) geçiyor.

```
iverilog -g2005 -o sim hdl/v2005/example/tb_system.v $(find hdl/v2005 -name '*.v' ! -name 'tb_*')
vvp sim
```

## Örnek sistem (`example/`)

```
PC --UART--> hexbus_axil_top --> axil_xbar --SLAVE0--> axilgpio_a32  0x0301_0000
                                           --SLAVE1--> axiluart_phy  0x0302_0000
```

`axil_xbar.v`, Interconnect Configurator'ın bu adres haritası için ürettiği sarmalayıcı
(üretildiği gibi, çeviri gerekmedi).

## Kendi dosyaların için
Başka bir yerden gelen dosyaları aynı şekilde çevir (Studio'nun interconnect çıktısı zaten
`localparam`'sız; Studio'nun ürettiği slave IP'leri `easyaxil.v` şablonundan gelir ve gövdelerinde
Verilog-2005'te geçerli `localparam`'lar vardır, gerekirse bunlar da çevrilebilir):

```
python3 hdl/v2005/no_localparam.py yeni_dosya.v -o cikti_klasoru
python3 hdl/v2005/no_localparam.py --check $(find hdl/v2005 -name '*.v')
```

Hexbus komutlarında veri **küçük harf** onaltılık yazılır: büyük `A` adres komutudur
(`W5A` = "5 yaz, sonra adres", `W5a` = "0x5a yaz").
