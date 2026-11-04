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

Версия задаётся в `nodera/__init__.py`. Тег `v<версия>` запускает тесты, сборку и создание черновика релиза с установщиком. Опубликуйте проверенный черновик, чтобы обновление стало доступно клиентам. Ручной запуск workflow с `release_tag` пересобирает указанный тег.

[Подписки](subscriptions.md) · [Карта](route-display.md) · [Версии](versioning.md)
