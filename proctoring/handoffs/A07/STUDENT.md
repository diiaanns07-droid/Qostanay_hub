# A07-student — экраны студента в Electron (калибровка, блокировка, микрофон, класс)

Ветка `codex/proctor-A07-student` от `codex/proctor-integration` @ `5c6a8e4e18bf29eedee4181cb6dd0c6d79eb72dc`
(C2 `d6752e6`, A06-native checkpoint 1, T02–T05). Пути: `proctoring/desktop/renderer/`, `proctoring/handoffs/A07/` +
минимальная правка оболочки (разрешена A01, описана ниже). Основной STATUS A07 (`STATUS.md`) не менялся.

## 1. Калибровка на весь экран (push 1)
* `screens/Calibration.tsx`: фаза сбора — полноэкранный слой `.calfs` поверх всего окна; пять крупных точек у краёв
  экрана в порядке A04/A01 (`CalibrationTargetValues`: центр → влево → вправо → вверх → вниз), отступ 44 px от края.
  Активная точка 46 px, пульсирует, кольцо прогресса = `samples / required_samples` от backend (не таймер).
  Собранная точка — зелёная с «✓», несобранная — красная, плюс текст в панели («собрано ✓ / не собрано / ждёт»,
  причина «Точка «вверх» не собрана: …»): цвет всегда с текстом.
* Подсказка крупно: «Смотрите на точку глазами, голову держите прямо.»
* Вступление: точки не запрашиваются, пока студент не нажал «Начать калибровку». Нажатие (жест) разворачивает окно
  на весь экран и запускает автосбор. Уже начатая калибровка (перезагрузка окна) продолжается без вступления.
* Панель статуса — между верхней и центральной точкой, кнопки — между центральной и нижней; проверено, что они не
  перекрывают ни одну точку на 1366×768 и 1920×1080. После калибровки окно выходит из полного экрана
  (режим экзамена A06 потом включает свой полный экран сам).

### Правка оболочки (A01 разрешил, минимальная)
| Файл | Что |
|---|---|
| `desktop/main/src/ipc/channels.ts` | `SEND.windowFullscreen = "qorgau:send:window-fullscreen"` |
| `desktop/main/src/main.ts` (`registerIpc`) | `ipcMain.on(SEND.windowFullscreen, …)`: только доверенный отправитель (`trustedSender`, как у всех IPC), только `boolean`; выход из полного экрана игнорируется, пока включён режим экзамена; `mainWindow.setFullScreen(flag)` |
| `desktop/preload/src/preload.ts` | `contextBridge.exposeInMainWorld("qorgauWindow", { setFullscreen(on) })` — один фиксированный односторонний канал |
Контракт `QorgauBridge` (contracts/ts/bridge.ts, A01) не менялся. Почему не HTML Fullscreen API: оболочка A06
отклоняет все запросы разрешений (`setPermissionRequestHandler → false`), в том числе `fullscreen`.
В браузере/FIXTURE используется HTML Fullscreen API (`lib/windowControl.ts`).

## Проверки
| Команда (из `proctoring/desktop`) | Результат |
|---|---|
| `npm run typecheck` | PASS (contracts, main, renderer) |
| `npm run build` | PASS |
| `node renderer/tests/e2e-fixture.mjs` (FIXTURE, Chrome через `CHROMIUM_PATH`) | **54/56**. Новые проверки: слой на всё окно; точки у краёв (1366×768: центр 683,384 · лево 44 · право 1322 · верх 44 · низ 724); панель и кнопки не перекрывают точки; подсказка; до «Начать» точки не запрашиваются; несобранная точка — цветом и текстом. 2 FAIL — «no console errors» из-за 404 в `vite preview`, **так же на базе до правок** (42/44) |
| `node renderer/tests/real-bridge/run-real.mjs` (настоящий backend через A06 BackendSupervisor, синтетика) | 12/13, калибровка от реальных сэмплов PASS; 1 FAIL на последнем шаге (`locator.click` timeout после сохранения решения) — **так же на базе** |
| `npm run test:shell` | 1 падение `bridge API drives a synthetic session…` — **так же на базе** без моих правок |
Окружение для тестов: Playwright 1.x во временной папке (не зависимость проекта), Chrome из системы
(`CHROMIUM_PATH`); `run-real.mjs` теперь тоже читает `CHROMIUM_PATH`.

## НЕ проверено (пока)
* LIVE на этом ноутбуке: калибровка с камерой и «взгляд вниз 4–5 с → эпизод» — нужен человек перед камерой.
