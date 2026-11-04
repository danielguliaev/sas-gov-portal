# Эксплуатация Правовой библиотеки штата Сан Андреас

Документ описывает текущие команды и границы поставки. Сеть отдельных ведомственных сайтов отложена по решению заказчика. Правила правоприменения и наполнение реальным законодательством сюда не входят.

## Локальная установка Windows

В каталоге `sas-gov-portal` выполните двойной щелчок по `start_portal.bat`. Требуется Python 3.11 или 3.12. Скрипт создаёт виртуальную среду `.venv-portal`, устанавливает зависимости, запускает сервер на `http://127.0.0.1:8000` и открывает браузер.

Для обновления исходников: `git switch feat/legislative-system-v1`, затем `git pull`; после остановки старого процесса запустить `start_portal.bat`.

## Миграции и история

База `SQLite`, управляется параметром `DATABASE_PATH` (по умолчанию `./sas_portal.db`). Первая миграция создаёт таблицы, вторая добавляет `article_revisions.display_snapshot`. Новые редакции сохраняют названия категорий, полей и выбранных вариантов на момент публикации. История, созданная **до** второй миграции, не может задним числом восстановить первоначальные названия переименованных справочников.

Приложение применяет миграции при запуске. Для обслуживания с резервным копированием перед миграцией остановите приложение и выполните:

```powershell
.\.venv-portal\Scripts\python.exe scripts\db_maintenance.py backup
.\.venv-portal\Scripts\python.exe scripts\db_maintenance.py migrate
.\.venv-portal\Scripts\python.exe scripts\db_maintenance.py verify
```

Откат не выполняется автоматическим удалением столбцов с историческими данными. Безопасное возвращение к предыдущему состоянию выполняется из проверенной резервной копии после остановки приложения.

## Резервное копирование и восстановление

Резервные копии создаются через SQLite `Connection.backup()` (согласованный снимок даже в режиме WAL). После записи выполняется `PRAGMA integrity_check` и создаётся манифест с SHA-256, версиями схемы и временем UTC.

```powershell
.\.venv-portal\Scripts\python.exe scripts\db_maintenance.py backup
.\.venv-portal\Scripts\python.exe scripts\db_maintenance.py verify backups\library_YYYYMMDD_HHMMSS.sqlite
```

Для восстановления **сначала остановите сервер**, убедитесь в отсутствии других пишущих процессов, затем выполните:

```powershell
.\.venv-portal\Scripts\python.exe scripts\db_maintenance.py restore backups\library_YYYYMMDD_HHMMSS.sqlite --confirm
```

Команда перед восстановлением сохраняет аварийную копию текущей БД. После восстановления повторно проверяет целостность. Контрольное испытание восстановления включено в `tests/test_admin_workflows.py`. Настройка автоматического расписания, удалённого хранилища и сроков хранения резервных копий остаётся задачей внедрения.

## Внешняя авторизация (контракт IAM)

В development по умолчанию `AUTH_MODE=demo`, а тестовые роли отображаются в интерфейсе. В production приложение отказывается стартовать без `AUTH_MODE=oidc`.

Для интеграции с OIDC-провайдером необходимы:

```dotenv
APP_ENV=production
AUTH_MODE=oidc
OIDC_ISSUER=https://identity.example.org
OIDC_AUDIENCE=legal-library
OIDC_JWKS_URL=https://identity.example.org/.well-known/jwks.json
OIDC_AUTHORIZATION_URL=https://identity.example.org/oauth2/authorize
OIDC_TOKEN_URL=https://identity.example.org/oauth2/token
OIDC_CLIENT_ID=legal-library-public
OIDC_REDIRECT_URI=https://library.example.org/
OIDC_SCOPE=openid profile
RATE_LIMIT_PER_MINUTE=240
```

**Это только иллюстрация параметров, не рабочие адреса сервиса.** Их предоставляет команда IAM. Сервер проверяет JWT RS256 через JWKS, issuer и audience. Браузер использует OAuth Authorization Code + PKCE/S256 и проверку `state`, сервер обменивает код на токен через настроенный HTTPS endpoint. Провайдер должен поддерживать публичный клиент и назначение `roles` в токене. Данные внешнего провайдера в репозиторий не внесены. До реальной интеграции и совместных испытаний режим production не считается готовым.

## Отдельный read-only сервисный API

Пути `/api/v1/integration/codes`, `/api/v1/integration/articles` и `/api/v1/integration/articles/{id}` предоставляют структурированные данные для будущего подключения игрового клиента. Они **не вычисляют наказания** и не принимают запросов на изменение юридических текстов. Для доступа требуется отдельный Bearer-токен, SHA-256 которого хранится в `SERVICE_API_KEY_SHA256`.

Для выпуска ключа используйте секретный менеджер организации, а не хранение токена в исходном коде. При отсутствии ключа маршруты возвращают 401.

## HTTPS, безопасность и мониторинг

Приложение отдаёт заголовки `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, а API — `Cache-Control: no-store`. Включаемый ограничитель запросов возвращает 429 и `Retry-After`. Он **локальный для одного процесса** и не заменяет распределённый ограничитель на уровне защищённого прокси.

Для production обязательны HTTPS/TLS, обратный прокси, контроль доступа к БД и резервным копиям, журнал ошибок, алерты, проверка кода на безопасность, регулярные восстановления, надёжное развёртывание, метрики нагрузки и план обновлений. Они **не развёрнуты** этим репозиторием. Параметры RPO/RTO/SLO согласуются отдельно.

## Проверка

```bash
python -m pip install -e ".[test,browser]"
pytest -q
node --check web/app.js
node --check web/admin.js
node --check web/search.js
node --check web/notes.js
python -m playwright install chromium
RUN_BROWSER_TESTS=1 pytest -q tests/test_browser.py
```

В Windows для включения браузерных тестов используйте `$env:RUN_BROWSER_TESTS="1"` перед `pytest`.

## Открытые эксплуатационные зависимости

До внешней публикации остаются совместные испытания IAM, развёртывание с HTTPS, контроль резервирования по расписанию, наблюдаемость и оповещения, масштабирование базы/поиска и полная приёмка AT-01—AT-20. Продуктивное юридическое наполнение проводится отдельным редакционным процессом.
