# gta-tr-subs — GTA 6 anlık Türkçe altyazı overlay'i (PS5 → PC)

PS5'te oynanan oyunun PC'de görünen penceresini (PS Remote Play, chiaki-ng veya capture kartı + OBS
Projector) yakalar, İngilizce altyazıyı OCR ile okur, Türkçeye çevirir ve orijinal altyazının **tam üstüne**
şeffaf bir overlay olarak bindirir. Windows 11, Python 3.12.

```
[WGC pencere yakalama, 30 fps]            capture/wgc.py  (yedek: monitor.py, test: video_file.py)
   → [ROI kırp]                           config: [roi]
   → [Değişim tespiti, her karede ~1 ms]  detect/change_detector.py
   → (değiştiyse) [Ön işleme] → [OCR]     ocr/preprocess.py, windows_ocr.py | rapid_ocr.py
   → [Stabilizasyon + dedupe]             stabilize.py
   → [Yönlendirici: cache → (bulut ‖ yerel) → yedek]   translate/router.py
   → [Overlay]                            overlay/window.py
```

## Kurulum

1. **Python + uv** — [uv](https://docs.astral.sh/uv/) kur (`winget install astral-sh.uv` veya `pip install uv`), sonra depo kökünde:

   ```bash
   uv sync
   ```

   Opsiyonel ekler: `uv sync --extra rapid` (yedek OCR motoru RapidOCR), `uv sync --extra ct2` (Katman 3).
   Geliştirme/test araçları (pytest, pillow) varsayılan olarak kurulur.

2. **Windows İngilizce OCR dil paketi** — birincil OCR motoru (Windows.Media.Ocr) için gerekir.
   Yönetici PowerShell:

   ```powershell
   Add-WindowsCapability -Online -Name "Language.OCR~~~en-US~0.0.1.0"
   ```

   (veya Ayarlar → Saat ve dil → Dil ve bölge → Dil ekle → English (United States).) Paket yoksa uygulama
   açık bir hata mesajı gösterir; RapidOCR kuruluysa otomatik ona düşer.

3. **Gemini API anahtarı (bulut katmanı)** — yalnızca `GEMINI_API_KEY` ortam değişkeninden okunur, hiçbir
   yere yazılmaz/loglanmaz. Kalıcı tanımlamak için (sonra terminali yeniden aç):

   ```powershell
   setx GEMINI_API_KEY "anahtarın"
   ```

   Anahtar yoksa uygulama yalnızca yerel katmanlarla çalışır.

4. **Ollama + TranslateGemma (yerel/offline katman)** — [ollama.com](https://ollama.com) kur, sonra:

   ```bash
   ollama pull translategemma:4b
   ```

   Uygulama açılışta modeli ısıtır (`keep_alive = 2h`). **VRAM:** `translategemma:4b` ≈ 4–5 GB,
   `translategemma:12b` ≈ 9–10 GB. GPU, Remote Play/chiaki video çözme ile paylaşıldığı için varsayılan 4B'dir;
   8 GB kartlarda 12B önerilmez. Ollama kapalıysa/model yoksa katman atlanır ve 20 sn'de bir yeniden yoklanır.

5. **(Opsiyonel) Katman 3 — CTranslate2 + opus-mt** — Ollama da yoksa devreye giren CPU yedeği (~50–100 ms):

   ```bash
   uv sync --extra ct2
   uvx --from ctranslate2 --with transformers --with torch --with sentencepiece ct2-transformers-converter --model Helsinki-NLP/opus-mt-tc-big-en-tr --output_dir models/opus-mt-tc-big-en-tr-ct2 --quantization int8 --copy_files source.spm target.spm
   ```

   Sonra `config.toml` → `[translate.ct2] enabled = true`.

### PS5 / görüntü kaynağı ayarları

- **PS Remote Play / chiaki-ng (birincil):** Pencereli veya **kenarlıksız tam ekran** çalıştır. *Exclusive
  fullscreen* overlay'i gizleyebilir. 1080p akış önerilir (720p de çalışır). Pencereyi simge durumuna
  küçültme — WGC kare vermeyi keser (başka pencerenin arkasında kalması sorun değil).
- **Capture kartı + OBS:** PS5'te **Ayarlar → Sistem → HDMI → HDCP'yi Etkinleştir**'i kapat (açıkken kart siyah
  görüntü verir). OBS'te kaynağa sağ tık → *Windowed Projector* (veya Fullscreen Projector) aç ve onu seç.
- Oyunda altyazıları aç; mümkünse altyazı arka planı kapalı, standart beyaz yazı.
- HDR akışında beyazlar soluk gelirse `[detect] bright_threshold` değerini düşür (örn. 140).

## Çalıştırma

```bash
uv run gta-tr-subs
```

İlk açılışta: (1) pencere listesinden hedefi seç (★ = otomatik öneri; seçim `config.toml`'a kaydedilir),
(2) donmuş kare üzerinde fareyle altyazı bölgesini çiz (pencereye **oransal** kaydedilir). Sonra overlay çalışır.

| Kısayol | İşlev |
|---|---|
| `F8` | Çeviriyi/overlay'i aç-kapa (kapalıyken OCR ve çeviri de durur) |
| `F9` | Altyazı bölgesini yeniden kalibre et (opsiyonel konuşmacı bölgesi dahil) |
| `F10` | Orijinal İngilizceyi küçük puntoyla göster/gizle |
| `F11` | Çeviri modu: yarış → bulut → yerel |
| `F12` | Debug paneli: canlı ROI önizlemesi, fps, OCR/çeviri süreleri, aktif katman, devre kesici |

Çıkış: sistem tepsisindeki **TR** simgesi → Çıkış, debug panelindeki düğme veya konsolda `Ctrl+C`.
Kısayollar `config.toml` → `[hotkeys]` altından değiştirilebilir (tuşlar oyuna/uygulamaya da iletilir; örn.
chiaki-ng'de `F11` tam ekran geçişiyse birini değiştir).

Diğer seçenekler:

```bash
uv run gta-tr-subs --pick-window          # pencere seçimini zorla
uv run gta-tr-subs --console              # overlay yok; kararlı altyazı + çeviriyi konsola yaz
uv run gta-tr-subs --source video.mp4     # kare kaynağı olarak video (overlay bir önizleme penceresini takip eder)
uv run gta-tr-subs --simulate-offline     # bulutu hataya zorla (yerele geçişi dene)
uv run gta-tr-subs --list-windows
```

## Yapılandırma

- `config.toml` — pencere başlığı, ROI, OCR motoru, eşikler, modeller, timeout'lar, mod, overlay görünümü,
  kısayollar. Her alan dosyada yorumla açıklanmıştır.
- `glossary.yaml` — `keep:` çevrilmeyecek özel isimler, `terms:` sabit terim karşılıkları. Bulut prompt'una
  eklenir; tüm katmanların çıktısında çeviri sonrası isim kontrolü yapılır ("Lusiya'yı" → "Lucia'yı").
- `translations.db` — kalıcı çeviri önbelleği (SQLite). Silersen sıfırlanır.

Çeviri davranışı:

- **Cache** her zaman önce. Yerel katmandan gelmiş bir kayıt gösterilir, bulut erişilebilirse arkadan sessizce
  bulut sonucuyla yükseltilir.
- **Yarış modu (varsayılan):** bulut ve yerel paralel başlar, ilk gelen gösterilir; bulut sonra gelirse ve altyazı
  hâlâ ekrandaysa yerine geçer ve cache'e yazılır.
- **Devre kesici:** bulut 2 ardışık hata/zaman aşımı (1200 ms) verirse 30 sn atlanır, sonra tek deneme isteğiyle
  yoklanır. Ağ yokken yerel zaten paralel çalıştığı için takılma olmaz.
- Son 3 satır (EN+TR) bağlam olarak buluta gönderilir (hitap/zamir tutarlılığı). TranslateGemma kendi model
  kartındaki prompt formatıyla çağrılır.

## Test (oyun olmadan)

```bash
uv run pytest                                              # birim testleri
uv run python tools/make_test_video.py                     # out/synthetic.mp4 + out/ground_truth.srt
uv run python tools/replay_test.py out/synthetic.mp4 --ground-truth out/ground_truth.srt
uv run python tools/replay_test.py out/synthetic.mp4 --simulate-offline
uv run python tools/replay_test.py kayit.mp4 --fake-translate   # ağ/model olmadan pipeline gecikmesi
uv run python tools/bench_ocr.py --errors                  # iki OCR motoru: süre + doğruluk
uv run python tools/bench_translate.py                     # katman başına TTFT/toplam süre
uv run python tools/bench_translate.py --router            # cache + yarış uçtan uca
uv run python tools/soak_test.py out/synthetic.mp4 --minutes 10   # bellek eğilimi
```

`replay_test.py` her altyazı için CSV yazar (zaman, OCR metni, kararlı olma süresi, katman, çeviri süresi, toplam
gecikme) ve sonunda p50/p95 gecikme, OCR karakter doğruluğu (`--ground-truth x.srt` verilirse), cache isabet
oranı ve "kaybolma → gizleme" süresini basar. Gerçek ölçüm için kendi oyun kaydını (İngilizce altyazılı GTA V/VI
videosu) ve elle hazırlanmış bir `.srt` kullan.

## Ölçümler (geliştirme makinesi: Windows 11, RTX 3070, sentetik 1080p/30 fps video)

Çeviri sağlayıcıları bu ölçümlerde **sahte**dir (bulut 350 ms, yerel 120 ms sabit gecikme); yani tablo pipeline'ın
kendi payını ve yönlendirici davranışını gösterir, gerçek Gemini/Ollama sürelerini değil.

| Ölçüm | Sonuç | Hedef |
|---|---|---|
| Değişim tespiti (her kare) | p50 1.3 ms, p95 1.5 ms | — |
| Windows OCR (ön işleme dahil) | p50 5.7 ms, p95 11.3 ms; `bench_ocr`: ort 8.7 ms | — |
| RapidOCR (CPU) | ort 530 ms, p95 1077 ms | yalnızca yedek |
| OCR karakter doğruluğu — Windows | %100 (replay 16 satır; `bench_ocr` 52 görüntü, 1080p+720p) | ≥ %97 |
| OCR karakter doğruluğu — RapidOCR | %99.1 | ≥ %97 |
| Belirme → "kararlı" (4 karelik fade dahil) | p50 128 ms, p95 143 ms | — |
| Toplam gecikme, cache isabeti | p50 84 ms, p95 124 ms | < 150 ms |
| Toplam gecikme, yeni satır (yerel 120 ms ile) | p50 261 ms, p95 267 ms | p50 < 600, p95 < 1200 |
| Altyazı kayboldu → overlay gizlendi | p50 243 ms, p95 269 ms, max 277 ms | ≤ 300 ms |
| Offline simülasyonu | 16/16 satır yerelden, takılma yok; 2 hatada devre açıldı, 30 sn sonra tek yoklama | 2 sn içinde geçiş |
| Overlay'in yakalamaya girmemesi | DXGI ekran yakalamasında ve WGC pencere yakalamasında overlay yok | çift çeviri yok |
| Bellek (6 dk, 4x hız ≈ 24 dk içerik, 43 081 kare, 540 altyazı) | 683–694 MB bandında, belirgin artış yok | 2 saatte sızıntı yok |

**Gerçek Ollama ile (`translategemma:4b`, RTX 3070, %100 GPU, ~2.9 GB; bulut yok):** tek başına çeviri ort
168 ms (ilk token 51 ms, p95 243 ms); replay'de uçtan uca yeni satır p50 427 ms, p95 575 ms; cache isabeti
p50 111 ms, p95 138 ms. Modelin diskten ilk yüklenmesi ~40 sn sürer (uygulama açılışında ısıtılır).
Ollama adresi `127.0.0.1` olmalı: `localhost` ile Windows önce IPv6 dener ve istek başına ~260 ms kaybedilir.
4B modelin çevirisi resmî ("siz") ve yer yer hatalıdır, küfürü yumuşatır; kalite için bulut katmanı gerekir.

Yeni satır gecikmesinin çeviri dışı payı ≈ 130–145 ms'dir (bunun ~100 ms'si test videosundaki fade-in). Yani
p50 < 600 ms hedefi için çeviri katmanına ≈ 450 ms kalıyor. 2 saatlik kesintisiz bellek testi ve gerçek
Remote Play penceresinde uçtan uca ölçüm **yapılmadı**.

## Varsayımlar

- **Dizin yapısı:** proje depo kökünde durur (ayrı bir `gta-tr-subs/` alt klasörü yok). Spec'teki ağaca ek olarak
  `pipeline.py` (çekirdek döngü), `app.py` (Qt denetleyici), `hotkeys.py`, `config.py`, `capture/picker.py`,
  `capture/base.py`, `ocr/base.py`, `ocr/text.py`, `translate/base.py`, `translate/fake.py` ve
  `tools/{synth,make_test_video,soak_test}.py` eklendi.
- **Monitör yakalama yedeği:** `dxcam`/`bettercam` yerine `windows-capture` paketinin kendi DXGI Desktop
  Duplication oturumu kullanıldı (aynı mekanizma, ek bağımlılık yok). `[capture] backend = "monitor"`.
- **OCR ön işleme:** "2x büyüt + kontrast" yerine varsayılan olarak **parlak piksel maskesinden siyah-beyaz metin
  görüntüsü** (`preprocess = "mask"`, 2x büyütülmüş) OCR'a veriliyor. Ölçümde Windows OCR doğruluğu gri
  görüntüde %76–98 arasında oynarken maskede %99.5–100 çıktı. `preprocess = "gray"` eski davranıştır.
- **"2 ardışık OCR" kuralı:** `fast_confirm = true` iken, OCR'lanan kareden sonraki karede maske değişmediyse
  ikinci OCR çalıştırılmadan "ardışık görülme" sayılır (~1 kare kazanç). `false` yapınca birebir iki OCR beklenir.
- **Gecikme tanımı:** altyazı maskesinin değiştiği ilk karenin elimize ulaştığı an → çevirinin overlay'e
  gönderildiği an. Akışın kendi gecikmesi (Remote Play) ve Qt'nin çizimi (≤ 1 kare) dahil değildir.
- **Spekülatif çeviri** (`speculative`) varsayılan kapalı: fade sırasındaki yarım okumalar için ~2 kat API
  çağrısı yapıyor, kazancı `fast_confirm` ile ~1 kareye indi.
- **Yeni satır kararlı olduğunda** çevirisi gelene dek eski Türkçe metin gizlenir (yanlış satırın üstünde
  kalmasın); arada orijinal İngilizce görünür.
- **Akış (streaming):** Gemini'den gelen kısmi metin, ekranda başka tam çeviri yokken anında gösterilir
  (`stream_partials`). "Toplam gecikme" metrikleri ilk **tam** çeviriye göredir.
- **Yerel sonuçlar da cache'e yazılır** (`cache_local_results`), ama "yerel" etiketiyle; bulut erişilebilir
  olduğunda bir sonraki görülmede bulut sonucuyla yükseltilir. Bulut kaydı asla yerelle ezilmez.
- **Gemini thinking:** `thinking = "auto"` → 3.x modellerde `thinking_level = MINIMAL`, 2.x'te
  `thinking_budget = 0`. API ayarı reddederse otomatik olarak bir alt seçeneğe düşülür.
- **Gemini güvenlik filtreleri** `BLOCK_NONE`: +18 oyun diyaloğundaki küfür/argo çevirisinin boş dönmemesi için.
- **TranslateGemma** model kartındaki tek kullanıcı mesajı formatıyla çağrılır; bağlam ve sözlük prompt'a
  eklenmez (format bozulmasın), sözlük çeviri **sonrası** uygulanır.
- **F8** yalnızca overlay'i gizlemez, pipeline'ı da duraklatır (boşa OCR/API çağrısı olmasın).
- **Konuşmacı ROI'si** (opsiyonel) yalnızca satır kararlı olduğunda bir kez okunur ve çevrilmeden başlıkta gösterilir.
- **Debug paneli ve overlay** `WDA_EXCLUDEFROMCAPTURE` ile işaretlidir: hiçbir ekran kaydında/yayınında görünmezler.

## Bilinen sorunlar / sınırlar

- **Gemini bu makinede canlı ölçülemedi** (`GEMINI_API_KEY` tanımlı değil); Ollama ölçüldü (yukarıda).
  Gemini istek yolu (hata/zaman aşımı/iptal dahil) sahte anahtar ve sahte sağlayıcılarla doğrulandı; gerçek p50/p95
  için `tools/bench_translate.py` ve `tools/replay_test.py` çalıştırılmalı. `gemini-3.5-flash-lite` model adı
  da bu yüzden doğrulanmadı — hata verirse `[translate.gemini] model` değerini değiştir.
- **OCR doğruluğu sentetik videoda ölçüldü** (temiz beyaz + siyah konturlu yazı, 1080p ve 720p). Gerçek Remote
  Play akışında sıkıştırma artefaktları sonucu düşürebilir; kendi kaydınla `replay_test.py --ground-truth` çalıştır.
- **Çok parlak arka plan** (altyazının tam arkasında beyaz gökyüzü/far) maskeyi kirletir. Deneyebileceklerin:
  `[detect] require_outline = true`, `bright_threshold` yükseltmek veya `[ocr] preprocess = "gray"`.
- **Çok benzer ardışık satırlar** (benzerlik ≥ %92; örn. uzun bir cümlede tek kısa kelime farkı) aynı satır
  sayılıp yeniden çevrilmeyebilir. `[stabilize] similarity` yükseltilebilir.
- **Karakter karakter yazılan altyazılar:** yazım durana kadar "kararlı" olmaz; çeviri cümle tamamlanınca gelir.
- **RapidOCR CPU'da yavaş** (bu makinede ~0.5 sn/görüntü): yalnızca yedek. GPU için
  `uv pip install onnxruntime-directml` + `rapid_use_dml = true` (denenmedi).
- **CTranslate2 yedeği** kodlandı ama model dönüştürülüp denenmedi.
- **Ekran ölçeklendirme:** overlay konumu yalnızca %100 ölçekte denendi; %125/%150'de ve farklı ölçekli çoklu
  monitörde kayma olursa bildir.
- **Exclusive fullscreen** pencereler overlay'i gizleyebilir → pencereli/kenarlıksız kullan.
- Overlay, hedef pencere başka bir pencerenin arkasındayken de (altyazı varsa) en üstte görünür; `F8` ile kapat.
- Global kısayollar tuşu yutmaz; aynı tuş odaktaki uygulamaya da gider.
