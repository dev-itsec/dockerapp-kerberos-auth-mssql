## Docker-приложение вне домена: подключение к Microsoft SQL Server через SQL Login и Kerberos

> [!NOTE]
> **Обновление от 2026-08-18: Добавлено автоматическое обновление Kerberos-билетов.**  
> В Kerberos-режим добавлен `k5start`: он запускает Gunicorn под своим управлением, контролирует срок действия TGT и получает новый билет из keytab до истечения текущего. 
> Также добавлен `Docker healthcheck`, который проверяет действующий Kerberos credential cache командой `klist -s`, доступность REST API и реальное подключение приложения к Microsoft SQL Server через `/api/health`.

**Цель проекта**: пошаговое развёртывание тестового приложения `dockerapp-kerberos-auth-mssql`  
**Окружение**: Ubuntu-контейнер на Docker-хосте вне домена `MATRIX.COM`, Microsoft SQL Server в домене.

### 1. Что демонстрирует стенд
Приложение в Docker-контейнере подключается к одной базе Microsoft SQL Server в двух режимах:

1. SQL Server Authentication — SQL login и пароль;
2. Kerberos — доменная service account и keytab без SQL login/password.

Один и тот же **FLASK REST API** выполняет **CRUD** над таблицей `dbo.DockerKerberosDemo`. В Kerberos-режиме `/api/health` дополнительно показывает фактический `auth_scheme=KERBEROS`.
```text
Браузер
   ↓ Fetch API / JSON
Gunicorn → Flask REST API в Ubuntu-контейнере
   ↓ FreeTDS + pyodbc
Microsoft SQL Server
   ├─ SQL login/password
   └─ Kerberos ticket → MSSQLSvc/lime.matrix.com:1433
```

### 2. Параметры стенда
| Параметр | Значение |
|---|---|
| Папка проекта | `dockerapp-kerberos-auth-mssql` |
| SQL Server FQDN | `lime.matrix.com` |
| SQL TCP port | `1433` |
| База | `SysAdminsTestDB` |
| Таблица | `dbo.DockerKerberosDemo` |
| SQL login | `srv_dockerapp-sql` |
| Домен AD | `MATRIX.COM` |
| Доменная учётка | `MATRIX\srv_dockerapp-kerberos` |
| Kerberos principal | `srv_dockerapp-kerberos@MATRIX.COM` |
| SQL service principal | `MSSQLSvc/lime.matrix.com:1433` |
| HTTP port приложения | `8888` |

### 3. Предварительные требования
- SQL Server слушает статический TCP/1433;
- `lime.matrix.com` разрешается в правильный IP с Docker-хоста и контейнера;
- время Docker-хоста, контейнера, SQL Server и контроллеров домена синхронизировано;
- SQL Server может обращаться к контроллерам домена;
- у администратора есть права создавать SQL logins/users и регистрировать SPN;
- установлен Docker Engine с Compose plugin;
- проект находится в каталоге `dockerapp-kerberos-auth-mssql`.

Проверка с Docker-хоста:
```bash
cd dockerapp-kerberos-auth-mssql
getent hosts lime.matrix.com
date -u
docker compose version
```

### 4. Создание базы, таблицы, пользователей и прав
Команды выполняются в SSMS под администратором SQL Server.

#### 4.1. База и таблица
```sql
USE [master];
GO

IF DB_ID(N'SysAdminsTestDB') IS NULL
    CREATE DATABASE [SysAdminsTestDB];
GO

USE [SysAdminsTestDB];
GO

IF OBJECT_ID(N'dbo.DockerKerberosDemo', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.DockerKerberosDemo
    (
        Id int IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_DockerKerberosDemo PRIMARY KEY,

        [Text] nvarchar(500) NOT NULL,

        UpdatedAt datetime2 NOT NULL
            CONSTRAINT DF_DockerKerberosDemo_UpdatedAt
            DEFAULT SYSUTCDATETIME()
    );
END;
GO
```

Таблица создаётся заранее, поэтому приложению не нужны `db_owner`, `CREATE TABLE` или `ALTER ON SCHEMA::dbo`.

#### 4.2. SQL login
```sql
USE [master];
GO

CREATE LOGIN [srv_dockerapp-sql]
WITH PASSWORD = N'passw@rd',
     CHECK_POLICY = ON,
     CHECK_EXPIRATION = ON,
     DEFAULT_DATABASE = [SysAdminsTestDB];
GO

USE [SysAdminsTestDB];
GO

CREATE USER [srv_dockerapp-sql]
FOR LOGIN [srv_dockerapp-sql];
GO

GRANT SELECT, INSERT, UPDATE, DELETE
ON OBJECT::dbo.DockerKerberosDemo
TO [srv_dockerapp-sql];
GO
```

#### 4.3. Доменный login для Kerberos
```sql
USE [master];
GO

CREATE LOGIN [MATRIX\srv_dockerapp-kerberos]
FROM WINDOWS
WITH DEFAULT_DATABASE = [SysAdminsTestDB];
GO

USE [SysAdminsTestDB];
GO

CREATE USER [MATRIX\srv_dockerapp-kerberos]
FOR LOGIN [MATRIX\srv_dockerapp-kerberos];
GO

GRANT SELECT, INSERT, UPDATE, DELETE
ON OBJECT::dbo.DockerKerberosDemo
TO [MATRIX\srv_dockerapp-kerberos];
GO
```

`VIEW SERVER STATE` и `VIEW SERVER PERFORMANCE STATE` не нужны: приложение получает схему собственной сессии через:
```sql
SELECT CONVERT(nvarchar(40), CONNECTIONPROPERTY('auth_scheme'));
```

Явный `CONVERT` обязателен для совместимости FreeTDS/pyodbc: без него результат `sql_variant` может вызвать `HY091 Descriptor type out of range`.

### 5. Подготовка SQL SPN

Приложение запрашивает точный SPN:
```text
MSSQLSvc/lime.matrix.com:1433
```

Он должен принадлежать фактической учётной записи службы SQL Server либо доменной учётной записи компьютера SQL-хоста. Нельзя назначать этот SQL SPN клиентской учётке `srv_dockerapp-kerberos`.

Проверка:
```powershell
setspn -Q MSSQLSvc/lime.matrix.com:1433
setspn -X
```

Если SPN отсутствует, сначала определить фактическую SQL service account, затем зарегистрировать:
```powershell
$SqlServiceIdentity = 'MATRIX\ФАКТИЧЕСКАЯ_УЧЕТКА_СЛУЖБЫ_SQL'
setspn -L $SqlServiceIdentity
setspn -S MSSQLSvc/lime.matrix.com:1433 $SqlServiceIdentity
```

Откат только что добавленного SPN:
```powershell
setspn -D MSSQLSvc/lime.matrix.com:1433 $SqlServiceIdentity
```

Если SQL Server работает от `LocalSystem`, `NetworkService` или `NT SERVICE\...`, SPN обычно регистрируется на computer account вида `MATRIX\ИМЯ_SQL_ХОСТА$`. Перед изменением обязательно проверить существующие SPN.

### 6. Создание keytab

**Зачем нужен keytab?**

Docker-хост не входит в домен `MATRIX.COM`, а процесс приложения внутри контейнера не может интерактивно ввести пароль доменной service account. Keytab позволяет приложению подтвердить свою доменную идентичность автоматически и без хранения пароля в connection string.

Keytab содержит Kerberos principal и долгосрочный криптографический ключ, сформированный из пароля доменной учётной записи для выбранного алгоритма шифрования и текущего KVNO. Пароль в открытом виде в keytab не хранится, однако находящегося в файле ключа достаточно для аутентификации от имени service account. Поэтому keytab необходимо защищать как пароль.

При старте контейнера происходит следующая последовательность:
```text
keytab, смонтированный read-only
        ↓
kinit -kt получает TGT для srv_dockerapp-kerberos@MATRIX.COM
        ↓
TGT сохраняется в FILE:/tmp/krb5cc_app
        ↓
FreeTDS запрашивает service ticket для MSSQLSvc/lime.matrix.com:1433
        ↓
SQL Server принимает Kerberos identity MATRIX\srv_dockerapp-kerberos
```

Keytab:
- не содержит SQL login или SQL-пароль;
- не является SQL service ticket — билеты выдаются KDC динамически;
- не заменяет SPN `MSSQLSvc/lime.matrix.com:1433`;
- позволяет контейнеру выполнить неинтерактивный `kinit`;
- становится недействительным после смены пароля AD-учётки и изменения KVNO.

Команда в CMD на сервере AD, успешно использованная для существующей AD-учётки:
```powershell
ktpass /out C:\Temp\srv_dockerapp-kerberos.keytab `
  /princ srv_dockerapp-kerberos@MATRIX.COM `
  /mapuser MATRIX\srv_dockerapp-kerberos `
  /ptype KRB5_NT_PRINCIPAL `
  /crypto AES256-SHA1 `
  /pass * `
  -setpass `
  -setupn
```

Особенности команды:
- `/pass *` интерактивно запрашивает текущий пароль;
- `-setpass` не меняет пароль AD-учётки;
- `-setupn` не меняет её UPN;
- `/mapop` не указан, поэтому используется стандартная операция `add`;
- AES256 keytab уже подтверждён успешным `kinit`.

Не запускать `ktpass` повторно без необходимости. Изменение пароля учётки повышает KVNO и делает старый keytab недействительным.

### 7. Разместить keytab на Docker-хосте
Копируем `srv_dockerapp-kerberos.keytab` на Docker-хост в папку `secrets`:
```bash
./secrets/srv_dockerapp-kerberos.keytab
```

Keytab не включается в Docker image и монтируется read-only.

Kerberos override должен содержать эквивалентную конфигурацию:
```yaml
services:
  app:
    environment:
      DB_AUTH_MODE: kerberos
      DB_USER: ""
      DB_PASSWORD: ""
      KRB5_PRINCIPAL: "srv_dockerapp-kerberos@MATRIX.COM"
      KRB5_REALM: "MATRIX.COM"
      KRB5_KEYTAB: /run/secrets/app.keytab
      KRB5CCNAME: FILE:/tmp/krb5cc_app
    volumes:
      - "./secrets/srv_dockerapp-kerberos.keytab:/run/secrets/app.keytab:ro"
```

### 8. Запуск с SQL-аутентификацией
Создать `.env` в каталоге `dockerapp-kerberos-auth-mssql`:

```dotenv
APP_PORT=8888
DB_SERVER=lime.matrix.com
DB_PORT=1433
DB_NAME=SysAdminsTestDB

# SQL Auth
DB_AUTH_MODE=sql
DB_USER=srv_dockerapp-sql
DB_PASSWORD=passw@rd

# FreeTDS: off, request, require или strict.
DB_ENCRYPTION=request

# Kerberos Auth
KRB5_PRINCIPAL=srv_dockerapp-kerberos@MATRIX.COM
KRB5_REALM=MATRIX.COM
KRB5_KEYTAB_PATH=./secrets/srv_dockerapp-kerberos.keytab
```

Проверить Compose без вывода итоговой конфигурации с паролем:
```bash
docker compose -f docker-compose.yml config --quiet
```

Запустить только основной Compose-файл:
```bash
docker compose -f docker-compose.yml up -d --build
docker compose -f docker-compose.yml logs 
```

Проверить веб-интерфейс:  
http://127.0.0.1:8888

![SQL-аутентификация](/images/SQL.png)

Ожидаемые признаки:
```text
Database table is ready
Starting gunicorn 26.0.0
```

Проверка:
```bash
curl -sS http://127.0.0.1:8888/api/health
```

Ожидается:
```json
{"api":true,"auth_scheme":"SQL","database":true,"database_name":"SysAdminsTestDB","mode":"sql","status":"ok"}
```

### 9. Переключение на Kerberos
Сначала остановить SQL-вариант:
```bash
docker compose -f docker-compose.yml down
```

Проверить объединённую конфигурацию:
```bash
docker compose -f docker-compose.yml -f docker-compose.kerberos.yml config --quiet
```

Запустить основной Compose и Kerberos override вместе:
```bash
docker compose -f docker-compose.yml -f docker-compose.kerberos.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.kerberos.yml logs
```

Проверить веб-интерфейс:  
http://127.0.0.1:8888

![KERBEROS-аутентификация](/images/KERBEROS.png)

Ожидается:
```text
Default principal: srv_dockerapp-kerberos@MATRIX.COM
krbtgt/MATRIX.COM@MATRIX.COM
Database table is ready
Starting gunicorn 26.0.0
```

Проверить health:
```bash
curl -sS http://127.0.0.1:8888/api/health
```

Ожидается:
```json
{"api":true,"auth_scheme":"KERBEROS","database":true,"database_name":"SysAdminsTestDB","mode":"kerberos","status":"ok"}
```

### 10. Проверка Kerberos tickets
После SQL-запроса выполнить:
```bash
docker compose -f docker-compose.yml -f docker-compose.kerberos.yml exec -T app klist
```

На успешно проверенном стенде получено:
```text
Ticket cache: FILE:/tmp/krb5cc_app
Default principal: srv_dockerapp-kerberos@MATRIX.COM

Valid starting     Expires            Service principal
08/17/26 12:15:57  08/17/26 22:15:57  krbtgt/MATRIX.COM@MATRIX.COM
        renew until 08/18/26 12:15:57
08/17/26 12:15:57  08/17/26 22:15:57  MSSQLSvc/lime.matrix.com:1433@MATRIX.COM
        renew until 08/18/26 12:15:57
```

Это подтверждает:
1. TGT выдан service account приложения;
2. KDC выдал SQL service ticket для точного SPN;
3. приложение обращалось к SQL Server по Kerberos.

`/api/health` со значением `auth_scheme=KERBEROS` является окончательным подтверждением со стороны SQL Server.

### 11. Возврат к SQL-аутентификации
Остановить Kerberos-конфигурацию:
```bash
docker compose -f docker-compose.yml -f docker-compose.kerberos.yml down
```

Запустить только основной файл:
```bash
docker compose -f docker-compose.yml up -d

curl -sS http://127.0.0.1:8888/api/health
```

Ожидается `mode=sql` и `auth_scheme=SQL`.

### 12. Проверка REST API и CRUD
Health:
```bash
curl -sS http://127.0.0.1:8888/api/health
```

Список:
```bash
curl -sS http://127.0.0.1:8888/api/items
```

Создание:
```bash
curl -sS -X POST http://127.0.0.1:8888/api/items \
  -H 'Content-Type: application/json' \
  -d '{"text":"Kerberos CRUD test"}'
```

Из ответа запомнить `id`, затем проверить изменение и удаление:
```bash
curl -sS -X PUT http://127.0.0.1:8888/api/items/ID \
  -H 'Content-Type: application/json' \
  -d '{"text":"Kerberos CRUD test updated"}'

curl -sS -X DELETE http://127.0.0.1:8888/api/items/ID
```

Вместо `ID` указать идентификатор временной записи. После теста запись должна быть удалена.

### 13. Диагностика
#### 13.1. Состояние контейнера

```bash
docker compose -f docker-compose.yml -f docker-compose.kerberos.yml ps -a

docker compose -f docker-compose.yml -f docker-compose.kerberos.yml logs --tail=200 app
```

#### 13.2. DNS, время и TCP из контейнера
```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.kerberos.yml \
  exec -T app getent hosts lime.matrix.com

docker compose \
  -f docker-compose.yml \
  -f docker-compose.kerberos.yml \
  exec -T app date -u

docker compose \
  -f docker-compose.yml \
  -f docker-compose.kerberos.yml \
  exec -T app /opt/venv/bin/python -c \
  "import socket; socket.create_connection(('lime.matrix.com',1433),5).close(); print('TCP/1433 OK')"
```

#### 13.3. Подробный Kerberos trace

Запустить одноразовый контейнер, не вмешиваясь в работающий:
```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.kerberos.yml \
  run --rm --no-deps \
  -e KRB5_TRACE=/dev/stderr \
  app klist
```

Entrypoint сначала выполнит `kinit`, а `KRB5_TRACE` покажет поиск KDC, используемый enctype и ошибки pre-authentication.

#### 13.4. Проверка SPN из AD
```powershell
setspn -Q MSSQLSvc/lime.matrix.com:1433
setspn -X
```
SPN должен быть уникальным и принадлежать фактической SQL service account/computer account.

#### 13.5. Типовые ошибки

| Ошибка | Вероятная причина | Проверка/исправление |
|---|---|---|
| `Cannot contact any KDC` | DNS, firewall, недоступен DC | проверить DNS, TCP/UDP 88, время |
| `Preauthentication failed` | неверный пароль/keytab, изменился KVNO, регистр principal | создать актуальный keytab после проверки AD |
| `Server not found in Kerberos database` | отсутствует или неверен SQL SPN | `setspn -Q MSSQLSvc/lime.matrix.com:1433` |
| `Cannot generate SSPI context` | дубликат SPN, DNS alias, время | `setspn -X`, проверить FQDN и часы |
| `Login failed for user MATRIX\...` | нет Windows login/user или прав на БД | проверить `CREATE LOGIN FROM WINDOWS`, user и GRANT |
| SQL error `262` | приложение пытается создать таблицу без DDL-прав | заранее создать таблицу администратором |
| `HY091 Descriptor type out of range` | raw `CONNECTIONPROPERTY` вернул `sql_variant` | использовать `CONVERT(nvarchar(40), CONNECTIONPROPERTY(...))` |
| `/api/health` = 503 | ошибка SQL или диагностики | посмотреть JSON ответа и traceback в логах |
| `/api/items` = 200, health = 503 | CRUD работает, сломан только диагностический запрос | проверить типизированный `CONNECTIONPROPERTY` |
| `auth_scheme=NTLM` | Kerberos не использован | проверить точный FQDN/SPN, не подключаться по IP |
| `auth_scheme=SQL` в Kerberos-режиме | запущен только основной Compose или остались SQL credentials | проверить оба `-f`, `DB_AUTH_MODE`, пересоздать контейнер |

#### 13.6. Ticket lifetime

Entrypoint выполняет `kinit` один раз при старте. После истечения TGT новые SQL-соединения могут перестать открываться. Для тестовой демонстрации можно получить новый билет перезапуском контейнера:
```bash
docker compose -f docker-compose.yml -f docker-compose.kerberos.yml restart app
```

Разово получить новый TGT из keytab без перезапуска контейнера:
```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.kerberos.yml \
  exec -T app \
  sh -lc 'kinit -kt "$KRB5_KEYTAB" "$KRB5_PRINCIPAL" && klist'
```
`kinit` перезапишет credential cache `/tmp/krb5cc_app` новым TGT. После этого вызовите приложение, чтобы оно получило новый билет SQL Server.

Перезапуск Gunicorn не требуется: FreeTDS использует обновлённый cache-файл при следующем подключении к SQL Server. Сразу после `kinit` билет `MSSQLSvc/...` может исчезнуть — это нормально, он будет запрошен заново при обращении к `/api/health`.

Проверка Kerberos tickets:
```bash
docker compose -f docker-compose.yml -f docker-compose.kerberos.yml exec -T app klist
```

Для длительной эксплуатации нужен отдельный механизм **renewal/re-kinit** и мониторинг срока билета. Оптимальный вариант для этого использовать `k5start`. Он получает TGT из keytab, следит за сроком действия и заранее получает новый билет. Это надёжнее, чем бесконечный цикл с `sleep` и `kinit -R`.

`kinit -R` ограничен полем `renew until`, а `kinit -kt`/`k5start` может получить совершенно новый TGT, пока keytab действителен и доступен KDC.

### 14. Unit-тесты — опционально
Unit-тесты не требуют отдельно запущенного приложения и реального SQL Server: Flask `test_client` вызывает API в памяти, а SQL-соединения подменяются mock-объектами.

Запуск через уже собранный образ:
```bash
cd dockerapp-kerberos-auth-mssql

docker run --rm \
  --entrypoint /opt/venv/bin/python \
  -v "$PWD:/work" \
  -w /work \
  dockerapp-kerberos-auth-mssql-app \
  -m unittest -v test_app.py
```

Альтернатива через Compose:
```bash
docker compose run --rm --no-deps \
  --entrypoint /opt/venv/bin/python \
  -v "$PWD:/work" \
  -w /work \
  app -m unittest -v test_app.py
```

В актуальной версии ожидается 10 успешных тестов:
```text
test_create_item (test_app.ApiTests.test_create_item) ... ok
test_create_rejects_empty_text (test_app.ApiTests.test_create_rejects_empty_text) ... ok
test_delete_item (test_app.ApiTests.test_delete_item) ... ok
test_health_aliases (test_app.ApiTests.test_health_aliases) ... ok
test_kerberos_health_casts_auth_scheme_for_freetds (test_app.ApiTests.test_kerberos_health_casts_auth_scheme_for_freetds) ... ok
test_list_items (test_app.ApiTests.test_list_items) ... ok
test_update_missing_item (test_app.ApiTests.test_update_missing_item) ... ok
test_kerberos_mode_has_no_credentials (test_app.ConnectionStringTests.test_kerberos_mode_has_no_credentials) ... ok
test_odbc_value_escapes_closing_brace (test_app.ConnectionStringTests.test_odbc_value_escapes_closing_brace) ... ok
test_sql_mode (test_app.ConnectionStringTests.test_sql_mode) ... ok

----------------------------------------------------------------------
Ran 10 tests in 0.026s

OK
```

Unit-тесты проверяют код и REST API изолированно. Они не доказывают доступность сети, SQL permissions, SPN или Kerberos. Для этого обязательны runtime-проверки `/api/health`, `klist` и живой CRUD.

### 15. Критерии успешной демонстрации
- контейнер запущен через Gunicorn;
- SQL-режим возвращает `mode=sql`, `auth_scheme=SQL`;
- Kerberos-режим не содержит SQL username/password в окружении контейнера;
- `klist` показывает TGT и `MSSQLSvc/lime.matrix.com:1433@MATRIX.COM`;
- `/api/health` возвращает `mode=kerberos`, `auth_scheme=KERBEROS`;
- Create, Read, Update и Delete работают в обоих режимах;
- после теста временная запись удалена;
- unit-тесты проходят, но рассматриваются отдельно от интеграционной проверки.

### 16. Официальные материалы
- [Microsoft: ktpass](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/ktpass)
- [Microsoft: регистрация SQL Server SPN](https://learn.microsoft.com/en-us/sql/database-engine/configure-windows/register-a-service-principal-name-for-kerberos-connections)
- [Microsoft: CREATE LOGIN](https://learn.microsoft.com/en-us/sql/relational-databases/security/authentication-access/create-a-login)
- [Microsoft: CREATE TABLE permissions](https://learn.microsoft.com/en-us/sql/t-sql/statements/create-table-transact-sql)
- [Microsoft: CONNECTIONPROPERTY](https://learn.microsoft.com/en-us/sql/t-sql/functions/connectionproperty-transact-sql)
