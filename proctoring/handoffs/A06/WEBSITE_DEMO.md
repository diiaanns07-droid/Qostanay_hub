# Видео: разрешённый сайт в Adal

Проверено на ноутбуке в настоящем Electron 43.7.5, **без enforce**.
Для короткой сцены используйте готовое демонстрационное окно: оно использует production `ExamSurface`
и настоящую сеть, а состояние класса/экзамена подставляет локально. Backend, камера, микрофон, kiosk,
возврат фокуса и native helper в этом запуске не включаются. Это демонстрация режима сайта, не полного класса.

## Запуск для записи, 30–45 секунд

PowerShell, checkout этой ветки (либо интеграции после merge):

```powershell
Set-Location C:\Qostanay_hub-codex-proctoring-prompts\A06-env\proctoring\desktop
$env:QORGAU_SHELL_NATIVE_ENFORCE = '0'
$env:ADAL_WEBSITE_URL = 'https://school.moodledemo.net/'
$env:ADAL_WEBSITE_ALLOWED_URLS = '["https://school.moodledemo.net/","https://school.moodledemo.net/*"]'
node main/tools/run-website-demo.mjs --show
```

Зависимости берутся из установленного `desktop/node_modules`; launcher сам собирает своё окно.
Первый адрес списка — стартовая страница. Второе правило разрешает ресурсы Moodle на том же домене.
Поддомены/другие домены автоматически не разрешаются. Общего env для сайта в production нет:
`ADAL_WEBSITE_*` — только параметры этого demo launcher.

1. Покажите заголовок «сайт экзамена · демонстрация без enforce», адрес Moodle и страницу **Mount Orange**.
   Фраза: «Внутри Adal открыт сайт из белого списка преподавателя».
2. Нажмите сверху **«Посторонний адрес»**. Это попытка страницы перейти на `https://example.com/`.
   Moodle остаётся на месте, внизу: «Адрес вне белого списка — заблокировано».
3. Нажмите **«Новое окно»**: вызывается `window.open` разрешённого адреса. Нового окна нет;
   внизу: «Новое окно сайта — заблокировано».
4. По желанию **«Печать»**, Ctrl+P/S/U, правый клик внутри Moodle: печать/сохранение/код/меню не открываются.
   Фраза: «Ограничения действуют в окне экзамена. В этом показе системный enforce выключен».
5. **«Завершить»** или обычное закрытие окна. Alt+Tab остаётся доступен. Автовыход — через 5 минут.

При первом открытии launcher автоматически проверяет три кнопки; после появления «готово» их можно
повторить для записи. Он ничего не отправляет в формы сайта и не входит в аккаунт.

## Проверить без видимого окна / без интернета

```powershell
node main/tools/run-website-demo.mjs
# По умолчанию JSON-результат: desktop/dist/website-demo-result.json; exit code 0 = PASS.

# Если интернет недоступен: свой HTTP fixture на случайном loopback-порту, тот же ExamSurface.
$env:ADAL_WEBSITE_URL = 'local'
Remove-Item Env:ADAL_WEBSITE_ALLOWED_URLS -ErrorAction SilentlyContinue
node main/tools/run-website-demo.mjs --show
```

Для собственного сайта задайте **его точный стартовый URL первым**, затем только необходимые
HTTP(S) адреса ресурсов/входа. Поддерживаются точный origin+path и `/*` в конце пути; не `*.example.com`.
Google Forms: нужен реальный опубликованный URL формы и отдельные разрешённые пути её CDN/входа.
SSO через popup намеренно запрещён; матрица совместимости Google Forms/SSO здесь не проверялась.
`school.moodledemo.net` — официальный учебный демо-сайт, указанный на [moodle.org/demo](https://moodle.org/demo/).
На самом `moodle.org/demo/` этот ноутбук получил антибот-страницу «Один момент…»: не используйте её
для записи. Переходы на `moodle.org`, `moodle.com` и `sandbox.moodledemo.net` текущим списком не разрешены.

## В настоящем классе друга

Сайт назначается через `welcome.exam` / `class_state.exam` от сервера класса, например:

```json
{"exam_id":"moodle-demo","title":"Moodle demo","mode":"url",
 "allowed_urls":["https://school.moodledemo.net/","https://school.moodledemo.net/*"],
 "allowed_apps":[],"instructions_ru":"Демонстрация разрешённого сайта"}
```

В существующем сервере преподавателя можно создать сессию через teacher API. Не создавайте её повторно
во время действующего экзамена. Команды из `proctoring/`, PIN берётся из запущенного сервера:

```powershell
$env:QORGAU_CLASS_TEACHER_PIN = '<PIN сервера преподавателя>'
@'
import os
from proctor.uplink.demo_teacher import Teacher
t = Teacher('127.0.0.1:8765', os.environ['QORGAU_CLASS_TEACHER_PIN'])
s = t.call('POST', '/api/teacher/session', {'title':'Moodle demo', 'mode':'url',
    'allowed_urls':['https://school.moodledemo.net/','https://school.moodledemo.net/*']})
print('Код подключения:', s['join_code'])
'@ | .venv\Scripts\python.exe -

# В терминале студента, из proctoring/desktop:
$env:QORGAU_CLASS_SERVER = '127.0.0.1:8765'  # или IP компьютера преподавателя
$env:QORGAU_CLASS_CODE = '<полученный шестизначный код>'
$env:QORGAU_CLASS_LABEL = 'Студент · сайт'
$env:QORGAU_SHELL_NATIVE_ENFORCE = '0'
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
npm start
```

Далее обычная подготовка сессии/согласие/preflight/калибровка → RUNNING; сайт отображается при связи
с классом и снятой блокировке. В полной оболочке RUNNING включает оконный kiosk и clipboard policy
даже при `NATIVE_ENFORCE=0`; для видео без этих ограничений используйте launcher выше.
Команда teacher API сверена с текущим кодом, но полный сервер → uplink → студент в этой задаче не запускался.

## Фактические результаты

- [Moodle, реальная сеть](checks/print-2026-10-08/moodle-school.json): PASS — заголовок и содержимое **Mount Orange**,
  URL сохранился при постороннем переходе, popup и печать отменены, события получены, kiosk/top выключены.
- [moodle.org/demo: challenge](checks/print-2026-10-08/moodle.json): проверка содержимого FAIL,
  хотя документ загрузился. Этот отрицательный результат сохранён; загрузка документа сама по себе
  не считается успешным открытием экзаменационного сайта.
- [Локальная страница](checks/print-2026-10-08/local.json): воспроизводимая проверка без внешнего сервера.
- `node main/tests/exam-surface.mjs`: 22 runtime self-test пункта и 16 сценариев Chromium,
  включая iframe, whitelist ресурсов/redirect, popup/download и очистку при pause/finish.
- LIVE с физическим нажатием клавиш/перетаскиванием в другое приложение и полноценный Forms/SSO
  не выполнялись. Chromium input/DOM pipeline проверен автоматизированно на этом ноутбуке.

Документация режима и ограничений: [PRINT-2026-10-08.md](PRINT-2026-10-08.md).
