# Данные и инструменты демонстрации

Для показа Adal используйте [актуальный сценарий](../docs/submission/ADAL_DEMO.md), [HTML-презентацию](../../presentation/adal/index.html) и [инструкцию запуска](../README.md).

Эта папка содержит технические материалы для повторяемой проверки backend:

| Путь | Назначение |
| --- | --- |
| `exams/demo_exam.json` | Демонстрационные вопросы; используются настройками backend |
| `verify_demo.py` | Проверка формата демонстрационных вопросов |
| `rehearse.py` | Ограниченная по времени проверка API в synthetic/replay |
| `replay/README.md` | Подготовка и воспроизведение записей; медиа остаются вне Git |
| `SOURCE_PROVENANCE.json` | Происхождение требований и регламента |
| `results/` | Исторические результаты с указанием проверенной ревизии |

`OPERATOR_CHECKLIST.md` и `REQUIREMENT_TO_DEMO.md` — материалы ранней версии. Текущие команды находятся в инструкции запуска выше. Старые PDF и дубли сценариев доступны в истории Git до `ac71d19`.

Из `proctoring/` с подготовленным Python:

```powershell
.\.venv\Scripts\python.exe demo\verify_demo.py
.\.venv\Scripts\python.exe demo\rehearse.py --mode synthetic --expected-sha <полный_SHA> --out <путь_к_результату.json>
```

Synthetic проверяет API на сценарных сигналах. Такой результат не подтверждает точность CV или блокировку Windows. Живую проверку проводите по [LIVE-чеклисту](../qa/scenarios/LIVE_TONIGHT.md).
