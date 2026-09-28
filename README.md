# Plaka Okuma — Akıllı Kantar

## Operatör ekranı · v1.2
- İki kamera önizlemesi, büyük plaka ve anlık tartım göstergesi.
- Sağ panelde geçiş kontrolü, kantar bağlantısı ve e-irsaliye eşleştirme.
- Tam genişlikte araç kayıtları, filtreler ve yatay kaydırma.
- **Sistem günlüğü** düğmesi son okumaları ve günlükleri ayrı pencerede açar.
- **Elle irsaliye gir** düğmesi manuel eşleştirme penceresini açar.
- Sistem duruyorsa veya kantar bağlantısı doğrulanmamışsa geçiş hazır gösterilmez.
- En küçük pencere boyutu 1280×900; önerilen ekran 1920×1080 (%100 ölçek).

Başlatma dosyaları kendi klasörünü kullanır; proje belirli bir sürücüye bağlı değildir.
Python 3.12 ile kurulup doğrulanmıştır. İlk kurulum için `KUR.bat` çalıştırın.

### Doğrulama
`python -m unittest test_scale_auto test_visits test_occupancy test_ocr_deadline test_irsaliye_plates test_ui`

Ekran testleri Tk ve açık masaüstü gerektirir. Gerçek kamera, kantar ve e-irsaliye
entegrasyonları tesis ağı ve ilgili yerel yapılandırmalar ile ayrıca doğrulanmalıdır.

## Başlatma
1. Masaüstü kısayolu **Plaka Okuma** veya `CALISTIR.bat` / `UYGULAMA.bat` — pencere açılır, **Başlat**
2. Kamera izleme (tarayıcı yok): `IZLE.bat` — çıkış: Q
3. Kamera testi: `TEST_KAMERA.bat`
4. Yeniden kurulum: `KUR.bat`
5. Eski konsol modu: `python live_plate_ocr.py`

## Kameralar
| Rol | IP | İzleme | OCR |
|---|---|---|---|
| Giriş ön | 172.16.21.152 | Dahua alt yayın `subtype=1` | ana `subtype=0` (sadece araç varken) |
| Giriş arka | 172.16.21.153 | aynı | aynı |
| Çıkış ön | 172.16.21.164 | Neutron `/media/video1` kısa oturum | aynı, kare alıp kapatır |
| Çıkış arka | 172.16.21.165 | aynı | aynı |

Kullanıcı: `admin` / `admin`

## Çıktılar
- Anlık kareler: `live_ocr\`
- Rapor: `live_ocr\live_report.json`

Sanal ortam ve model önbellekleri proje klasöründe tutulur; GitHub'a gönderilmez.
