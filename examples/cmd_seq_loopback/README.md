# UART loopback testi: üç PCS, OK / FAIL LED'leri

Donanım `examples/cmd_seq_rs485` ile **aynı**: aynı proje (`rs485_terminal.axproj.json`), aynı
adresler, aynı `hdl/` blokları. Yalnızca üç program farklı. FPGA'yı yeniden sentezlemeden, yalnızca
programları yükleyerek terminal örneği ile bu test arasında geçiş yapılabilir.

```
PC --hexbus--> SEQ_MAIN 0x0000_0000 --+--> SEQ_GPIO 0x0002_0000 --> GPIO  0x0003_0000  LED 0 = OK, LED 1 = FAIL
                                      +--> SEQ_UART 0x0004_0000 --+--> UART1 0x0005_0000 --+
                                                                  +--> UART2 0x0005_1000 --+  dışarıdan loopback
```

## Kablo
Varsayılan, çapraz kablo:
- **yol A:** UART1 TX → UART2 RX
- **yol B:** UART2 TX → UART1 RX

Her tur iki yönü de dener. RS-485 4 telli / RS-422 hatta bu, iki transceiver'ın TX çiftini
karşıdakinin RX çiftine bağlamak demek. Her UART kendi TX'ini kendi RX'ine bağlıyorsa SEQ_UART'ın
ayarlarını `TX_A = RX_A = 0x0005_0000` ve `TX_B = RX_B = 0x0005_1000` yap (aşağıda).

## Ne yapar
| Sıralayıcı | Program | Görevi |
|---|---|---|
| SEQ_UART | `seq_uart.s` (177 komut) | Test motoru. Bir turda her yol için `LEN` bayt üretip gönderir; aynı diziyi kendisi de üretip alınanla karşılaştırır. Yanlış bayt, kayıp bayt (zaman aşımı), parite / çerçeve hatası, turdan önce gelen beklenmedik baytı sayar ve ilk hatanın yerini kaydeder. |
| SEQ_MAIN | `seq_main.s` (83 komut) | Diğer ikisini başlatır. Turları zamanlar (`GO` yazar, `DONE`'ı bekçi süresiyle bekler), sonucu SEQ_GPIO'ya taşır, toplamları tutar. |
| SEQ_GPIO | `seq_gpio.s` (63 komut) | LED'ler. Başarılı turda **LED 0** yanar, başarısızda **LED 1** yanar. Yanan LED her turda bir an söner; test çalışıyor mu, takıldı mı buradan anlaşılır. Açılışta ikisi birlikte kısa bir süre yanar (lamba testi). |

Veri: varsayılan olarak sözde rastgele (xorshift32). Dizi turdan tura devam eder, her tur başka
baytlar gider. İstenirse 00 01 02 … sayacı seçilebilir.

## Çalıştırma (Studio)
1. `examples/cmd_seq_rs485/rs485_terminal.axproj.json`'u aç ve Debug Bridge'den bağlan.
2. Sırayla SEQ_GPIO, SEQ_UART, SEQ_MAIN satırına çift tıkla. Her pencereye bu klasördeki kendi `.s`
   dosyasını yapıştır → Assemble → **Load**.
3. SEQ_MAIN penceresinde **PT_WR_RUN**'ı işaretle (CTRL = 0x61) ve **Run**.

Yarım saniyede bir tur koşar. Kablo sağlamsa LED 0 yanar ve her turda bir an söner. Kabloyu
çıkarınca LED 1 yanar; takınca LED 0'a döner.

## Sonucu okumak (Debug Bridge)
**SEQ_MAIN:**

| Adres | Ne |
|---|---|
| `0x0000_0400` | tur sayısı |
| `0x0000_0404` | son turun RESULT'u (0 = geçti) |
| `0x0000_0408` | başarısız tur sayısı |
| `0x0000_040C` | son turun süresi (çevrim) |
| `0x0000_0424` | durum: 1 test ediyor, 2 `MAX` tur bitti |

**RESULT bitleri:**

| Bit | Değer | Anlamı |
|---|---|---|
| b0 | 0x01 | yanlış bayt geldi |
| b1 | 0x02 | bayt(lar) gelmedi: zaman aşımı, kablo kopuk ya da baud uyuşmuyor |
| b2 | 0x04 | parite / çerçeve hatası |
| b3 | 0x08 | gönderen UART baytları almadı (TX FIFO dolu kaldı: TX kapalı ya da CTS) |
| b4 | 0x10 | SEQ_UART bekçi süresi içinde cevap vermedi (çalışmıyor mu?) |
| b5 | 0x20 | turdan önce hatta beklenmedik bayt vardı (gürültü, yüzen hat) |

**SEQ_UART:**

| Adres | Ne |
|---|---|
| `0x0004_0438` / `043C` / `0440` | doğru / yanlış / kayıp bayt sayısı |
| `0x0004_0444` / `0448` | parite-çerçeve hatası / beklenmedik bayt sayısı |
| `0x0004_044C` | ilk hatanın yolu (1 = A, 2 = B) |
| `0x0004_0450` | ilk hatanın bayt sırası |
| `0x0004_0454` | beklenen bayt |
| `0x0004_0458` | gelen bayt (`0x100` = hiç gelmedi) |
| `0x0004_045C` | ilk hatanın tur numarası |

Örnek: `044C = 1`, `0450 = 0`, `0458 = 0x100` → "yol A'da ilk bayt hiç gelmedi": UART1 TX →
UART2 RX teli. `0454 = 0x1F`, `0458 = 0x17` → bit 3 bozuk geliyor.

## Ayarlar (çalışırken yazılabilir, 0 = varsayılan)
**SEQ_UART** (bir sonraki turda geçerli):

| Adres | Ne | Varsayılan |
|---|---|---|
| `0x0004_0400` / `0404` | yol A: gönderen / alan UART | UART1 / UART2 |
| `0x0004_0408` / `040C` | yol B: gönderen / alan UART | UART2 / UART1 |
| `0x0004_0410` | 1 = yalnız yol A | iki yol |
| `0x0004_0414` | yol başına bayt | 32 |
| `0x0004_0418` | hiçbir bayt gitmeden / gelmeden geçebilecek süre (çevrim) | 1 000 000 (50 MHz'de 20 ms) |
| `0x0004_041C` | UART'lara yazılan PHY_CTRL | `0xC1` (TX + RX açık, DE hep açık) |
| `0x0004_0420` | 0 = sözde rastgele, 1 = sayaç | 0 |
| `0x0004_0424` | dizinin durumu (tohum) | `0x2545_F491` |

**SEQ_MAIN:**

| Adres | Ne | Varsayılan |
|---|---|---|
| `0x0000_0410` | turlar arası bekleme (çevrim) | 25 000 000 (0,5 s) |
| `0x0000_0414` | `MAX`: bu kadar tur koşup durur, LED'ler son sonucu gösterir | 0 = sürekli |
| `0x0000_041C` | `MAX` tur bittikten sonra 1 yaz: yeniden `MAX` tur | |
| `0x0000_0420` | SEQ_UART'ı bekleme süresi | 100 000 000 (2 s) |

`MAX` başlangıçta ve `0x0000_041C` yazılınca okunur. Önce `MAX`'ı yaz, sonra SEQ_MAIN'i başlat.

**SEQ_GPIO:**

| Adres | Ne | Varsayılan |
|---|---|---|
| `0x0002_0408` / `040C` | OK / FAIL LED maskesi | LED 0 / LED 1 |
| `0x0002_0410` | `HOLD`: 1 = bir hata görülünce FAIL LED'i sonraki turlar geçse de yanık kalır | 0 |
| `0x0002_0414` | `HELD`: 1 = tutulan bir hata var; 0 yaz → bırakır | |
| `0x0002_0418` | yanan LED'in her turda sönük kaldığı süre | 2 500 000 (50 ms) |

Uzun süreli test için `HOLD = 1`: gece boyunca tek bir hata olsa bile sabah FAIL LED'i yanık
bulursun. Ayrıntısı `0x0004_044C..045C`'te.

## Simülasyon
`sim/run_loopback.py` (testlerde de çalışır) tasarımı RS-485 örneğiyle aynı bloklarla kurar.
Çapraz kabloyu testbench içinde modeller ve şunları dener:
- kablo sağlam: turlar geçer, LED 0 yanar, LED 1 söner, her bayt doğru;
- UART1 → UART2 teli kesik: RESULT = 0x02, LED 1 yanar, ilk hata "yol A, bayt 0, hiç gelmedi";
- tel geri takılınca: LED 0;
- `HOLD = 1` iken yol B'de tek bit bozulur: LED 1 yanar, sonraki turlar geçse de yanık kalır;
  `HELD = 0` yazılınca LED 0'a döner;
- bus hatası yok, üç sıralayıcı da çalışıyor.

```
python3 examples/cmd_seq_loopback/sim/run_loopback.py              # UART bit başına 16 çevrim, ~25 s
python3 examples/cmd_seq_loopback/sim/run_loopback.py --real-baud  # 434 (115200 @ 50 MHz)
```
