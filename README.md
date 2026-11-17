# gta-tr-subs — PS5 oyunlarına anlık Türkçe altyazı

PS5'te oynadığın oyunun İngilizce altyazısını PC ekranından okur, Türkçeye çevirir ve çeviriyi orijinal
altyazının **tam üstüne** bindirir. GTA 6 için tasarlandı; İngilizce altyazısı olan her oyunda çalışır.

Oyun görüntüsü PC'ye PS Remote Play, chiaki-ng ya da capture kartı + OBS ile gelir. Program o pencereyi
yakalar, altyazı bölgesini OCR ile okur, çeviriyi şeffaf ve tıklamayı geçiren bir overlay'de gösterir.
Windows 11 gerekir.

- **Hızlı:** daha önce görülen satır ~85 ms'de, yeni satır ~430 ms'de ekranda (ölçümler aşağıda).
- **İnternetsiz de çalışır:** yerel model (Ollama) her zaman hazırdır; bulut yanıt vermezse takılma olmaz.
- **Sansürsüz, tona sadık:** küfür ve argo yumuşatılmaz, ama kaynakta olmayan küfür de eklenmez.
- **Özel isimler korunur:** `glossary.yaml` içindeki isimler (Lucia, Jason, Vice City…) çevrilmez.

## Hızlı başlangıç

Kurulum bittikten sonra (aşağıda) proje klasöründe:

| Ne için | Çift tıkla | veya terminalde |
|---|---|---|
| Denemek, ayarlamak, gecikmeyi görmek | `test-paneli.bat` | `.venv\Scripts\gta-tr-subs.exe --ui` |
| Oynarken kullanmak (yalnızca overlay) | `baslat.bat` | `.venv\Scripts\gta-tr-subs.exe` |

İlk kez deniyorsan test paneliyle başla.

## Kurulum

1. **Python ortamı.** [uv](https://docs.astral.sh/uv/) kur (`python -m pip install uv`), sonra proje klasöründe:

   ```bash
   python -m uv sync
   ```

   Bu, Python 3.12'yi ve tüm bağımlılıkları `.venv` klasörüne kurar.

2. **Windows İngilizce OCR paketi.** Yönetici PowerShell'de:

   ```powershell
   Add-WindowsCapability -Online -Name "Language.OCR~~~en-US~0.0.1.0"
   ```

   (Ya da Ayarlar → Saat ve dil → Dil ve bölge → Dil ekle → English (United States).) Paket yoksa program
   açılışta ne yapman gerektiğini söyleyen bir hata gösterir.

3. **Ollama ve yerel model (ücretsiz, internetsiz çalışır).** [ollama.com](https://ollama.com)'dan Ollama'yı kur, sonra:

   ```bash
   ollama pull translategemma:4b
   ```

   Model ~3.3 GB iner, ekran kartında ~3 GB yer tutar. Program açılışta modeli yükler; diskten ilk yükleme
   ~40 sn sürebilir.

4. **Gemini API anahtarı (opsiyonel ama önerilir: çeviri kalitesi belirgin daha iyi).**
   [aistudio.google.com/apikey](https://aistudio.google.com/apikey) adresinden anahtar al ve kaydet:

   ```powershell
   setx GEMINI_API_KEY "anahtarın"
   ```

   Ardından açık terminalleri kapatıp yeniden aç. Anahtar yalnızca bu ortam değişkeninden okunur; hiçbir
   dosyaya ya da loga yazılmaz. Anahtar yoksa program yalnızca yerel modelle çalışır.

   > Ücretsiz katmanda sınır **dakikada 15 istek**tir. Program buna göre ayarlıdır: sınıra yaklaşınca o satırı
   > yerel modelle çevirir. Ücretli katmana geçersen `config.toml` içinde `requests_per_minute = 0` yap.

## PS5'i bağlama

**PS Remote Play ya da chiaki-ng (ek donanım gerekmez)**

1. PS5'te **Ayarlar → Sistem → Uzaktan Oynama → Uzaktan Oynamayı Etkinleştir**.
2. PC'de [PS Remote Play](https://remoteplay.dl.playstation.net/remoteplay/) ya da
   [chiaki-ng](https://github.com/streetpea/chiaki-ng) ile PS5'e bağlan. Çözünürlüğü 1080p yap.
3. Mümkünse PS5'i ve PC'yi kabloyla (Ethernet) bağla; Wi-Fi'da oyun gecikmesi hem yüksek hem dalgalı olur.
4. Pencereyi **simge durumuna küçültme** (görüntü yakalanamaz). Başka pencerenin arkasında kalması sorun değil.
5. Pencereli ya da kenarlıksız tam ekran kullan; *exclusive fullscreen* overlay'i gizleyebilir.

**Capture kartı + OBS**

1. PS5'te **Ayarlar → Sistem → HDMI → HDCP'yi Etkinleştir**'i kapat (açıkken kart siyah görüntü verir).
2. OBS'te kaynağa sağ tıkla → *Windowed Projector*; programda o pencereyi seç.

Oyunda altyazıları açmayı unutma. Standart beyaz yazı en iyi sonucu verir.

## Kullanım

### Test paneli

`test-paneli.bat` → tek pencerede her şeyi görürsün.

1. **Kaynak** listesinden oyunun penceresini seç (★ = otomatik öneri). Oyun açık değilse "Sentetik test
   videosu"na bas; program kendi ürettiği altyazılı videoyla çalışır.
2. **Başlat**'a bas. Sol üstte görüntü akmalı.
3. Ekranda bir altyazı varken **Altyazı bölgesini seç…**'e bas ve altyazının çıktığı alanı fareyle çiz.
   2–3 satırlık altyazı sığacak kadar olsun; gereğinden büyük seçme.
4. İzle:

| Bölüm | Ne gösterir |
|---|---|
| Önizleme | Yakalanan kare ve yeşil altyazı bölgesi çerçevesi |
| Alttaki şerit | OCR'a giden maske; altyazı harfleri **yeşil** görünmeli |
| Şu anki satır | Okunan İngilizce, Türkçe çeviri, katman (cache / bulut / yerel) ve gecikme |
| Geçmiş | Her satırın kararlı olma süresi, ilk çevirinin katmanı ve gecikmesi (yeşil = hedefin altında), bulut sonradan yerine geçtiyse onun gecikmesi; altta p50/p95; CSV'ye kaydedilir |
| İstatistik | fps, OCR ve çeviri süreleri, devre kesici, bulut hata / zaman aşımı / kota sayaçları |
| Elle çeviri testi | Yazdığın İngilizce satırı her katmandan geçirir, sonucu ve süresini gösterir |

"Çeviri" kutusundaki ayarlar çalışırken değişir: mod, overlay aç/kapa, İngilizceyi göster, internet kesintisi
simülasyonu. **"Sahte çevirmen" çeviri yapmaz**, İngilizceyi geri verir; yalnızca ağ ve model olmadan hız
ölçmek içindir.

### Overlay (oynarken)

`baslat.bat` → ilk açılışta pencereyi ve altyazı bölgesini sorar, sonra yalnızca overlay çalışır.

| Kısayol | İşlev |
|---|---|
| `F8` | Çeviriyi aç / kapa (kapalıyken OCR ve çeviri de durur) |
| `F9` | Altyazı bölgesini yeniden seç |
| `F10` | Orijinal İngilizceyi küçük puntoyla göster / gizle |
| `F11` | Çeviri modu: yarış → bulut → yerel |
| `F12` | Debug paneli: canlı önizleme, fps, süreler, aktif katman |

Çıkış: sistem tepsisindeki **TR** simgesi → Çıkış. Kısayollar `config.toml` → `[hotkeys]` altından değişir.
Tuşlar oyuna/uygulamaya da iletilir; chiaki-ng'de `F11` tam ekran geçişiyse birini değiştir.

Çeviri kutusunun köşesindeki küçük etiket çevirinin nereden geldiğini gösterir: `cache`, `bulut`, `yerel`.

### Komut satırı seçenekleri

```bash
.venv\Scripts\gta-tr-subs.exe --ui                 # test paneli
.venv\Scripts\gta-tr-subs.exe --pick-window        # pencere seçimini yeniden sor
.venv\Scripts\gta-tr-subs.exe --calibrate          # açılışta altyazı bölgesini yeniden seç
.venv\Scripts\gta-tr-subs.exe --mode local_only    # race | cloud_first | local_only
.venv\Scripts\gta-tr-subs.exe --console            # overlay yok; okunan satırı ve çeviriyi konsola yaz
.venv\Scripts\gta-tr-subs.exe --source video.mp4   # kare kaynağı olarak video
.venv\Scripts\gta-tr-subs.exe --list-windows       # yakalanabilir pencereleri listele
```

## Ayarlar

| Dosya | İçerik |
|---|---|
| `config.toml` | Kişisel ayarların. İlk çalıştırmada `config.example.toml`'dan oluşturulur; seçtiğin pencere ve altyazı bölgesi buraya kaydedilir. Git'e girmez. Her alan yorumla açıklanmıştır. |
| `glossary.yaml` | `keep:` çevrilmeyecek özel isimler, `terms:` sabit terim karşılıkları. İstediğin gibi düzenle. |
| `translations.db` | Kalıcı çeviri önbelleği. Silersen sıfırlanır. |

En sık dokunulan ayarlar:

| Ayar | Ne zaman değiştirilir |
|---|---|
| `[translate] mode` | `race` (varsayılan: bulut ve yerel yarışır), `cloud_first`, `local_only` |
| `[translate.gemini] requests_per_minute` | Ücretli Gemini katmanında `0` yap |
| `[translate.ollama] model` | Ekran kartın 12 GB+ ise `translategemma:12b` (daha kaliteli, daha yavaş) |
| `[detect] bright_threshold` | Altyazı soluksa (HDR akışı) düşür, örn. `140` |
| `[overlay] opacity`, `font_family` | Kutunun opaklığı ve yazı tipi |

## Çeviri nasıl çalışır

1. **Önbellek.** Satır daha önce çevrildiyse anında gösterilir. Oynadıkça dolar; tekrar eden replikler anlık olur.
2. **Yarış (varsayılan).** Gemini ve yerel model aynı anda başlar, ilk gelen gösterilir. Gemini sonra gelirse ve
   altyazı hâlâ ekrandaysa çeviri onunkiyle değişir ve önbelleğe o yazılır.
3. **Bulut sorunluysa.** İki ardışık hata/zaman aşımında bulut 30 sn atlanır, sonra tek istekle yoklanır. Kota
   dolarsa (429) API'nin bildirdiği süre kadar beklenir. Bu sürede çeviri yerel modelden gelir.
4. **Bağlam.** Son 3 satır Gemini'ye bağlam olarak gider; hitap (sen/siz) ve zamirler tutarlı kalır.

Yerel model hızlıdır ama çevirisi daha zayıftır: resmî ("siz") konuşur, küfürü yumuşatır, ara sıra hata yapar.
Kalite için Gemini anahtarı önerilir.

## Sorun giderme

| Belirti | Çözüm |
|---|---|
| `No module named uv` | `uv`'ye gerek yok: `baslat.bat` / `test-paneli.bat` kullan ya da `.venv\Scripts\gta-tr-subs.exe` çalıştır. |
| "Windows OCR için 'en-US' dil paketi kurulu değil" | Kurulum 2. adımdaki komutu yönetici PowerShell'de çalıştır. |
| Çeviri hep `yerel` geliyor | Anahtar görünmüyor olabilir: `setx`'ten sonra terminali yeniden aç. İstatistik sekmesinde "429" ya da "kota nedeniyle atlanan" artıyorsa ücretsiz kotaya takılıyorsun. |
| Türkçe yerine İngilizce görünüyor | Test panelinde "Sahte çevirmen" işaretli; kaldırıp yeniden başlat. |
| Önizleme siyah | Pencere simge durumunda olmasın. Yine siyahsa `config.toml` → `[capture] backend = "monitor"` dene ya da chiaki-ng kullan. |
| Altyazı okunmuyor | Alttaki şeritte harfler yeşil mi? Değilse `bright_threshold`'u düşür. Her yer yeşilse yükselt ya da `[detect] require_outline = true` yap. Bölgeyi daha dar seç. |
| Çeviri geç geliyor | İstatistik sekmesinde hangi aşamanın uzun sürdüğüne bak. İlk dakikada yerel model ısınıyor olabilir. |
| Overlay görünmüyor | Oyun penceresi *exclusive fullscreen* olmasın. `F8` ile kapatılmış olabilir. |
| Overlay ekran kaydında/yayında görünmüyor | Bilerek böyle: overlay kendi çevirisini okumasın diye tüm ekran yakalamalarından gizlenir. |

Ayrıntılı kayıtlar `logs/gta-tr-subs.log` dosyasındadır.

## Performans

Geliştirme makinesi: Windows 11, RTX 3070. Sentetik 1080p/30 fps video, gerçek Gemini (ücretsiz katman) ve
gerçek Ollama (`translategemma:4b`).

| Ölçüm | Sonuç | Hedef |
|---|---|---|
| Yeni satır, ilk tam çeviri | p50 430–454 ms, p95 527–534 ms | p50 < 600, p95 < 1200 |
| Önbellekteki satır | p50 84 ms, p95 136 ms | < 150 ms |
| Altyazı kayboldu → overlay gizlendi | p50 243 ms, en fazla 277 ms | ≤ 300 ms |
| OCR karakter doğruluğu (Windows OCR) | %100 (1080p ve 720p, 52 görüntü) | ≥ %97 |
| OCR süresi | ortalama 8.7 ms | — |
| Yerel model, tek başına | ortalama 168 ms (ilk kelime 51 ms) | — |
| Gemini, tek başına | p50 644 ms, p95 867 ms | — |
| Bellek (6 dk, 4x hız ≈ 24 dk içerik) | 683–694 MB bandında, artış yok | sızıntı yok |

Bu sayıları okurken:

- Yeni satır hedefi Gemini sayesinde değil, yerel modelin önce gelmesi sayesinde tutuyor. Gemini tek başına
  hedefin üstünde; ücretsiz katmanda ara sıra ilk yanıtı saniyelerce geciktiriyor.
- Gecikmenin ~100 ms'si oyunun kendi altyazı belirme animasyonudur; kısaltılamaz.
- OCR doğruluğu temiz, sentetik yazıyla ölçüldü. Gerçek Remote Play akışında sıkıştırma sonucu düşürebilir.
- Gerçek PS Remote Play penceresiyle 6 dakikalık bir oturumda 45 satır çevrildi (9 bulut, 36 yerel). O oturumda
  gecikme ve doğruluk ölçülmedi.
- 2 saatlik kesintisiz bellek testi yapılmadı.

Kendi ölçümün için test panelindeki Geçmiş sekmesini ya da aşağıdaki araçları kullan.

## Bilinen sınırlar

- **Çok parlak arka plan** (altyazının tam arkasında beyaz gökyüzü) okumayı bozabilir.
- **Çok benzer ardışık satırlar** (uzun bir cümlede tek kısa kelime farkı) aynı sayılıp yeniden çevrilmeyebilir.
- **Harf harf yazılan altyazılar** yazım bitince çevrilir.
- **Ücretsiz Gemini kotası** yoğun diyalogda yetmez; satırların bir kısmı yerel kalitede kalır.
- **Ekran ölçeği:** overlay konumu %100 ölçekte denendi; %125/%150'de kayma olabilir.
- Overlay, oyun penceresi başka bir pencerenin arkasındayken de en üstte görünür; `F8` ile kapat.
- RapidOCR yedeği CPU'da yavaştır (~0.5 sn); CTranslate2 yedeği kodlandı ama denenmedi.

## Geliştirme

```
[Pencere yakalama, 30 fps]             capture/wgc.py   (yedek: monitor.py, test: video_file.py)
   → [Altyazı bölgesini kırp]          config: [roi]
   → [Değişim tespiti, ~1 ms]          detect/change_detector.py
   → (değiştiyse) [Ön işleme] → [OCR]  ocr/preprocess.py, windows_ocr.py | rapid_ocr.py
   → [Stabilizasyon + tekrar önleme]   stabilize.py
   → [Çeviri yönlendirici]             translate/router.py  (cache, gemini, ollama_local, ct2_fallback)
   → [Overlay]                         overlay/window.py
```

Çekirdek döngü `pipeline.py` içindedir ve arayüzden bağımsızdır; overlay uygulaması (`app.py`), test paneli
(`ui/test_panel.py`) ve başsız test araçları aynı çekirdeği kullanır.

```bash
.venv\Scripts\python.exe -m pytest                                    # birim testleri
.venv\Scripts\python.exe tools\make_test_video.py                     # out/synthetic.mp4 + ground_truth.srt
.venv\Scripts\python.exe tools\replay_test.py out\synthetic.mp4 --ground-truth out\ground_truth.srt
.venv\Scripts\python.exe tools\replay_test.py out\synthetic.mp4 --simulate-offline
.venv\Scripts\python.exe tools\bench_ocr.py --errors                  # iki OCR motoru: süre + doğruluk
.venv\Scripts\python.exe tools\bench_translate.py                     # katman başına süre + örnek çeviriler
.venv\Scripts\python.exe tools\soak_test.py out\synthetic.mp4 --minutes 10   # bellek eğilimi
```

`replay_test.py` bir videoyu oynatıp her altyazı için CSV yazar ve p50/p95 gecikme, OCR doğruluğu (`.srt`
verilirse), önbellek isabet oranı ve gizleme süresini basar. Kendi oyun kaydınla gerçek ölçüm alabilirsin.

Opsiyonel ekler:

- **RapidOCR (yedek OCR):** `python -m uv sync --extra rapid`, sonra `[ocr] engine = "rapid"`.
- **CTranslate2 (üçüncü çeviri katmanı):** `python -m uv sync --extra ct2`, modeli dönüştür, `[translate.ct2] enabled = true`:

  ```bash
  uvx --from ctranslate2 --with transformers --with torch --with sentencepiece ct2-transformers-converter --model Helsinki-NLP/opus-mt-tc-big-en-tr --output_dir models/opus-mt-tc-big-en-tr-ct2 --quantization int8 --copy_files source.spm target.spm
  ```

### Tasarım kararları

Özgün gereksinimler `SPEC.md` içindedir. Ondan bilerek ayrılan noktalar:

- **OCR'a maske veriliyor.** "2x büyüt + kontrast" yerine parlak piksellerden siyah-beyaz metin görüntüsü
  üretiliyor; Windows OCR doğruluğu gri görüntüde %76–98 arasında oynarken maskede %99.5–100 çıktı.
  `[ocr] preprocess = "gray"` eski davranıştır.
- **Hızlı onay.** OCR'lanan kareden sonraki karede görüntü değişmediyse ikinci OCR beklenmeden "kararlı"
  sayılır (~1 kare kazanç). `[stabilize] fast_confirm = false` birebir iki OCR bekler.
- **Ollama adresi `127.0.0.1`.** `localhost` ile Windows önce IPv6 dener ve istek başına ~260 ms kaybedilir.
- **Monitör yakalama yedeği** `dxcam`/`bettercam` yerine `windows-capture` paketinin DXGI oturumunu kullanır.
- **Yerel çeviriler de önbelleğe yazılır**, ama bulut erişilebilir olduğunda bir sonraki görülmede bulut
  sonucuyla yükseltilir. Bulut kaydı asla yerelle ezilmez.
- **Yeni satır kararlı olunca** çevirisi gelene dek eski Türkçe metin gizlenir; yanlış satırın üstünde kalmaz.
- **Gecikme tanımı:** altyazının değiştiği ilk karenin programa ulaştığı an → çevirinin overlay'e gönderildiği
  an. Akışın kendi gecikmesi (Remote Play) dahil değildir.
- **Gemini güvenlik filtreleri kapalıdır** (`BLOCK_NONE`); +18 oyun diyaloğu boş dönmesin diye.
