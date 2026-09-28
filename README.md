# Plaka Okuma — Akıllı Kantar

Tesis tartım operatörü için masaüstü uygulama. Kamera, kantar ve e-irsaliye
bağlantıları yerel yapılandırmada tutulur; adres ve hesap bilgileri bu dosyada yoktur.

## Operatör ekranı
- Yuvarlatılmış paneller, üst menü ve büyük tartım tipografisi
- Plaka, tartım ve irsaliye için üç aşamalı durum göstergesi
- Raporlar menüsünden kayıt özeti ve CSV dışa aktarma
- İki kamera önizlemesi, büyük plaka ve anlık tartım
- Geçiş kontrolü, kantar bağlantısı ve e-irsaliye eşleştirme
- Araç kayıtları, filtreler ve sistem günlüğü

Başlatma dosyaları kendi klasörünü kullanır. Python 3.12. İlk kurulum: `KUR.bat`.

### Doğrulama
`python -m unittest test_scale_auto test_visits test_occupancy test_ocr_deadline test_irsaliye_plates test_ui test_watch_preview`

Önceki sürümden güncellerken `KUR.bat` ile bağımlılıkları yükleyin, ardından uygulamayı
yeniden açın. Kamera, kantar ve e-irsaliye bağlantıları tesis ağı ve yerel ayarlarla
doğrulanır.

## Başlatma
1. `CALISTIR.bat` / `UYGULAMA.bat` — pencere açılır, **Başlat**
2. Kamera testi: `TEST_KAMERA.bat`
3. Yeniden kurulum: `KUR.bat`

Sanal ortam ve model önbellekleri proje klasöründe tutulur; GitHub'a gönderilmez.
