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

Разработка ведётся в `d-win`, проверенные изменения попадают в `pro-win` через pull request. Push в эти ветки и pull request запускают проверки и создают тестовую portable-сборку `Nodera-test-<commit>` в GitHub Actions. Она предназначена для ручного тестирования и не публикуется как обновление.

В `d-win` выполняются только быстрые проверки Ruff и синтаксиса Python, затем сборка; pytest и проверка форматирования пропускаются. В pull request в `pro-win` и в самой `pro-win` обязательны Ruff, проверка форматирования и весь pytest. При новом запуске для той же ветки или pull request предыдущая незавершённая проверка отменяется.

Версия задаётся в `nodera/__init__.py`. После переноса изменений в `pro-win` тег `v<версия>` запускает тесты, сборку и создание черновика релиза с установщиком. Опубликуйте проверенный черновик, чтобы обновление стало доступно клиентам. Ручной запуск release workflow с `release_tag` пересобирает указанный тег из `pro-win`; без тега доступна сборка установщика из `pro-win` без создания релиза.

[Подписки](subscriptions.md) · [Карта](route-display.md) · [Версии](versioning.md)
