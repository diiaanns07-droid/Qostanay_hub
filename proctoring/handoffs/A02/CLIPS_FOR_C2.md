# Клипы эпизодов для режима класса — интерфейс A02 → C2 (uplink студента)

Протокол: `qorgau.class.v1` §5 (`proctoring/contracts/class/PROTOCOL_v1.md`, baseline `78798b7`). Владелец: A02
(`proctor.capture`). C2 вызывает только публичные методы ниже и не открывает камеру.

## Интерфейс
```python
from proctor.capture import ClipError, ClipResult          # и FrameCaptureService (сервис захвата из ModuleRegistry)

capture.export_clip(t_center_session_ms: float, before_s: float = 5.0, after_s: float = 5.0,
                    *, out_dir: str | Path | None = None, name: str | None = None) -> Path
capture.export_clip_result(...same...) -> ClipResult        # path, frames, t_first_ms, t_last_ms, bytes,
                                                            # width, height, fps, partial, content_type
capture.clip_buffer_stats() -> dict | None                  # frames, bytes, max_bytes, t_first_ms, t_last_ms
```
* `t_center_session_ms` — время сессии (`Incident.t_start_ms`), не wall-clock. Клип = 5 с до и 5 с после начала.
* **Блокирует** до прихода кадров `t + after_s` (не дольше `after_s + 3 с`). Вызывать в отдельном потоке.
* Файл: MJPG `.avi`, `content_type = "video/x-msvideo"`, ≤ 8 МБ, в `%TEMP%\qorgau-clips\` (вне Git),
  имя `<session>-t<ms>-<random>.avi`. Удалять файл после загрузки — задача C2.
* `partial=True`: окно покрыто не полностью (камера остановилась, буфер короче окна, таймаут). Файл есть, но неполный.

## Когда вызывать (важно)
Буфер хранит **только последние 10 с**. Поэтому `export_clip` нужно вызывать **сразу при открытии эпизода**
(`incident` со `state:"open"`), сохранить путь и `clip_available: true`, а по команде `request_clip {incident_id}`
загрузить уже готовый файл: `POST /api/student/clips/{incident_id}`, `Authorization: Bearer <resume_token>`,
`Content-Type: video/x-msvideo`. Если вызвать при `request_clip` через минуту, будет `ClipError("no_frames")`.

```python
import threading
def on_incident_opened(inc):                      # C2, в потоке uplink
    def work():
        try:
            path = capture.export_clip(inc.t_start_ms, 5.0, 5.0)
            clips[inc.incident_id] = path            # clip_available = True
        except ClipError as e:                       # e.code: no_frames | disk_full | disk_error | encoder_failed | too_large | invalid_argument
            log.warning("clip %s: %s", inc.incident_id, e.code)  # clip_available = False
    threading.Thread(target=work, daemon=True).start()
```

## Ошибки (`ClipError.code`)
| code | когда |
|---|---|
| `no_frames` | нет кадров в окне (камера не запущена, клипы выключены, окно старше 10 с) |
| `invalid_argument` | нечисловое/отрицательное время или `before_s + after_s > 10` |
| `disk_full` | во временной папке меньше 64 МБ |
| `disk_error` | не удалось создать папку/записать/переименовать файл |
| `encoder_failed` | нет OpenCV или `VideoWriter` не открылся |
| `too_large` | даже после запасных шагов файл > 8 МБ |
Запасные шаги для лимита 8 МБ: качество 75 → 50 → каждый второй кадр (половина fps) → половина размера кадра.

## Память и цифры (измерено 08.10, Ryzen 5 7535HS, Windows 11)
* Буфер: кадры вписаны в 640×360 с сохранением пропорций (4:3 → 480×360), JPEG q70, ~15 FPS, 10 с.
  Без сжатия было бы 10 с × 15 × 691 КБ ≈ 104 МБ — так не храним. Жёсткий предел 24 МБ (старые кадры вытесняются),
  не больше 600 кадров.
* Реальные кадры камеры (ролики replay): буфер 2,1–2,3 МБ за 10 с, `add()` 1,1–1,5 мс на кадр, клип 10 с —
  3,8–4,3 МБ. Живая камера: экспорт ждал 5,3 с, 127 кадров, 2,8 МБ, буфер 1,6 МБ.
* Худший случай (синтетический шум): буфер 10,3 МБ за 10 с (< 24 МБ).
* Отключение (тесты/слабые машины): `FrameCaptureService(settings, clip_ring_s=0)`.

## Не входит / ограничения
* `.mp4` не пишется (только MJPG `.avi`, протокол допускает `video/x-msvideo`).
* Клип показывает человека: хранить только во временной папке, загружать только по запросу преподавателя, не коммитить.
* Времена на Windows/Python 3.12 квантованы ~16 мс (A02 R12).
