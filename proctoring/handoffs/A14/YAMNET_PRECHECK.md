# A14 YAMNet — исходное состояние, 2026-10-08

Ветка: `codex/proctor-A14-yamnet`.
Точная база: `b4cce9c141fd73f0db326cffac7e7cd18eb57c89` из `codex/proctor-integration`.
Checkout: `worktrees/A14-yamnet`. Изменения разрешены только в `backend/proctor/audio/` и `handoffs/A14/`.

Получены ветка, база, границы и срок push 21:00. В сообщении не указаны поведение YAMNet,
целевые классы или критерий приёмки; соответствующего задания нет и среди файлов этой базы.
Капитану отправлено уточнение: добавление к Silero, замена Silero или отдельная оценка,
требуемые классы звука и условия приёмки. Реализация YAMNet ещё не начата.

## Проверенная база

Прочитаны существующие `STATUS.md`, `vad.py`, `assets.py`, `prepare.py`, `monitor.py`,
`fusion.py`, тесты аудио и жизненного цикла. Текущий A14 не переписывается с нуля:

- Один владелец микрофона, mono 16 kHz / float32 / блок 512 отсчётов, ограниченная очередь.
- Silero ONNX и резервный режим по энергии сигнала; сводки раз в 0,5 с.
- В хранилище выходят скалярные наблюдения; PCM не сохраняется и не отправляется.
- `AudioFusion` вызывает правило A05. Жизненный цикл подключён в интеграции;
  REPLAY и SYNTHETIC не включают микрофон.
- Исторический LIVE A14 из `live-results.json` не подтверждает акустическую чувствительность;
  его статус в предыдущем handoff сохранён.

На Windows 11 / Python 3.12.14:

```powershell
$env:PYTHONPATH="$PWD\proctoring\backend;$PWD\proctoring\contracts\python"
& 'C:\Qostanay_hub-codex-proctoring-prompts\verify-candidate\proctoring\.venv\Scripts\python.exe' -m pytest proctoring/backend/proctor/audio/tests -q
```

**20 PASS**, 1 предупреждение устаревшего TestClient/httpx. Новый микрофонный LIVE не запускался.
Использован существующий venv кандидата; пакеты не устанавливались, shared lockfile не менялся.

| Зависимость | Доступность в проверенном venv |
|---|---|
| numpy | 2.4.6 |
| onnxruntime | 1.29.0 |
| sounddevice | 0.5.6 |
| pytest | 9.1.1 |
| tensorflow, torch, onnx, scipy, tflite_runtime | отсутствуют |

## Технические данные для выбора реализации

Официальный YAMNet выдаёт 521 класс звука; вход — mono 16 kHz.
Первая оценка требует не менее 975 мс звука, поэтому его результат нельзя подставить
как независимую оценку каждого текущего 32-мс блока Silero.
Понадобится явно определить окно, временную привязку и сочетание результатов двух моделей.
Источник: [официальный README TensorFlow](https://github.com/tensorflow/models/tree/master/research/audioset/yamnet).

Официальный пример использует TensorFlow Hub и возвращает scores, embeddings и spectrogram.
Текущий venv такого runtime не содержит. Формат артефакта и способ его подготовки предстоит выбрать
после получения задания; совместимость конкретного ONNX/TFLite экспорта пока не проверена.
Источник: [TensorFlow YAMNet tutorial](https://www.tensorflow.org/hub/tutorials/yamnet).

Не заявляется: YAMNet inference, проверка классов/точности, CPU latency, fallback нового детектора или LIVE.
Этот checkpoint фиксирует подготовку и тесты существующего A14, а не поставку YAMNet.
