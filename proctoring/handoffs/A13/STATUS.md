# A13 — STATUS (тот же человек, что в начале экзамена)

* Роль: A13. Что делает модуль: **контроль, что за компьютером тот же человек, что в начале экзамена**. Сравнение идёт
  локально и только в памяти, базы лиц нет. Это **не** биометрическая идентификация: модуль не знает, кто человек, и не
  сверяет его с документом или списком. Это **не** защита от подделки (фото, маска, видео не распознаются).
* Пути: `proctoring/backend/proctor/identity/`, `proctoring/handoffs/A13/`. Разрешённые A01 правки:
  `backend/proctor/app.py`, `backend/proctor/session.py` (см. DEPENDENCIES.txt §1).
* Ветка `codex/proctor-A13` от `codex/proctor-integration` @ `6263aef9aabdee678b96d44b11cfec9e565370f7`. Контракт 1.1
  (`IdentityObservation`, `IncidentRule.IDENTITY_MISMATCH`, `Component.IDENTITY`) не менялся.

## Как работает

| Шаг | Что происходит |
|---|---|
| Кадры | только от A02: `capture.add_consumer("identity", …, max_fps=4)`. Камеру и файлы модуль не открывает. |
| До RUNNING | (preflight, калибровка) модели не запускаются, наблюдение `unknown`, `reasons=["exam_not_started"]`, 1 раз/с. |
| Эталон | A01 вызывает `exam_started(t)` при переходе в RUNNING. Дальше анализ 2 раза/с; кадр берётся в эталон, только если на нём **ровно одно** лицо YuNet (score ≥ 0.9, сторона ≥ 60 px). После 5 таких кадров эталон = медиана 5 признаков SFace (L2-норма). Каждый из 5 должен совпадать с медианой по тому же порогу, иначе несовпавшие отбрасываются и набор продолжается (эталон не может быть смесью двух людей). Пока эталона нет: `enrolled=false`, `same_person=unknown`, `reasons=["enrolling"]` (после 3 с ещё `"enrollment_delayed"`). |
| Сверка | 1 раз в секунду (`interval_ms=1000`, меньше задать нельзя): косинусная близость к эталону. `present`, если ≥ **0.363**, `absent`, если ниже (`reasons=["below_threshold"]`). |
| Не могу сказать | `unknown` без similarity: `no_face`, `multiple_faces` (это ловит A04), `face_too_small`, `model_missing`/`model_invalid`/`config_invalid` (status=error), `analyzer_error`. При отсутствии модели **никогда** не `present`. |
| Пауза/продолжение | эталон сохраняется (повторный `exam_started` ничего не делает). |
| Конец сессии | `end_session()` удаляет эталон и все признаки. |

**Порог 0.363 — рекомендация OpenCV для SFace (cosine), не подбирался на наших данных.** Источники:
`opencv/opencv_zoo` → `models/face_recognition_sface/sface.py` (`self._threshold_cosine = 0.363`, NormL2 1.128) и
`opencv/opencv` → `samples/dnn/face_detect.py` (`cosine_similarity_threshold = 0.363`). Порог детекции 0.9, NMS 0.3,
top_k 5000 — значения по умолчанию из того же `face_detect.py`.

**Приватность.** Признаки лица (128 чисел) и кадры не пишутся на диск, не уходят в WebSocket, в fusion, в отчёт или
uplink. Из модуля выходит только число `similarity` (4 знака) в `IdentityObservation` и флаг `enrolled`. Это проверено
тестом `test_only_similarity_leaves_the_module`: поля наблюдения совпадают с контрактом, файлов не создаётся.

## Модели (OpenCV Zoo, проверено по официальному репозиторию 2026-10-08)

| Роль | Файл | Лицензия | Размер | sha256 |
|---|---|---|---|---|
| детектор лица, `cv2.FaceDetectorYN` | `face_detection_yunet_2023mar.onnx` | MIT (`models/face_detection_yunet/LICENSE`, © 2020 Shiqi Yu) | 232 589 B | `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4` |
| признак лица, `cv2.FaceRecognizerSF` | `face_recognition_sface_2021dec.onnx` (fp32) | Apache-2.0 (README: «All files in this directory are licensed under Apache 2.0 License») | 38 696 353 B | `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79` |

URL: `https://github.com/opencv/opencv_zoo/raw/main/models/<dir>/<file>` (git LFS, отдаётся через
`media.githubusercontent.com`). YuNet 2023mar выбран потому, что 2026may требует OpenCV 5, а у нас
opencv-contrib-python 4.13.0.92. Manifest: `backend/proctor/identity/models.manifest.json`. Контрактный
`ModelManifest.module` допускает только phone/attention, поэтому у A13 своя схема `qorgau.identity.models/1` с теми же
полями плюс `role` и `license_url`.

Подготовка **до** экзамена (runtime ничего не скачивает):
```powershell
cd proctoring
.venv\Scripts\python -m proctor.identity.prepare --download   # https only, size+sha256, .part -> rename
.venv\Scripts\python -m proctor.identity.prepare --check      # offline: sha256 + загрузка в OpenCV
```
Папка: `QORGAU_MODELS_DIR`, иначе `%LOCALAPPDATA%\QorgauExam\models`; файлы ложатся в `identity\` вместе с копией
manifest (URL, лицензия, sha256, `installed_at`). Backend должен запускаться с тем же `QORGAU_MODELS_DIR`. Runtime
читает файл один раз, сверяет sha256 **этих байтов** и отдаёт их OpenCV из памяти (нет второго чтения, нет проблем
OpenCV с кириллицей в пути). Веса в Git не попадают (`*.onnx` в `.gitignore`).

Health (`Component.IDENTITY`): `model_loaded` (OK) · `model_missing` / `model_invalid` / `manifest_invalid` /
`config_invalid` / `opencv_unavailable` (UNAVAILABLE) · `closed` (STOPPED). Сообщение `model_missing` называет
`models/identity/<file>` и команду подготовки, без абсолютного пути. В preflight отдельной строки нет (enum
`PreflightCheckId` не менялся), поэтому без моделей A13 экзамен не блокируется. Это видно в `/v1/health` и в
наблюдениях `unknown/model_missing` раз в секунду.

## Проверки (Windows-ноутбук команды, 2026-10-08)

| Что | Результат |
|---|---|
| `pytest backend/proctor/identity` (заглушки FaceDetectorYN/FaceRecognizerSF + manifest/prepare + real-model + wiring + сводка LIVE) | **28 passed** (real-model не пропущен: веса установлены) |
| из них: эталон набирается (5 кадров, 2 Гц, enrolled на 5-м) / не набирается (нет лица, 2 лица, мелкое лицо, до RUNNING) | pass |
| смена человека → `absent`, similarity < 0.363, `below_threshold`; возврат → `present` | pass |
| нет лица / 2 лица после эталона → `unknown`, similarity = null | pass |
| нет модели → health `model_missing`, наблюдения `unknown` 1 раз/с, никогда `present` | pass |
| смешанный эталон (A,A,B,A,A) → B отброшен, эталон по A | pass |
| wiring: live-сессия с фейковой камерой → до RUNNING эталона нет, после есть, наблюдения доходят до fusion, finish сбрасывает эталон; synthetic-сессия без identity | pass |
| полный `pytest -q` | 1038 passed, 40 skipped, **4 failed, не из-за A13**: 3 в `phone/tests/test_config_manifest_prepare.py` (`install_success_writes_atomically`, `install_source_error_…` — Windows `\` в пути; `app_health_lists_phone_model_missing` — сетевой guard ловит `socket.connect`) падают одинаково с app.py/session.py из `6263aef` и из этой ветки. 4-й «плавает» между прогонами: в одном `proctor_classctl/…::test_bad_command_inputs`, в другом `phone/…::test_app_health_lists_phone_config_invalid`. Отдельно оба проходят, в том числе после всех identity- и backend-тестов |
| `prepare --download` → `--check` на ноутбуке | оба файла скачаны, sha256 совпали, `model_loaded` |
| время анализа (детекция + признак, кадр 640×480 с лицом) | p50 **25 мс**, max 60 мс (20 прогонов). При 1 Гц это ~2.5 % одного ядра |

### Реальные числа на роликах A02 (согласие записано в их manifest; кадры не сохранялись)

`python -m proctor.identity.live --replay <id>`, отчёты в `%LOCALAPPDATA%\QorgauExam\a13-live\` (только числа).
**Это фактическая запись по одному человеку, а не измерение точности.**

| Ролик | Эталон | similarity того же человека | absent | unknown |
|---|---|---|---|---|
| `zone_a_green_01` (30 с, один человек) | за 2.6 с | min 0.654 (короткий взгляд вправо 15.5–16.5 с), медиана 0.854, max 0.922 | 0 из 31 | 4 (набор эталона) |
| `zone_c_red_01` (40 с: телефон, уход 14.5–26.5 с, второй человек в маске с 32 с) | за 2.3 с | min 0.563, медиана 0.768, max 0.973 | 0 из 40 | 16: 4 на набор эталона и 12 `no_face`, пока студента нет |

На `zone_c_red_01` YuNet не нашёл лицо второго человека в маске (по разметке 2 лица в 36.5–40 с). Поэтому там
`present` по студенту, а не `multiple_faces`. Маска закрывает лицо, это ожидаемо и описано как ограничение.

## LIVE: смена человека — ПЛАН (не выполнено)

Нужны два человека, второй только с устного согласия. Ничего не записывается, в отчёт идут только числа.
```powershell
cd proctoring
.venv\Scripts\python -m proctor.identity.live --camera 0 --seconds 40 --swap-at 20 --label "капитан, затем <имя> (устное согласие, 08.10.2026)"
```
0–3 с — капитан смотрит в камеру (эталон). На 20-й секунде терминал печатает `SWAP NOW`: капитан встаёт, садится
второй человек. Записать в таблицу выше `before_swap` / `after_swap` и `longest_absent_run_s` из итоговой сводки.
Ожидание по порогу OpenCV: после смены similarity < 0.363 и `absent` ≥ 3 с подряд. Это гипотеза, пока не измерено.

## Ограничения (говорить честно)
* Не идентификация личности и не защита от подделки: фото или видео того же человека пройдут как `present`.
* Маска, сильный поворот головы, плохой свет и лицо меньше 60 px дают `unknown` (или пониженную similarity), а не `absent`.
* Эталон берётся с того, кто сидит в первые ~3 с после старта. Если в это время сидит не тот человек, модуль будет
  «следить» за ним. Модуль сравнивает с началом экзамена, а не с документом.
* Порог 0.363 не калиброван на наших камерах и людях. Похожие люди (близнецы, родственники) могут дать `present`.
