# IP arayüzleri

Bir **IP arayüzü**, tek bir IP türü için yazılmış ayrı bir uygulamadır (ör. axiluart_phy için hat ayarları,
terminal ve donanım doğrulaması). AXIL Studio'nun içinde çalışmaz: UART-to-AXI Debug Bridge onu ayrı bir süreç
olarak başlatır, arayüz de Debug Bridge'e bilgisayarın içindeki yerel bir servis üzerinden bağlanır. Kart erişimi
her zaman Debug Bridge'in bağlantısından ve istek kuyruğundan geçer; arayüz seri portu kendisi açmaz ve
AXIL Studio olmadan çalışmaz.

| Klasör | İçerik |
|---|---|
| `sdk/axil_client.py` | Python istemcisi (tek dosya, yalnızca standart kütüphane) |
| `axiluart_phy/` | axiluart_phy'nin UART arayüzü (AXIL Studio ile birlikte gelen örnek arayüz) |

## Bir IP'yi arayüzüne bağlamak

IP'nin register haritası arayüzün **kimliğini** taşır: Register Map Editor → IP paneli → *IP arayüzü*
(`IPBlock.panel`, regspec'te `"panel"`). Kimlik projede ve IP kütüphanesinde IP ile birlikte saklanır; arayüzün
dosya yolu hiçbir yerde yazmaz.

## Arayüzün tanıtım dosyası (`axil-interface.json`)

Arayüz uygulamasının klasöründe bulunur:

```json
{
  "format": "axil-interface",
  "protocol": 1,
  "id": "axiluart_phy",
  "version": "1.0.0",
  "title": {"en": "UART interface", "tr": "UART arayüzü"},
  "desc": {"en": "...", "tr": "..."},
  "command": ["{python}", "uart_ui.py"]
}
```

- `command`: başlatılacak program ve argümanları; manifest'in klasöründe çalıştırılır. `{python}` AXIL Studio'yu
  çalıştıran Python'dur (başka bir Python ya da derlenmiş bir program da olabilir); `{folder}` manifest'in klasörü.
  Klasörde bulunan göreli dosya adları tam yola çevrilir.
- `title` / `desc`: düz metin ya da dil kodlarına göre sözlük.
- `protocol`: arayüzün konuştuğu servis protokolü; AXIL Studio farklı bir sürümü açmaz.

## Arayüz nerede aranır

Aynı kimlik birden çok yerde bulunursa ilk bulunan kullanılır:

1. **IP arayüzü klasörleri** (Debug Bridge → *Debug* menüsü → *IP arayüzü klasörleri…*): kullanıcının eklediği
   klasörler; geliştirilen bir sürüm böylece öne geçer.
2. Açık **IP kütüphanelerinin klasörleri**: kütüphane ile arayüzü aynı klasörde dağıtılırsa ayar gerekmez.
3. AXIL Studio'nun `interfaces/` klasörü.

Her klasör iki alt seviyeye kadar taranır. Bulunamayan bir arayüz için Debug Bridge aranan kimliği yazar ve klasör
ayarlarını açmayı önerir.

## Başlatma

Debug Bridge'de arayüzü olan IP seçilince register panelinde arayüzün düğmesi çıkar (IP'ye çift tık ya da sağ tık
da açar). Arayüz zaten çalışıyorsa yenisi başlatılmaz, penceresi öne getirilir (`raise` olayı). AXIL Studio
başlatırken ortam değişkenlerini verir:

| Değişken | Anlamı |
|---|---|
| `AXIL_STUDIO_PORT` | Servisin TCP portu (yalnızca 127.0.0.1) |
| `AXIL_STUDIO_TOKEN` | Bu arayüz oturumunun anahtarı (hangi IP için açıldığını belirler) |
| `AXIL_STUDIO_LANG` | Arayüz dili (`en`, `tr`) |
| `PYTHONPATH` | Başına `interfaces/sdk` eklenir: `import axil_client` doğrudan çalışır |

Arayüzün standart hata çıktısı toplanır; sıfırdan farklı bir çıkış kodunda son satırları Debug Bridge'in trafik
günlüğüne yazılır. AXIL Studio kapanırken `closing` olayı gönderilir; kapanmayan arayüz sonlandırılır.

## Protokol (sürüm 1)

TCP üzerinden satır başına bir JSON nesnesi (UTF-8). Arayüz istek gönderir, AXIL Studio istekleri geliş sırasıyla
yanıtlar ve olayları her an gönderebilir:

```
→ {"id": 1, "op": "hello", "token": "...", "protocol": 1, "client": "..."}
← {"id": 1, "ok": true, "result": {"protocol": 1, "studio": "0.1.23", "ip": {...}, "connected": true,
                                   "port": "COM5", "lang": "tr", "theme": {"dark": false, "tokens": {...}},
                                   "settings": {...}}}
→ {"id": 2, "op": "read", "offset": 8}
← {"id": 2, "ok": true, "result": 256}
← {"event": "connection", "connected": false, "port": ""}
→ {"id": 3, "op": "write", "offset": 16, "value": 198}
← {"id": 3, "ok": false, "error": {"code": "not_connected", "message": "..."}}
```

`ip`: IP'nin adı, taban adresi (`base`, sayı), açıklaması, sürümü, `panel` kimliği ve register haritası
(`registers`: ad, offset, erişim, reset, bit alanları, değer anlamları).

| İstek | Alanlar | Sonuç |
|---|---|---|
| `hello` | `token`, `protocol` | Yukarıdaki bilgiler (ilk istek olmalı) |
| `read` | `offset` (IP tabanından) ya da `addr` (mutlak); `quiet` | Okunan 32 bit |
| `write` | `offset` / `addr`, `value`; `quiet` | — |
| `batch` | `ops`: `[{"op": "read"\|"write", "offset"\|"addr", "value"}]`; `quiet` | Okunanlar listesi (yazmalar için `null`) |
| `read_until` | `offset` / `addr`, `mask`, `value`, `max` (≤ 4096) | `(kelime & mask) == value` olan ilk kelimeye kadar okunanlar (FIFO boşaltma) |
| `lock` / `unlock` | — | Bağlantıyı tutar: Debug Bridge'in canlı izlemesi durur, Bağlan ve register paneli kilitlenir; başka arayüzlerin istekleri `busy` alır. Arayüzün bağlantısı koparsa kilit kendiliğinden kalkar |
| `ip` | — | Güncel IP bilgisi |
| `settings_get` / `settings_set` | `values` (JSON nesnesi) | Bu IP örneği için projede saklanan değerler (ör. ACLK) |
| `show_register` | `register` | Debug Bridge'de register'ı seçer ve öne getirir |
| `add_config` | `rows`: `[{"register", "value"}]` | Debug Bridge config listesine satır ekler |
| `status` / `log` | `text` | Durum çubuğuna / trafik günlüğüne yazar |

`quiet`: erişim Debug Bridge'in trafik günlüğüne yazılmaz ve okunan değer ekranlarına işlenmez (periyodik okuma
için). `read_until` varsayılan olarak sessizdir.

Hata kodları: `not_connected` (Debug Bridge karta bağlı değil), `busy` (bağlantıyı başka arayüz tutuyor), `bus`
(erişim başarısız: zaman aşımı, AXI hatası), `ip_removed` (IP adres haritasından çıkarıldı), `bad_request`,
`unknown_op`, `bad_token`, `no_session`.

Olaylar: `connection` (`connected`, `port`), `ip` (IP'nin adresi ya da haritası değişti), `ip_removed`, `theme`
(AXIL Studio'nun renkleri; arayüz kendini buna göre boyayabilir), `raise` (pencereyi öne getir), `closing`.

## Python istemcisi

```python
from axil_client import AxilClient, AxilError, Link

client = AxilClient.from_env("benim arayüzüm")
print(client.ip["name"], hex(client.ip["base"]))
client.on_event = lambda ev: print(ev)          # okuyucu iş parçacığında çağrılır
value = client.read(0x08)
client.write(0x0C, 0x41)
client.batch([("write", 0x14, 0x00FA0032), ("write", 0x10, 0xC2), ("read", 0x18)])
client.lock()
try:
    ...                                          # uzun bir test
finally:
    client.unlock()
```

Çağrılar yanıt gelene kadar bekler ve birden çok iş parçacığından kullanılabilir. Arayüz penceresi açılmadan gelen
olaylar `on_event` atanınca teslim edilir. `Link(client)` mutlak adreslerle `read(addr)` / `write(addr, value)`
bekleyen kodlar içindir. Python dışında bir dille yazılan arayüz aynı protokolü doğrudan konuşur.
