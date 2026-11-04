# Nodera Client

Клиент Mihomo для Windows 10/11 x64. Python 3.12+, PySide6; ядро встроено.

## Запуск

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python scripts/prepare_core.py
.venv\Scripts\python main.py
```

В PyCharm: интерпретатор `.venv\Scripts\python.exe`, запуск `main.py`.

«Подписки → Добавить» → вставьте HTTPS-ссылку Mihomo → подключитесь кнопкой питания. В настройках выберите **Proxy** или **TUN**. Приложение всегда запускается с правами администратора: запрос Windows появляется до открытия главного окна. Подписка не меняет выбранный режим.

## Проверки и сборка

```powershell
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m ruff format --check .
.\scripts\build.ps1
# Установщик: на машине сборки нужен Inno Setup 6
.\scripts\build.ps1 -Installer
```

Тесты очищают `.pytest_cache/tmp`. Последняя сборка: `dist\Nodera.lnk`. Для portable-передачи нужна вся папка `dist\builds\<build-id>\Nodera`. Установщик: `dist\installers\Nodera-Setup-<версия>-x64.exe`. Python, Qt, библиотеки, ядро и геоданные входят в комплект; устанавливать зависимости на компьютере клиента не нужно. Проверка обновлений приложения — раздел «Приложение», источник — GitHub Releases.

## Особенности

- Несколько подписок, выбор серверов, локальные правила, карта, диагностика и журнал.
- Обновление подписок — раз в 6 часов, включая активный сеанс. Новая конфигурация проверяется и применяется без перезапуска ядра и ручного переподключения; существующие соединения продолжают использовать прежний маршрут, новые — обновлённые правила. При ошибке выполняется откат.
- Данные: `%LOCALAPPDATA%\NoderaClient`. Подписки защищены DPAPI текущего пользователя; системный прокси восстанавливается после отключения.
- Mihomo закреплён в `nodera/resources/mihomo.json`, проверяется по SHA-256 и обновляется вместе с клиентом.
- Базы GeoIP/GeoSite/ASN входят в сборку; версии и SHA-256 — `nodera/resources/geodata.json`.
- Подробный сетевой журнал: `%LOCALAPPDATA%\NoderaClient\logs\network.jsonl` (ротация 8 МБ × 4, секреты скрыты).

[Подписки](docs/subscriptions.md) · [Карта](docs/route-display.md) · [Версии](docs/versioning.md) · [Установка и обновления](docs/updates.md)
