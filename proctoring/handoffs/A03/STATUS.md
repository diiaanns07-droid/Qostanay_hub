# A03 — STATUS (обнаружение телефона и признаки возможной съёмки)

* Роль: A03, CV-инженер обнаружения телефона. Пути: `proctoring/backend/proctor/phone/`, `proctoring/handoffs/A03/`.
* Ветка: `claude/focused-babbage-29mbea` (назначена платформой).
* База (контракт/baseline): BOOTSTRAP A01 `35bea4c7b28d2c622cf7ba26ff354273cc7b6c49` (`claude/nifty-ride-ux8e4j`),
  контракт `qorgau.v1` 1.0.0 — не изменялся. Предыдущий checkpoint A03: нет (это первый; SHA коммита сообщается вне файла).
* Этап: **checkpoint 1** — `create_phone_analyzer(settings)`, реальный локальный ONNX inference на onnxruntime CPU,
  manifest модели, `PhoneObservation` с детекциями/треками/сигналами, корректный health при отсутствии весов,
  инструменты оценки. Камеру модуль не открывает; кадры только от A02 через `FrameAnalyzer.process()`.

## Что работает (проверено в Linux-контейнере, без камеры/Windows/GPU)

| Часть | Файл | Статус |
|---|---|---|
| Фабрика `create_phone_analyzer(settings) -> FrameAnalyzer(name="phone")` | `phone/__init__.py`, `analyzer.py` | реализовано, проверено (protocol, A01 `/v1/health`, smoke) |
| Модель YOLO11n ONNX (официальный asset Ultralytics), manifest + sha256 | `models.manifest.json`, `manifest.py` | реальная загрузка + inference проверены |
| Подготовка весов до экзамена (явная, вне runtime) | `prepare.py` | `--download` / `--from-file` / `--check` проверены |
| Детектор: letterbox (rect), декодирование YOLO-head, NMS, класс по ИМЕНИ `cell phone` | `detector.py` | реальный inference, unit-тесты геометрии |
| Качество кадра (яркость/контраст/резкость) → `quality`, `quality_flags`, unknown | `quality.py` | эвристика, не калибрована |
| Трекинг: IoU + центр-гейт, выпадения ≤ 700 мс, несколько телефонов, лимит треков | `tracker.py` | fake-тесты + real-pixel motion check |
| Сигналы `phone_visible`, `phone_raised`, `possible_screen_capture` | `signals.py` | fake-тесты + real-pixel motion check; на клипах NOT EVALUATED |
| Оценка: фото (image-level), клипы (event-level), полусинтетика движения | `eval/` | инструменты готовы; клипов нет |

### Интерфейс (для A01/A05/A08/A07)
* `proctor.phone.create_phone_analyzer(settings)` — дёшево, без I/O; `load()` проверяет manifest + размер + SHA-256 файла
  `settings.models_dir/phone/yolo11n.onnx`, создаёт ORT-сессию (CPUExecutionProvider, 2 потока), warm-up. Никогда не
  скачивает и не бросает исключения. Health-коды: `model_loaded` (OK), `model_missing`, `model_invalid`,
  `manifest_invalid`, `runtime_missing`, `config_invalid` (UNAVAILABLE), `inference_errors` (DEGRADED, ≥20% ошибок),
  `closed` (STOPPED). Сообщение `model_missing` называет `models/phone/yolo11n.onnx` и команду подготовки, без абсолютных путей.
* `process(frame)` → ровно один `PhoneObservation` на кадр: `observation_id = "phone-<frame_id>"` (стабилен при повторной
  доставке), время/источник копируются из кадра, `producer = {module:"phone", version:"0.1.0", model_id:"yolo11n-coco-onnx",
  model_sha256, config_version:"phone-cfg-1.<sha12>"}`, `latency_ms` измерен от `t_capture_mono_ns`.
  Дубликаты/кадры не по порядку → `[]` (считаются), чужая сессия → `[]`. Неверный кадр (не ndarray, не uint8, не HxWx3,
  < 16 px) → одно наблюдение `status=error`, флаг `invalid_frame`, все сигналы `unknown`. Ошибка inference →
  `status=error`, `inference_error`. Нет модели → `status=error`, `model_unavailable`.
* `detections[]`: bbox нормализован в НЕзеркальном кадре; `confidence` = score детектора (не вероятность списывания);
  `track_id` (`ph-N`, уникален в сессии), `track_quality` (качество ассоциации, независимо от confidence), `track_age_ms`.
* Сигналы (точная семантика — docstring `signals.py`):
  * `phone_visible`: present (`detected_in_frame` | `detected_high_confidence` ≥0.5 | `track_coasting` ≤400 мс после
    пропуска, confidence=null) · unknown (`unconfirmed_detection`, `frame_unusable`) · absent (`no_detection`).
  * `phone_raised`: present (`track_rose_into_raise_zone`: центр поднялся ≥0.12 высоты кадра за ≤1.5 с и находится выше
    линии y=0.60; `appeared_in_raise_zone`: появился в зоне, ни разу не был ниже неё, держится ≥600 мс; далее
    `track_held_in_raise_zone`, гистерезис до y>0.68) · unknown (`insufficient_track_history`, `rise_below_threshold`,
    `track_unconfirmed`, `frame_unusable`) · absent (`no_phone_in_view`, `track_below_raise_zone`). Зона — полоса кадра
    (для веб-камеры над экраном это уровень груди/лица); калибровка A04 анализатору по контракту недоступна.
  * `possible_screen_capture`: **никогда не absent**. present (`steady_phone_in_front_of_screen_zone`) только при
    наблюдаемом шаблоне: подтверждённый трек неподвижен (смещение центра ≤0.035) ≥800 мс, крупный (площадь ≥0.012),
    в центральной верхней зоне (x∈[0.2,0.8], y≤0.55); факт `camera_direction_observable=false`. Иначе
    `insufficient_evidence` (`camera_side_not_observable` / `phone_not_in_view`) — честный пробел покрытия кейса.
    Это не «снимок сделан» и не направление камеры телефона.
* Доп. публичные атрибуты (вне протокола): `.manifest` (ModelManifest после load), `runtime_stats()` (p50/p95 inference и
  обработки, счётчики) — запрос в `DEPENDENCIES.txt` п.2.
* Конфигурация: `PhoneConfig` + переменные `QORGAU_PHONE_<FIELD>` (например `QORGAU_PHONE_INPUT_SIZE=480`,
  `QORGAU_PHONE_CONF_THRESHOLD=0.25`, `QORGAU_PHONE_INTRA_OP_THREADS=1`); период inference — `settings.phone_max_fps`
  (A01, 8 fps) + опционально `QORGAU_PHONE_MIN_INTERVAL_MS`. Неверное значение → health `config_invalid`.
  GPU: только опция `QORGAU_PHONE_EXECUTION_PROVIDER=auto`, не проверена (в lock только CPU-сборка ORT).

### Модель
`yolo11n-coco-onnx`: https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.onnx, 10 930 182 B,
sha256 `634279b40c07c6391472c51ad45b81ebc48706a9a1fe72dd3396322acd0c053b`, экспорт Ultralytics 8.3.237 (2025-12-12),
динамический H/W, `names[67]="cell phone"` (индекс берётся из names модели и сверяется с manifest).
**Лицензия AGPL-3.0** — перед любым распространением нужна проверка (DEPENDENCIES п.6). Веса вне Git (`models/**`
игнорируется). Почему не YOLOv8n: у YOLO11n есть официальный готовый ONNX — не нужен torch/ultralytics ни в runtime,
ни при подготовке; тот же формат выхода (YOLOv8n поддерживается кодом при замене manifest).

## Команды

```bash
cd proctoring
.venv/bin/python -m proctor.phone.prepare --download        # один раз, с сетью; или --from-file <копия>
.venv/bin/python -m proctor.phone.prepare --check           # офлайн: размер+sha256+загрузка ORT+warm-up
.venv/bin/python -m pytest -q backend/proctor/phone/tests   # 401 passed, 2 skipped (опциональный набор изображений)
.venv/bin/python -m proctor.phone.eval.images --images <dir> --labels <dir> --out <report.json>
.venv/bin/python -m proctor.phone.eval.clips --manifest <clips.json> --out <report.json> --split test
.venv/bin/python -m proctor.phone.eval.motion_check --image <photo.jpg>   # полусинтетика, не точность
```

## Проверки и результаты (2026-10-08, Linux x86_64, Intel Xeon 2.8 GHz 4 vCPU, Python 3.12.3, ORT 1.29.0 CPU)

| Проверка | Результат |
|---|---|
| `pytest backend/proctor/phone/tests` (fake + real-model) | **401 passed, 2 skipped** (skip = нет `QORGAU_PHONE_EVAL_IMAGES`) |
| полный `python -m pytest -q` с весами | 463 passed, **2 failed — тесты A01** (см. ниже), 2 skipped |
| полный набор A01 без весов | 63 passed, **1 failed — тест A01** |
| `python -m proctor smoke` | 34/34 PASS (synthetic; phone в synthetic остаётся скриптом A01) |
| `contracts/tools/generate.py --check`, `verify_ownership.py --self-test` | PASS, PASS |
| `verify_ownership.py --agent A03 --base 35bea4c…` | PASS (результат в финальном отчёте коммита) |

Падающие тесты A01 ожидаемы и вызваны самим фактом интеграции модуля (запрос A01 — `DEPENDENCIES.txt` п.1):
`test_health_reports_missing_modules_honestly` (ждёт `module_not_integrated`, получает честное `model_missing`/`model_loaded`)
и `test_live_never_falls_back_to_synthetic` (с установленными весами проверка `phone_model` честно `pass`).

### Реальная CV-проверка (отдельно от fake-тестов)
1. **Детектор на COCO val2017** (held-out: YOLO11n обучался на train2017; 5000 фото, 214 с телефонами, 262 бокса,
   697 hard negatives = фото с remote/book/laptop/mouse/keyboard/tv/clock/scissors/toothbrush без телефона; IoU≥0.5,
   640 rect, пол 0.05 → AP50 — нижняя граница):
   AP50 = **0.40**. При пороге по умолчанию 0.20: precision **105/187 = 0.56**, recall **105/262 = 0.40**;
   recall по размеру: крупные (площадь ≥2% кадра) **49/68**, средние **48/109**, мелкие **8/85**;
   негативные фото с ложным боксом **48/4786**, из них hard negatives **32/697** (чаще laptop 14, keyboard 11, tv 12, mouse 9,
   remote 9, book 8). При 0.30: P 92/136=0.68, R 92/262=0.35; при 0.50: P 66/82=0.80, R 66/262=0.25.
   inference p50 52 мс / p95 77 мс (640×480 rect, 2 потока).
   **input 480** (подмножество телефоны+hard negatives): recall 85/262 (крупные 47/68, средние 36/109), inference p50 28.5 мс.
   Это общие фотографии, не ракурс веб-камеры: число НЕ является точностью экзаменационного сценария.
2. **Полусинтетическая проверка движения** (`eval/motion_check.py`, реальные пиксели COCO val2017 000000179112 и
   000000120420, «подъём» = сдвиг окна кадрирования, 8 fps): один `track_id` на весь ролик; `phone_raised` →
   `track_rose_into_raise_zone` при подъёме центра с y≈0.79/0.74 до ≈0.50/0.54; `possible_screen_capture` → present
   только после ~0.6–0.8 с неподвижного удержания; до этого `insufficient_evidence`. Движение синтетическое
   (как наклон камеры), без смаза — точность не доказывает.
3. **Event/clip-level точность на записанных экзаменационных клипах: NOT EVALUATED** — клипов нет (в песочнице нет камеры;
   Wikimedia/Open Images/HF заблокированы сетевой политикой). План, категории и протокол: `eval/EVAL_PLAN.md`,
   `eval/dataset_manifest.json`, формат клипов `eval/clip_manifest.example.json`.

## Не проверено
Windows; реальная камера и A02-capture (интеграция только по контракту `FramePacket`); latency на демо-ноутбуке; GPU;
работа на реальных экзаменационных клипах (подъём рукой, смаз, слабый свет, калькулятор и т.п.); пороги не настроены
на клипах.

## Условия демонстрации для A10 (что честно показывать)
* Камера над экраном, лицо в верхней половине кадра, ровный свет; телефон крупно (≥ ~10% ширины кадра), 1–3 с.
* «Подъём»: телефон с колен/стола (вне кадра) поднять к уровню груди/лица за ~0.5–1 с и задержать — ожидаем `phone_raised`.
* «Возможная съёмка экрана»: держать телефон неподвижно тыльной стороной к монитору ≥1 с в центре верхней части кадра —
  сигнал означает только «наблюдаемый шаблон», не снимок; при другом ракурсе честно `insufficient_evidence`.
* Не обещать распознавание мелкого телефона далеко/в темноте (мелкие 8/85 на фото) и отсутствие ложных срабатываний
  на клавиатуре/ноутбуке/пульте; калькулятор не измерялся. Не подбирать скрытый «идеальный» ролик — показывать живой
  сценарий и replay-запись, помеченную как REPLAY.

## Блокеры
Нет блокеров для checkpoint 1. Нужны: решение A01 по двум тестам (п.1), записанные клипы для оценки (команда, через A02).

## Следующий шаг
Разбор findings независимого ревью; затем при наличии клипов — `eval.clips` (tune/test) и настройка порогов.

## Порядок интеграции
A02 (capture) → **A03** (+A04 параллельно) → A05 (правила по `PhoneObservation.signals`) → A08. A01 при интеграции
правит 2 своих теста (п.1); общий код и контракты A03 не менял.
