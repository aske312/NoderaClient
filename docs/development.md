# Разработка

Windows x64, Python 3.12+; для установщика — Inno Setup 6.7+.

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python scripts/prepare_core.py
.venv\Scripts\python main.py
```

```powershell
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m ruff format --check .
.\scripts\build.ps1 -Installer
```

Результат: `dist/installers/Nodera-Setup.exe` и SHA-256. Для portable-передачи нужна вся папка `dist/builds/<build-id>/Nodera`.

Работа — в `d-win`: CI проверяет Ruff и синтаксис, затем собирает приложение. Полный прогон — в PR с целью `pro-win`, в самой `pro-win` и при выпуске.

После проверок `pro-win` продвигается без новых коммитов: `git switch pro-win`, `git merge --ff-only <проверенный-коммит>`, `git push origin pro-win`, `git switch d-win`. Перед этим синхронизировать ветки с origin. При расхождении истории остановиться.

Версия — в `nodera/__init__.py`, заметки — в `docs/releases/v<версия>.md`. Тег `v<версия>` на коммите `pro-win` запускает сборку черновика релиза. После ручной проверки установщика черновик публикуется и становится доступен обновлятору.
