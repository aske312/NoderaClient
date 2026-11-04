# Версии

Источник версии: `nodera/__init__.py`. Формат `MAJOR.MINOR.PATCH`: несовместимость / новые возможности / исправления. Меняется при подготовке релиза; тег — `v<версия>` после проверок.

`scripts/build_metadata.py` записывает UTC build ID, время и Git-ревизию в `nodera/resources/build-info.json`. Версия попадает в Windows FileVersion/ProductVersion, build ID — в PrivateBuild и подсказку интерфейса. Сборщик не меняет версию и не публикует релизы.

Упаковка: `packaging/Nodera.spec`, запуск — `scripts/build.ps1` (`-Clean` для полной пересборки). Результат: `dist/builds/<build-id>/Nodera`, ярлык — `dist/Nodera.lnk`. Старые сборки сохраняются.
