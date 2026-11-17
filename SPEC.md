# PROMPT — GTA 6 Anlık Türkçe Altyazı Overlay (PS5 → PC)

> Bu metni olduğu gibi kodlama ajanına (Claude Code vb.) ver. Proje klasörüne `SPEC.md` olarak da koyabilirsin.

---

## Rol ve hedef

Sen kıdemli bir Windows masaüstü + gerçek zamanlı görüntü işleme geliştiricisisin. Benim için **Windows 11 üzerinde çalışan, PS5'te oynadığım GTA 6'nın İngilizce altyazılarını ekrandan okuyup anında Türkçeye çeviren ve orijinal altyazının üstüne bindiren** bir uygulama yazacaksın.

Oyun PC monitöründe görünüyor. Görüntü PC'ye iki yoldan biriyle geliyor:
1. **PS Remote Play** (resmi uygulama) veya **chiaki-ng** penceresi (birincil senaryo, ekstra donanım yok)
2. **HDMI capture kartı** → OBS "Fullscreen/Windowed Projector" penceresi (alternatif)

Her iki senaryoda da uygulama **belirli bir pencereyi** yakalayacak, altyazı bölgesini OCR ile okuyacak, çevirecek ve şeffaf bir overlay'de gösterecek.

**En önemli metrik: altyazının ekranda belirmesinden Türkçesinin görünmesine kadar geçen süre.** Hedef: önbellekte varsa < 150 ms, yeni satırda p50 < 600 ms, p95 < 1200 ms.

---

## Kesinleşmiş teknoloji seçimleri

- **Dil:** Python 3.12 (64-bit), `uv` ile bağımlılık yönetimi. Tip ipuçları + `asyncio` tabanlı pipeline.
- **Ekran yakalama:** Windows Graphics Capture (WGC) ile **pencere yakalama** — `windows-capture` paketi (Rust tabanlı, hızlı). Monitör yakalama yedeği: `dxcam`/`bettercam`. Pencere yakalama, üstündeki overlay'i görmez; bu yüzden birincil yöntem budur.
- **OCR (iki motor, benchmark ile seç):**
  - Birincil: **Windows.Media.Ocr** (`winrt-Windows.Media.Ocr` / pywinrt). Çok hızlı, temiz oyun altyazısında yeterli. Windows'ta İngilizce dil paketi kurulu olmalı; değilse kullanıcıya açık hata mesajı ver.
  - Yedek: **RapidOCR** (`rapidocr-onnxruntime`, PaddleOCR modelleri), GPU için `onnxruntime-directml`.
  - Her iki motoru aynı test görüntüleriyle ölçen bir `bench_ocr.py` yaz; config'den seçilebilsin.
- **Çeviri — katmanlı:**
  - **Katman 0 – Önbellek:** Bellek içi LRU + kalıcı SQLite (`translations.db`). GTA'da tekrar eden satırlar çok; anahtar = normalize edilmiş İngilizce metin.
  - **Katman 1 – Bulut LLM:** Google **Gemini API**, model `gemini-3.5-flash-lite` (config'den değiştirilebilir, örn. `gemini-flash-lite-latest`). Resmi `google-genai` SDK, **streaming**, thinking en düşük seviyede/kapalı, `temperature` düşük. API anahtarı yalnızca `GEMINI_API_KEY` ortam değişkeninden okunur, koda/log'a asla yazılmaz.
  - **Katman 2 – Yerel LLM (offline):** **Ollama** + `translategemma:4b` (GPU varsa `translategemma:12b` seçeneği). Ollama HTTP API (`http://localhost:11434`), model uygulama açılışında "ısıtılsın" (warm-up isteği, `keep_alive` uzun).
  - **Katman 3 – Ultra hızlı yedek (opsiyonel):** CTranslate2 ile `Helsinki-NLP/opus-mt-tc-big-en-tr` (CPU'da bile ~50–100 ms). Ollama da çalışmazsa devreye girer.
- **Overlay:** PySide6 (Qt6). Çerçevesiz, şeffaf, her zaman üstte, tıklamayı geçiren pencere (`FramelessWindowHint | WindowStaysOnTopHint | Tool | WindowTransparentForInput`, `WA_TranslucentBackground`). Ayrıca Win32 `SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)` çağır ki overlay hiçbir ekran yakalamaya girmesin (kendi çevirimizi OCR'lamayalım).
- **Diğer:** `rapidfuzz` (metin benzerliği), `numpy`, `opencv-python` (ön işleme), `pydantic-settings` (config), `loguru` (log), global kısayollar için `pynput` veya `keyboard`.

---

## Pipeline (uçtan uca)

```
[WGC pencere yakalama, 30 fps]
   → [ROI kırp: sadece altyazı bölgesi]
   → [Değişim tespiti: ucuz, her karede]
   → (değiştiyse) [Ön işleme] → [OCR]
   → [Metin stabilizasyonu + dedupe]
   → [Çeviri yönlendirici: cache → (bulut ‖ yerel yarış) → yedek]
   → [Overlay güncelle]
```

### 1. Yakalama
- Açılışta pencere listesinden hedefi seçtir ("PS Remote Play", "chiaki", "Projector" vb. başlıkla otomatik öner). Seçim config'e kaydedilsin.
- Yakalama ayrı thread'de, son kareyi tek elemanlı kuyruğa koysun (eski kareler düşürülsün, birikme olmasın).
- `--source video.mp4` modu: kare kaynağı olarak video dosyası (test için, aşağıya bak).

### 2. ROI kalibrasyonu
- İlk çalıştırmada (ve kısayolla) yakalanan karenin donmuş görüntüsü üzerinde fareyle altyazı bölgesini dikdörtgen olarak seçtir. Varsayılan: alt %22, yatay orta %80.
- ROI pencere boyutuna **oransal** kaydedilsin (pencere boyutu değişirse bozulmasın).
- Konuşmacı ismi ayrı renkte/satırda olabilir; opsiyonel ikinci ROI desteği olsun.

### 3. Değişim tespiti (OCR'ı gereksiz çalıştırma)
- ROI'yi gri tonlamaya çevir, parlak piksel eşiği uygula (altyazılar genelde beyaz + koyu kontur) → ikili maske.
- Maskeyi küçült (örn. 1/4), önceki maskeyle XOR farkı oranını hesapla. Eşik altıysa OCR yok.
- Maske neredeyse boşsa → "altyazı yok" durumu → overlay'i (kısa bir gecikmeyle, örn. 250 ms) gizle.

### 4. Ön işleme + OCR
- Metnin bounding box'ını bul, sadece o kısmı kırp, 2x büyüt, kontrastı artır.
- OCR sonucu: satırları birleştir, tire/satır sonu düzelt, normalize et (boşluk, tırnak, "..." vs.).
- Güven skoru düşükse veya metin anlamsızsa (çok kısa, harf oranı düşük) at.

### 5. Metin stabilizasyonu
- Altyazılar belirirken/kaybolurken fade olur; yarım okunan metni çevirmeye gönderme.
- Kural: aynı (veya `rapidfuzz` ratio ≥ 92) metin **2 ardışık OCR'da** görülünce "kararlı" say. Konfigüre edilebilir.
- Son gösterilen metinle aynıysa yeniden çevirme.
- Bir satır öncekinin uzantısıysa (karakter karakter yazılan altyazı durumu) önceki isteği iptal et, yenisini gönder.

### 6. Çeviri yönlendirici
- Önce cache. Varsa anında göster.
- Yoksa **yarış modu** (varsayılan): bulut ve yerel istek **paralel** başlasın.
  - Hangisi önce gelirse göster.
  - Bulut sonucu sonra gelirse ve altyazı hâlâ ekrandaysa, bulut sonucuyla sessizce değiştir (bulut kaliteli kabul edilir). Cache'e bulut sonucu yazılsın.
- Diğer modlar (config): `cloud_first` (bulut, timeout 1200 ms → yerel), `local_only`.
- **Offline/devre kesici:** Bulut 2 ardışık hata/timeout verirse 30 sn boyunca bulutu atla, sadece yerel kullan; sonra tek bir deneme isteğiyle tekrar yokla. Ağ yokken uygulama hiç takılmamalı.
- Yeni satır geldiğinde eski satırın bekleyen istekleri iptal edilsin.

### 7. Çeviri prompt'u (LLM'e giden)
Sistem talimatı (Türkçe çıktı, sadece çeviri):
- "GTA 6 oyun altyazısını doğal, konuşma dilinde Türkçeye çevir. Sadece çeviriyi yaz, açıklama ekleme."
- Kısa ve okunabilir tut; altyazı uzunluğunu aşırı uzatma.
- Argo, küfür ve ton korunsun (sansürleme, yumuşatma yok; oyunun +18 tonuna sadık kal).
- Özel isimler çevrilmez: kişi, yer, marka, araç, radyo istasyonu (örn. Lucia, Jason, Vice City, Leonida). `glossary.yaml` dosyasından sabit terimler eklensin; kullanıcı düzenleyebilsin.
- Bağlam: son 3 çevrilmiş satır (İngilizce + Türkçe) prompt'a eklensin ki zamirler, hitap (sen/siz) ve ton tutarlı olsun.
- TranslateGemma için kendi beklediği prompt formatını kullan (model kartındaki formatı uygula); glossary'yi orada da uygula (çeviri sonrası özel isim kontrolü).

### 8. Overlay
- Orijinal altyazı ROI'sinin tam üstüne, yarı opak koyu arka plan kutusu (orijinal İngilizceyi kapatsın) + beyaz, konturlu Türkçe metin.
- Yazı boyutu otomatik: metni ROI genişliğine sığdır, en fazla 2–3 satır.
- Opsiyonlar: orijinal İngilizceyi küçük puntoyla üstte göster/gizle; overlay opaklığı; font.
- Kaynağın hangi katmandan geldiğini gösteren küçük, kapatılabilir rozet (cache / bulut / yerel) — debug için.
- Overlay, yakalanan pencere hareket ederse/boyut değiştirirse onu takip etsin.
- Not: Remote Play/chiaki **pencereli veya kenarlıksız tam ekran** çalışmalı (exclusive fullscreen overlay'i gizleyebilir). README'de belirt.

### 9. Kısayollar (klavye)
- `F8` overlay aç/kapa · `F9` ROI yeniden kalibre · `F10` İngilizceyi göster/gizle · `F11` çeviri modu döngüsü (yarış / bulut / yerel) · `F12` debug paneli (fps, OCR süresi, çeviri süresi, aktif katman).

---

## Yapılandırma ve dosya yapısı

```
gta-tr-subs/
  pyproject.toml
  config.toml            # pencere başlığı, ROI, OCR motoru, modeller, timeout'lar, mod
  glossary.yaml          # sabit terimler / çevrilmeyecek isimler
  src/gta_tr_subs/
    main.py
    capture/  (wgc.py, monitor.py, video_file.py)
    roi/      (calibrate.py)
    detect/   (change_detector.py)
    ocr/      (windows_ocr.py, rapid_ocr.py, preprocess.py)
    stabilize.py
    translate/(router.py, cache.py, gemini.py, ollama_local.py, ct2_fallback.py, prompts.py)
    overlay/  (window.py, win32.py)
    metrics.py
  tools/
    bench_ocr.py
    bench_translate.py
    replay_test.py
  tests/
  README.md
```

---

## Test stratejisi (oyun olmadan da test edilebilmeli)

- `replay_test.py`: İngilizce altyazılı bir oyun videosunu (örn. GTA V veya GTA 6 fragmanı kaydı) kare kaynağı olarak oynatır, tüm pipeline'ı çalıştırır, her altyazı için şunları CSV'ye yazar: zaman, OCR metni, kararlı olma süresi, çeviri katmanı, çeviri süresi, toplam gecikme.
- Sonunda p50/p95 gecikme, OCR hata oranı (varsa elle hazırlanmış `ground_truth.srt` ile karşılaştırma), cache isabet oranı özeti bas.
- Offline testi: `--simulate-offline` bayrağı bulut istemcisini hataya zorlasın; uygulamanın takılmadan yerele geçtiğini doğrula.
- Birim testleri: stabilizasyon mantığı, dedupe, cache, devre kesici, glossary uygulaması.

---

## Performans kuralları

- Ana UI thread'i asla bloklanmasın; yakalama, OCR ve ağ ayrı thread/task'larda.
- OCR yalnızca değişim olduğunda ve en fazla ~10–15 Hz çalışsın.
- Tüm kritik adımlar `metrics.py` ile ms cinsinden ölçülsün, debug panelinde canlı görünsün.
- GPU'yu Remote Play/oyun akışıyla paylaşıyoruz; yerel modelin VRAM kullanımını README'de belirt, 4B varsayılan.

---

## Teslimat sırası (her adımda çalışan bir şey ver)

1. **M1:** Pencere seçimi + WGC yakalama + ROI kalibrasyonu + debug penceresinde ROI canlı önizleme.
2. **M2:** Değişim tespiti + OCR (iki motor) + `bench_ocr.py`. Konsola kararlı altyazıları yazdır.
3. **M3:** Çeviri katmanları (cache, Gemini streaming, Ollama TranslateGemma, yarış modu, devre kesici) + `bench_translate.py`.
4. **M4:** Overlay (capture'dan hariç tutulmuş, tıklama geçiren) + kısayollar.
5. **M5:** `replay_test.py`, metrik özeti, README (kurulum: Python/uv, Ollama + model indirme, `GEMINI_API_KEY`, Windows İngilizce OCR dil paketi, Remote Play/chiaki-ng ayarları, capture kartı için PS5'te HDCP kapatma).

Her milestone sonunda: nasıl çalıştırılacağını, ölçtüğün gecikme değerlerini ve bilinen sorunları kısaca raporla. Belirsiz bir noktada makul varsayımı yap, README'de "Varsayımlar" başlığı altında not düş ve devam et.

---

## Kabul kriterleri

- 1080p Remote Play penceresinde, standart beyaz altyazıda OCR doğruluğu ≥ %97 (karakter bazında).
- Yeni satırda p50 toplam gecikme < 600 ms, p95 < 1200 ms; cache isabetinde < 150 ms.
- İnternet kesildiğinde 2 sn içinde yerel modele geçiş, kullanıcı hiçbir şey yapmadan; internet gelince otomatik buluta dönüş.
- Overlay kendi çıktısını asla OCR'lamıyor (çift çeviri/titreme yok).
- Altyazı kaybolduğunda overlay ≤ 300 ms içinde kayboluyor.
- Uygulama 2 saat kesintisiz çalışmada bellek sızıntısı yapmıyor.
