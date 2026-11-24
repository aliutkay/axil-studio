# Üç PCS ile RS-485 terminal + LED

RS-485 hattından gelen her bayta `OK: <bayt>` diye cevap verilir ve LED yanıp söner (`1`..`9`
gelirse o kadar kez, başka bir bayt gelirse bir kez). İşi üç komut sıralayıcı paylaşır:

```
PC --hexbus--> SEQ_MAIN 0x0000_0000 --+--> SEQ_GPIO 0x0002_0000 --> GPIO  0x0003_0000
                                      +--> SEQ_UART 0x0004_0000 --+--> UART1 0x0005_0000
                                                                  +--> UART2 0x0005_1000
```

| Sıralayıcı | Program | Görevi |
|---|---|---|
| SEQ_UART | `seq_uart.s` (97 komut) | Alıcı UART'ı dinler, gelen bayta verici UART'tan cevap yazar, `D[EVENTS]`'i artırır |
| SEQ_GPIO | `seq_gpio.s` (50 komut) | `D[REQ]` değişince LED'i `D[N]` kez yakıp söndürür; istenirse boştayken heartbeat |
| SEQ_MAIN | `seq_main.s` (38 komut) | Diğer ikisini başlatır; SEQ_UART'ı izler, SEQ_GPIO'ya blink isteği yazar |

SEQ_UART, SEQ_GPIO'ya ulaşamaz: her sıralayıcı yalnızca kendi altındaki bus'ı görür. Aradaki
bağlantıyı SEQ_MAIN kurar; iki çocuğun D-mem'ini posta kutusu gibi kullanır.

## Hangi UART RX, hangisi TX: üstten ayar
Program yeniden yüklenmeden, çalışırken Debug Bridge'den değiştirilir (SEQ_UART'ın D-mem'i):

| Adres | Ne | Varsayılan (0 yazılıysa) |
|---|---|---|
| `0x0004_0400` | alıcı UART'ın adresi | `0x0005_0000` (UART1) |
| `0x0004_0404` | verici UART'ın adresi | `0x0005_1000` (UART2) |
| `0x0004_0408` | alıcının PHY_CTRL'u | `0x80` (RX açık, TX kapalı, DE düşük) |
| `0x0004_040C` | vericinin PHY_CTRL'u | `0x42` (TX açık, RX kapalı, DE yalnız gönderirken) |

**Ters çevirmek için:** `0x0004_0400` ← `0x0005_1000`, `0x0004_0404` ← `0x0005_0000`. Program
değişikliği bir sonraki baytta görür. Önce yeni alıcının sürücüsünü kapatır, sonra vericiyi açar;
iki sürücü hiç aynı anda sürmez. RS-485'in DE'si sürekli açık olsun istersen vericinin PHY_CTRL'u
için `0x41` yaz.

Diğer ayarlar ve okunabilecek sayaçlar:

| Adres | Ne |
|---|---|
| `0x0004_0410` / `0x0004_0414` / `0x0004_0418` | cevaplanan bayt sayısı / son bayt / parite-çerçeve hatası sayısı |
| `0x0004_0480`.. | cevap metni, word başına bir karakter, 0 ile biter (0 = `OK: `) |
| `0x0002_0404` | istek başına blink sayısı (SEQ_MAIN yazar) |
| `0x0002_0408` | yanıp sönen LED'ler (maske, 0 = LED 0) |
| `0x0002_040C` / `0x0002_0410` | yanık / sönük süre, çevrim (0 = 3 000 000 = 50 MHz'de 60 ms) |
| `0x0002_0418` / `0x0002_041C` | heartbeat LED maskesi (0 = kapalı; örn. 2 = LED 1) / yarım periyot (0 = 0,5 s) |
| `0x0000_0400` / `0x0000_0404` | SEQ_MAIN: iletilen istek sayısı / son bayt |

CR ve LF cevaplanmaz (terminalde Enter). Baud, UART'ların SETUP'ı: `axiluart_phy_pk`'de
`INITIAL_SETUP = 434` → 50 MHz'de 115200 8N1. Terminali 115200 8N1 ayarla.

## Çalıştırma (Studio)
1. `rs485_terminal.axproj.json`'u aç. Adres haritası donanımınla aynı.
2. Debug Bridge → bağlan. SEQ_GPIO, SEQ_UART ve SEQ_MAIN satırlarına sırayla çift tıkla; her
   pencerede kendi `.s` dosyasını yapıştır → Assemble → **Load**. Run'a basma.
3. SEQ_MAIN penceresinde **PT_WR_RUN**'ı işaretle (CTRL = 0x61) ve **Run**. SEQ_MAIN, SEQ_GPIO
   ve SEQ_UART'ı kendisi başlatır (PT_WR_RUN ile, programı yüklüyse ve çalışmıyorsa).
4. Terminalden yaz: her karaktere `OK: x` gelir, LED yanıp söner.

PT_WR_RUN neden gerekli: PC'nin alt bloklara yazmaları (UART ayarı, rol değiştirme, çocukların
D-mem'i) çalışan SEQ_MAIN'in içinden geçer. PT_WR_RUN = 0 iken bunlar SLVERR alır. SEQ_MAIN
çocukları da PT_WR_RUN ile başlatır; UART arayüzünden UART'lara erişebilmen için.

## HDL (HDL Designer)
`hdl/` içinde Interconnect Configurator'ın bu proje için ürettikleri:

| Dosya | Ne |
|---|---|
| `seq_main_cmd_seq_pk.v`, `seq_gpio_cmd_seq_pk.v`, `seq_uart_cmd_seq_pk.v` | Her sıralayıcının kendi bloğu; SELF_ID içinde (0x00, 0x02, 0x04), parametre / generic yok |
| `axil_xbar_SEQ_MAIN_pk.v` (+ `.v`) | SEQ_MAIN'in altındaki 1×2 interconnect (SEQ_GPIO, SEQ_UART) |
| `axil_xbar_SEQ_UART_pk.v` (+ `.v`) | SEQ_UART'ın altındaki 1×2 interconnect (UART1, UART2) |

Üç sıralayıcının **üçü de `hdl/cmd_seq_pk.v`'yi kullanamaz**: o blok SELF_ID = 0 ile üretilmiş.
SEQ_GPIO ve SEQ_UART kendi register'larına cevap vermez. Her sıralayıcıya kendi `seq_*_cmd_seq_pk`
bloğunu koy. Interconnect Configurator artık bunları her komut sıralayıcı alt sistem için
`<alt sistem>_cmd_seq_pk.v` olarak üretiyor. `_inst.v` dosyaları şablondur, derlemeye ekleme.

Bağlantı: `seq_main` `MASTER_AXI` → `axil_xbar_SEQ_MAIN_pk` `MASTER0`; `SLAVE0` → `seq_gpio`
`SLAVE_AXI`, `SLAVE1` → `seq_uart` `SLAVE_AXI`; `seq_gpio` `MASTER_AXI` → `axilgpio_pk`;
`seq_uart` `MASTER_AXI` → `axil_xbar_SEQ_UART_pk` `MASTER0`; `SLAVE0` → UART1, `SLAVE1` → UART2.
Çekirdekler: `hdl/cmd_seq/rtl/cmd_seq.v`, `cmd_seq_core.v` ve `hdl/v2005` (interconnect, gpio,
uart_phy, wbuart32, common).

## Simülasyon
`sim/run_sim.py` (testlerde de çalışır) bu tasarımı yukarıdaki bloklarla kurar ve gerçek RS-485
baytlarıyla dener:
- UART1'e `3` → UART2'den `OK: 3` CR LF, LED 3 kez; `A` → 1 kez; CR → cevap yok. UART1 DE sürmez.
- PC çalışırken rolleri değiştirir (SEQ_MAIN'in içinden) → UART2'ye `2` → UART1'den `OK: 2`, LED
  2 kez; artık UART1 sürer.
- Sayaçlar, bus hatası yok, üç sıralayıcı hâlâ çalışıyor.

```
python3 examples/cmd_seq_rs485/sim/run_sim.py              # UART bit başına 16 çevrim, ~5 s
python3 examples/cmd_seq_rs485/sim/run_sim.py --real-baud  # 434 (115200 @ 50 MHz), ~1 dk
```

Not: ZipCPU'nun alıcısı resetten sonra hattı ~16 bit süresi boşta görene kadar bayt almaz.
Kart açılır açılmaz gelen ilk bayt kaybolabilir; terminalden yazarken fark edilmez.
