# Модели и веса Adal: что реально используется и что нужно офлайн

Baseline `2974f64`. Сценарий: **классная демонстрация, один LIVE CV-эпизод на ПК студента** (телефон в кадре
и/или взгляд в сторону), преподаватель видит событие, локальный отчёт, перезапуск. Метки ниже — для этого сценария.
Наличие каталога ничего не доказывает: готовность = файл на месте + размер + SHA-256 из пина владельца модуля.
Пины читаются инструментом во время запуска из файлов владельцев, здесь не дублируются как истина.

| Модуль | Файл (под каталогом моделей) | Пин (истина) | Где ищет runtime | Метка | Что будет без него |
|---|---|---|---|---|---|
| Телефон A03 | `phone/yolo11n.onnx`, 10 930 182 B, `634279b4…` | `backend/proctor/phone/models.manifest.json` | `QORGAU_MODELS_DIR`, иначе `<checkout>\proctoring\models` (`settings.py:16,39,66-86`) | **required** (PC2/PC3) | backend стартует, health phone UNAVAILABLE, preflight `phone_model` FAIL (required) → калибровка/старт отклоняются (`session.py:415,432,658-700`) |
| Лицо/взгляд A04 | `attention/face_landmarker.task`, 3 758 596 B, `64184e22…` | `backend/proctor/attention/models.manifest.json` | то же | **required** (PC2/PC3) | preflight `face_model` FAIL → старт отклоняется (`session.py:416`); загрузка по пути `model_asset_path` (`attention/landmarker.py:131`) |
| Личность A13 | `identity/face_detection_yunet_2023mar.onnx`, `identity/face_recognition_sface_2021dec.onnx` | `backend/proctor/identity/models.manifest.json` | то же (runtime); **но** `identity.prepare` по умолчанию пишет в `%LOCALAPPDATA%\QorgauExam\models` (`identity/prepare.py:31-36`) | optional | нет строки preflight; health identity UNAVAILABLE, общий health DEGRADED, у студента «Сверка лица: Недоступно»; экзамен не блокируется |
| Речь A14 | `audio/silero_vad.onnx` (`1a153a22…`), `audio/manifest.json`, `audio/LICENSE.silero.txt` | `backend/proctor/audio/assets.py` (SHA-256, ревизия); размер не закреплён | **только** `%LOCALAPPDATA%\QorgauExam\models\audio` (`audio/assets.py:14-18`), `QORGAU_MODELS_DIR` игнорируется | optional, рекомендуется | микрофон в LIVE включается всегда (`session.py:713-723`); без Silero — энергетический фолбэк `energy_fallback`, который **может открыть инцидент «речь» без пометки об эвристике** (`audio/vad.py:61-67`, `audio/fusion.py:26-27`) |

Других моделей нет: YAMNet/VGGish/Whisper/speaker-ID в baseline не используются (grep по репозиторию).
COCO-датасеты `phone/eval/*` и тестовые изображения MediaPipe не нужны офлайн.

## Проверено в облачном контейнере (Linux, не Windows)

- Штатные команды подготовки репозитория скачали файлы в scratchpad (не в git):
  `proctor.phone.prepare --download` → `[model_ok] … 634279b4…`, загрузка ORT, warm-up 57.9 ms;
  `proctor.attention.model_tool fetch` → `OK … 64184e22…`; `proctor.audio.prepare --download` → OK.
  SHA-256 всех трёх совпадают с пинами. Размер Silero — 2 327 524 B (записан в `kit_pins.json` как наблюдение).
- `proctor.identity.prepare --download` → `HTTP Error 403` от egress-политики контейнера (github.com/opencv raw).
  **Источник identity из контейнера не перепроверен**; в репозитории он записан A13. Это не дефект репозитория.
- Архив Electron `electron-v43.7.5-win32-x64.zip`: SHA-256 `7acfa064…` совпал с `checksums.json` npm-пакета
  `electron@43.7.5` (integrity которого закреплён в `desktop/package-lock.json`); внутри `electron.exe` PE x64.
- 47 колёс `requirements/full.txt` для `win_amd64`/cp312 скачаны с проверкой `--require-hashes`; лицензии взяты
  из METADATA каждого колеса (у всех есть). `pip download --no-index` только из комплекта, внутри сетевого
  namespace **без сети**, разрешил все 46 применимых к Linux-маркерам колёс для Windows (colorama — win-only маркер,
  в комплекте есть).

## MediaPipe и сеть — важная находка

- **Факт (Linux, mediapipe 0.10.35)**: `FaceLandmarker.create_from_options` → 30 кадров → `close()` под `strace`
  открыл TCP и отправил `CONNECT play.googleapis.com:443` через прокси, затем TLS и данные. Это встроенный
  клиент usage-логирования (строки `mediapipe_log_extension.proto`, `SystemInfo app_id/app_version/mediapipe_version`).
  Изображения он, по всей видимости, не отправляет, но содержимое под TLS не проверено.
- **Факт (Linux)**: без сети (`unshare -n`) — только попытки DNS к 8.8.8.8/8.8.4.4 → `ENETUNREACH`, создание +
  30 кадров + закрытие за 0.46 с; с недоступным прокси — 0.15 с. Зависания нет. Онлайн та же операция заняла 1.03 с.
- **Гипотеза (Windows)**: `libmediapipe.dll` импортирует WinINet (`InternetOpenA`, `HttpSendRequestA`…) и содержит
  те же строки Clearcut (разбор PE-таблицы читателем A04-подсистемы). Нужна проверка на репетиции: нет ли задержки
  при старте сессии в ЛВС без интернета (DNS роутера может отвечать медленно). Отключить это из кода Adal нельзя
  без решения владельца A04; брандмауэр этот комплект не меняет.

## Лицензии / распространение

- YOLO11n: **AGPL-3.0** (manifest A03, метаданные ONNX). Копирование на свои ПК для демонстрации — внутреннее
  использование; передача третьим лицам требует решения команды (AGPL: текст лицензии и источник). В комплекте
  помечено `distribution`. Решение не записано в репозитории.
- FaceLandmarker: Apache-2.0 (manifest A04, по model cards; текста лицензии в `.task` нет).
- YuNet MIT, SFace Apache-2.0 (manifest A13 с `license_url`).
- Silero VAD: MIT (`audio/prepare.py`), текст `LICENSE.silero.txt` из закреплённой ревизии едет вместе с весами.
- Electron: MIT; `LICENSES.chromium.html` внутри архива.
- Колёса: лицензия из METADATA каждого колеса (License-Expression / License / classifier); при отсутствии — BLOCKED.
