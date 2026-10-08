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

## Шаг 2: подключено к AudioMonitor

- По умолчанию монитор использует `CombinedAudioDetector`. Silero обрабатывает прежние блоки по 512 отсчётов.
  Его средний score за полусекундную сводку сравнивается с **0,5**; YAMNet speech sum — с **0,3**.
  Срабатывание любого даёт `present`; `voice_probability = max(silero, yamnet_speech_sum)`.
  `AudioConfig.yamnet_threshold` / `QORGAU_AUDIO_YAMNET_THRESHOLD` задаёт порог YAMNet.
  Пока идёт прежняя калибровка шума, результат остаётся `unknown`.
- YAMNet загружается и выполняется в отдельном потоке. Окно **0,975 с**, шаг **0,512 с**;
  максимум одно ожидающее окно плюс одно в обработке. Переполнение заменяет ожидающее окно свежим.
  Колбэк микрофона не выполняет inference. Оценки старше **1,25 с** отбрасываются.
  Сброс при разрыве потока очищает PCM и меняет поколение, поэтому результат старого inference не возвращается.
  Stop закрывает классификатор; новый старт создаёт новый detector. PCM только в ограниченной памяти.
- Отсутствие/ошибка YAMNet: health `yamnet_unavailable`, Silero продолжает работу.
  Это ограничение health, а не признак недостоверности работающего Silero: его наблюдения сохраняют `status=ok`.
  Иначе существующий A05 отвергал бы их. `yamnet_unavailable` не добавляется в причины речевых наблюдений.
- Нет Silero: YAMNet работает вместе с прежним энергетическим fallback.
  Если поддержка только энергетическая, сохраняются `status=degraded`, `reasons=["energy_fallback"]`.
  Энергетический score — эвристика; он может реагировать на тоновые шумы.
- В reasons идут только положительные источники (`silero`, `yamnet.*`) и прежний контекст fallback/калибровки.
  Контракт, `audio/fusion.py`, A05 и зависимости не изменены. Runtime не обращается к сети.

Проверка интеграции:

```powershell
python -m pytest backend/proctor/audio/tests backend/proctor/fusion/tests backend/tests/test_lifecycle_api.py -q
```

Первый прогон шага 2: **222 PASS** (аудио, fusion, lifecycle); затем **6 PASS** тестов расчёта LIVE-долей.
Дополнительно **3 PASS**: передача YAMNet-only / Silero-only / energy-only наблюдений в неизменённое правило A05.
Финальный целевой прогон новых combined + live_report тестов: **40 PASS**.
Прежние тесты не редактировались. Проверены OR, неречевые классы, отказы загрузки/inference, очередь,
устаревание, сброс, освобождение worker и настоящий Silero + YAMNet на синтетической тишине с реальным темпом.
Это не LIVE: микрофон при этих проверках не открывался.

## LIVE: первый прогон PARTIAL, контролируемая тишина ожидает повтора

Результат 2026-10-08: [YAMNET_LIVE.md](YAMNET_LIVE.md),
[полные скалярные данные](yamnet-live-20261008-185239.json).
Капитан подтвердил фоновый разговор в calibration/silence: детектор корректно отметил речь,
но фаза тишины невалидна и получает `numeric_criterion_met="not_applicable"`.
Калибровка загрязнена речью; speech/whisper приведены с этой оговоркой, без полного LIVE PASS.
Повтор тишины — позже в тихом месте по готовности капитана.

Из чистого PowerShell на этом ноутбуке:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Qostanay_hub-codex-proctoring-prompts\worktrees\A14-yamnet\proctoring\handoffs\A14\Run-YamnetLive.ps1"
```

Перед повтором завершить текущий экзамен Adal, чтобы освободить микрофон. Запуск только по готовности капитана.
Остановка **Ctrl+C**. Скрипт ничего не устанавливает; `-CheckOnly` проверяет модели без микрофона
(проверено на ноутбуке: PASS). `-Python` позволяет явно указать существующий интерпретатор.

10 с до старта, 5 с калибровки в тишине, затем подсказки: **20 с тишины**, 5 с подготовки,
**20 с обычной речи с 1 м**, 5 с подготовки, **20 с шёпота с 1 м**. Всего около 85 с.
Сохраняется `yamnet-live-<дата-время>.json`: только контрактные наблюдения, числа и имена классов,
точная ОС, пороги, доли окон Silero / YAMNet / итог и число срабатываний только YAMNet. Звук не сохраняется.
Тишина: итог ≤10%; речь: итог ≥70%; шёпот: честные доли, без автоматического PASS.
Неполная выборка/unknown не проходит численный критерий. Акустические условия требуют подтверждения оператора
даже при выполнении численного критерия. Отдельную фазу можно повторить через `-Phase silence|speech_nearby|whisper_1m`.
Прерванный Ctrl+C тест не считается завершённым. Исторический LIVE Silero не выдаётся за новый LIVE YAMNet.
