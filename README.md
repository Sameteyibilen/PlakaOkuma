# Plaka Okuma — D:\PlakaOkuma

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

Not: C: diski dolu olduğu için sanal ortam, cache ve EasyOCR modelleri D: üzerindedir.
