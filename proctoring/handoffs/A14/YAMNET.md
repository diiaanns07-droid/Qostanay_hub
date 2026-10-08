# A14 — Silero + YAMNet, 2026-10-08

Ветка `codex/proctor-A14-yamnet`, база `b4cce9c141fd73f0db326cffac7e7cd18eb57c89`.
Пути: `backend/proctor/audio/` и `handoffs/A14/`. Контракт 1.1 и правило A05 не меняются.
Задание и формат кодов с точкой подтверждены капитаном в чате.

## Шаг 1: официальный артефакт, подготовка, отдельный inference

- Источник: [MediaPipe Audio Classifier](https://ai.google.dev/edge/mediapipe/solutions/audio/audio_classifier).
  Ссылка документации `.../float32/latest/yamnet.tflite` закреплена на версию `1`:
  [yamnet.tflite](https://storage.googleapis.com/mediapipe-models/audio_classifier/yamnet/float32/1/yamnet.tflite).
- Размер **4 126 810 байт**; SHA256 **`4d8b4a53282dc83ef04e3e7dbc4fbc98082e34e44ed798e16c3a0cdd4c584faf`**.
- **Apache-2.0** подтверждена непосредственно во встроенных TFLite metadata проверенного файла;
  полный [текст лицензии Apache](https://www.apache.org/licenses/LICENSE-2.0.txt) сохраняется при подготовке.
  Встроенный ZIP содержит `yamnet_label_list.txt`, ровно 521 уникальный класс.
- `prepare --download` теперь подготавливает обе модели; `--check` проверяет обе.
  Для отдельной модели есть `--model silero|yamnet`; default `all`.
  Новые файлы: `yamnet.tflite`, `yamnet.manifest.json`, `yamnet_label_list.txt`, `LICENSE.yamnet.txt`.
  Существующий Silero `manifest.json` сохраняет свой формат.
  Путь: `%LOCALAPPDATA%\QorgauExam\models\audio`, либо `QORGAU_MODELS_DIR\audio`.
- Runtime не скачивает файлы. Проверка SHA, лицензии и карты классов предшествует загрузке.
  Используется **установленный mediapipe 0.10.35**, никаких новых pip-пакетов.
- Классификация ровно 15 600 mono float32 отсчётов при 16 kHz (0,975 с), режим AUDIO_CLIPS.
  Сумма только разрешённых речевых классов ограничена сверху 1.0 для существующего поля `Unit`.
  Это оценка модели, а не откалиброванная статистическая вероятность.

## Код → исходный класс YAMNet

| Код причины | Исходный класс | Индекс в проверенном файле |
|---|---|---:|
| `yamnet.speech` | Speech | 0 |
| `yamnet.child_speech` | Child speech, kid speaking | 1 |
| `yamnet.conversation` | Conversation | 2 |
| `yamnet.narration` | Narration, monologue | 3 |
| `yamnet.babbling` | Babbling | 4 |
| `yamnet.whispering` | Whispering | 12 |
| `silero` | Не класс YAMNet; срабатывание Silero VAD | — |

`Male speech, man speaking` и `Female speech, woman speaking` **отсутствуют** в этом артефакте.
Они не получают выдуманных индексов. Любые остальные классы, включая Music, Silence,
Typing, Computer keyboard, Writing, Rustle, Mechanical fan, Air conditioning и Vehicle,
не участвуют в сумме речи. Отдельного имени Mouse click также нет; посторонние имена игнорируются.

Точка вместо двоеточия согласована: контракт `Code` допускает только `^[a-z0-9_.]{1,64}$`.

## Проверено на ноутбуке

```powershell
# Из proctoring; PYTHONPATH указывает на backend и contracts/python этого checkout.
python -m proctor.audio.prepare --download
python -m proctor.audio.prepare --check
python -m pytest backend/proctor/audio/tests -q
python -m proctor.audio.benchmark --output handoffs/A14/yamnet-benchmark.json
```

Шаг 1: **42 PASS** (20 прежних + 22 новых), реальная модель включена, пропусков нет.
Проверены whitelist, сумма и ограничение [0,1], неверные оценки, повреждённый файл/manifest,
реальная карта модели и классификация синтетической тишины. Микрофон не включался.

Производительность: [yamnet-benchmark.json](yamnet-benchmark.json), Windows 11 `10.0.26200`,
Python 3.12.14, CPU AMD64 Family 25 Model 68 Stepping 1.
100 измеряемых окон после 5 прогревочных; в замер включены AudioData, inference и свёртка классов.
Среднее **3,693 мс**, p95 **4,664 мс**, максимум **6,758 мс**: критерий ≤30 мс **PASS**.
Загрузка MediaPipe/модели отдельно: 680 мс. Измерены тишина/шум/тон, это не проверка точности на речи.

## Далее

Соединение с Silero по OR (0,5 / 0,3), отдельный worker, отказоустойчивость, жизненный цикл и LIVE
в этом checkpoint ещё не включены. Исторический LIVE Silero не выдаётся за новый тест YAMNet.
