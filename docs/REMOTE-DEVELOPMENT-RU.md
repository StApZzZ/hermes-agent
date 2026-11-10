# Hermes Remote Development: Windows + Linux

VS Code UI остаётся на Windows. Проект, Git, extension host, языковые серверы,
Hermes, Claude Code, зависимости, сборки и кэши работают на Linux через Remote-SSH.
Копия проекта на Windows, синхронизация файлов и WSL не нужны.

`hermes remote` — launcher на Python 3.11+ со стандартной библиотекой. Он не
импортирует локальный агент Hermes, PM, Node или Claude Code. Во время SSH
остаётся небольшой процесс launcher: полного отсутствия Python на Windows это
не означает. Сгенерированный `config --proxy` запускает ACP через `ssh` напрямую,
вообще без локального Python.

Всё описанное ниже, от пакетов на сервере до открытой папки в VS Code, делает одна команда —
[`scripts/deploy/hermes-deploy.sh`](../scripts/deploy/README.md) (Windows: `hermes-deploy.ps1`):

```sh
scripts/deploy/hermes-deploy.sh --host <server> --name devbox --root-login
```

Разделы ниже — то, что она делает, по шагам, и ручной путь.

## 1. Подготовка Linux

Используйте непривилегированную учётную запись, например `dev`.
На Debian/Ubuntu администратор устанавливает системные инструменты:

```sh
sudo apt-get update
sudo apt-get install -y openssh-server git tmux curl ca-certificates
```

Установите Hermes под `dev` по [официальной инструкции](https://hermes-agent.nousresearch.com/docs/getting-started/installation),
Claude Code — по [инструкции Anthropic](https://code.claude.com/docs/en/setup):

```sh
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
curl -fsSL https://claude.ai/install.sh | bash
export PATH="$HOME/.local/bin:$PATH"
hermes model
hermes acp --check
claude auth login
mkdir -p ~/projects
# Клонируйте свой проект только на сервер, например /home/dev/projects/myproject.
```

Installer Hermes подготавливает managed runtime: системный `python3` не доказывает
совместимость Hermes. Native Claude Code не требует системного Node; при npm
установке Node нужен на сервере. Hermes и Claude авторизуются независимо;
launcher не переносит токены. Подходящий backend Hermes на сервере обычно
`terminal.backend: local` — здесь local означает Linux-сервер.

## 2. Windows: OpenSSH и VS Code

Установите VS Code и OpenSSH Client Windows. В PowerShell:

```powershell
ssh -V
code --install-extension ms-vscode-remote.remote-ssh
```

Добавьте свои значения в `%USERPROFILE%\.ssh\config`:

```sshconfig
Host devbox
    HostName your-server.example
    User dev
    Port 22
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
```

Передайте серверу публичный ключ штатным способом. Приватный ключ остаётся
в Windows/ssh-agent. Выполните `ssh devbox` вручную, сверяя fingerprint с доверенным
источником. Doctor/ACP используют BatchMode и StrictHostKeyChecking=yes:
неизвестный/изменённый ключ и парольный запрос дают ошибку вместо зависания.

## 3. Launcher без установки полного Hermes

Hermes, установленный из этой ветки, предоставляет `hermes remote`. Обычный вызов
без глобальных profile/container-параметров обрабатывается до bootstrap, PM,
плагинов и импортов агента. Если полного Hermes на Windows нет, достаточно Python
3.11+ и трёх файлов (без pip-зависимостей):

```powershell
$target = "$env:LOCALAPPDATA\HermesRemote\hermes_cli\subcommands"
New-Item -ItemType Directory -Force -Path $target | Out-Null
$base = 'https://raw.githubusercontent.com/StApZzZ/hermes-agent/main/hermes_cli/subcommands'
foreach ($file in @('remote.py', 'remote_doctor.py', 'remote_workspaces.py')) {
    Invoke-WebRequest "$base/$file" -OutFile "$target\$file"
}
py -3 "$target\remote.py" --help
```

Для закреплённой версии замените имя ветки в `$base` проверенным commit SHA.
В текущую сессию PowerShell или свой profile добавьте:

```powershell
function hermes {
    if ($args.Count -eq 0 -or $args[0] -ne 'remote') {
        throw 'Этот launcher поддерживает только hermes remote'
    }
    & py -3 "$env:LOCALAPPDATA\HermesRemote\hermes_cli\subcommands\remote.py" @($args | Select-Object -Skip 1)
}
```

В исходном checkout также работает
`python hermes_cli/subcommands/remote.py open devbox --workspace /home/dev/projects/myproject`.
Скрипт можно запускать из любой текущей папки.

## 4. Проект и устойчивые терминальные сессии

```powershell
hermes remote doctor devbox --workspace /home/dev/projects/myproject
hermes remote open devbox --workspace /home/dev/projects/myproject --new-window
hermes remote claude devbox --workspace /home/dev/projects/myproject
hermes remote codex devbox --workspace /home/dev/projects/myproject
hermes remote gemini devbox --workspace /home/dev/projects/myproject
```

VS Code открывает папку через `--remote ssh-remote+devbox`. На Windows launcher
обходит исполнение `code.cmd`, вызывая `Code.exe` и штатный CLI JS напрямую
(включая version-directory layout новых установок):
путь с `&`, `%`, кавычками и пробелами не интерпретирует `cmd.exe`.

Claude запускается в tmux на отдельном socket `hermes-remote`. Имя сессии
`LABEL-HASH` зависит от SSH-алиаса, пути и агента. Разные проекты не попадают
в одну сессию даже при одинаковом `--session`. Для нескольких бесед в одном
проекте используйте разные `--session feature1` / `feature2`. В label допустимы
буквы, цифры, `_`, `-`; точки и двоеточия tmux запрещены.

Отсоединиться: Ctrl+B, затем D. Та же команда подключает к прежнему процессу.
Ctrl+C адресован удалённому foreground job, resize передаёт OpenSSH/PTTY.
Штатный выход из агента завершает процесс. `--resume` открывает выбор истории
при создании новой сессии, но не перезапускает уже работающий агент. История Claude
остаётся на Linux. Ручной просмотр: `ssh devbox 'tmux -L hermes-remote list-sessions'`.

`--no-tmux` отключает устойчивость. tmux не переживает reboot/OOM/kill. Если сервер
уничтожает user-процессы при logout, lifecycle нужно согласовать с администратором;
launcher эту политику не меняет.

## 5. Сохранённые workspaces

Создайте `%APPDATA%\hermes-remote\workspaces.json` (без APPDATA используется
`~/.config/hermes-remote/workspaces.json`). Только закладки, без секретов:

```json
{
  "myproject": {
    "host": "devbox",
    "workspace": "/home/dev/projects/myproject",
    "agent": "claude",
    "session": "work",
    "tmux": true
  }
}
```

```powershell
hermes remote list
hermes remote open myproject
hermes remote doctor myproject
hermes remote start myproject
hermes remote claude myproject --session feature2
```

`start` использует предпочтительный `agent`: `hermes` (по умолчанию), `claude`,
`codex` или `gemini`. Одноимённые команды всегда запускают выбранный CLI.
`--workspace`/`--session` переопределяют закладку. `--config PATH`
выбирает другой JSON. Неизвестные поля и неверные типы — ошибка. `--ssh-config PATH`
передаёт OpenSSH `-F`; для VS Code отдельно настройте `remote.SSH.configFile`.
Закладки не синхронизируют серверную БД Projects и файлы проекта.

## CLI и подписки

Все три штатных CLI можно запускать на Linux под собственной подпиской.
Установите Codex и Gemini на сервере с Node.js 20+:

```sh
npm install --global @openai/codex @google/gemini-cli
codex login --device-auth
claude auth login --claudeai
NO_BROWSER=true gemini
# В Gemini выберите Sign in with Google и аккаунт своей подписки.
```

Вход выполняется пользователем в браузере; коды и токены не нужно отправлять в чат.
Для Codex device login может понадобиться включить его в настройках аккаунта;
альтернатива — обычный `codex login` через SSH tunnel localhost:1455.
Источники: [Codex auth](https://developers.openai.com/codex/auth/),
[Gemini auth](https://geminicli.com/docs/get-started/authentication/),
[Claude auth](https://code.claude.com/docs/en/authentication).

Команды `remote claude/codex/gemini` запускают соответствующий агент в tmux;
его история и авторизация принадлежат штатному CLI на сервере. `--resume` использует
`claude --resume`, `codex resume`, а для Gemini — `gemini --resume` (последняя сессия).
Gemini получает `NO_BROWSER=true` для штатной ручной авторизации на SSH-хосте.
Отсоединение и повторное подключение к активному tmux вообще не требуют `--resume`.

### Hermes поверх CLI (`codex-cli`, `claude-cli`, `gemini-cli`)

Плагин `plugins/model-providers/cli-subscriptions` регистрирует три провайдера
типа `external_process`. Hermes ведёт сессию, историю, память и **сам выполняет
все инструменты**; CLI только генерирует следующий ответ модели под вашей
подпиской. Через тот же клиент идут и вспомогательные запросы (заголовки,
компрессия, vision-маршрутизация) — HTTP-клиент для этих провайдеров не создаётся.

```sh
hermes config set model.provider claude-cli   # или codex-cli / gemini-cli
hermes config set model.default default       # модель по умолчанию самого CLI
hermes -z "проверка" --provider codex-cli     # разовый запрос через другой CLI
hermes auth status claude-cli                 # штатный статус входа CLI
hermes auth add codex-cli                     # штатный вход: codex login --device-auth
```

Свойства и ограничения:

- Токены не читаются и не копируются: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
  `GEMINI_API_KEY` и т. п. удаляются из окружения CLI, Codex принудительно
  использует вход ChatGPT, Claude — claude.ai. Gemini в режиме API key/Vertex
  отклоняется: переключите `gemini` → `/auth` → Sign in with Google.
- Собственные инструменты CLI выключены (`--tools ""`/`--restricted`, Codex
  read-only без shell, Gemini policy deny-all, без MCP).
- Для Claude стабильная часть (system prompt Hermes + контракт инструментов)
  передаётся через `--system-prompt-file`, поэтому кэшируется: в проверке доля
  cache read 94–96 % против 7 % при передаче всего через stdin.
- Ошибки CLI (например, исчерпанный лимит подписки) показываются текстом самого
  CLI; stderr не выводится, так как там могут быть device-коды.
- Только текст: изображения/аудио отклоняются явно. Контекст по умолчанию
  считается 256k; при необходимости задайте `model.context_length`.
- После обновления кода остановите фоновый gateway (`hermes gateway stop`),
  иначе `hermes -z` продолжит обслуживаться старым процессом.

Чтобы Hermes не заимствовал CLI-токены для **других** провайдеров:

```sh
hermes config set auth.adopt_external_logins false
```

## 6. Hermes ACP

Установите `formulahendry.acp-client` **в SSH: devbox**. В `Developer: Show Running
Extensions` проверьте, что расширение работает на SSH extension host. Его
[manifest/исходники](https://github.com/formulahendry/vscode-acp/tree/e7371659e3ac100db842b419b1361205a193032e)
поддерживают command/args и NDJSON по stdio; файловые и терминальные callbacks
работают там, где extension host. Явного extensionKind в manifest нет.
При необходимости задайте в User settings и переподключитесь:

```json
{
  "remote.extensionKind": {"formulahendry.acp-client": ["workspace"]}
}
```

Это поведение размещения описано в [VS Code Extension Host](https://code.visualstudio.com/api/advanced-topics/extension-host).
Получите настройки и объедините их с Remote settings:

```powershell
hermes remote config devbox --workspace /home/dev/projects/myproject
```

Результат — `hermes acp` напрямую и абсолютный Linux `acp.defaultWorkingDirectory`.
Если launcher Hermes не в PATH удалённого extension host, задайте полный путь
к установленному PM wrapper, а не случайной Python/venv.

Для ACP-клиента, который по необходимости работает локально:

```powershell
hermes remote config devbox --workspace /home/dev/projects/myproject --proxy
```

Этот JSON запускает **ssh напрямую**, без PTY и локального Hermes. Команда
`hermes remote acp ...` предоставляет такой же byte-transparent bridge: stdin,
stdout, stderr наследуются как OS handles без перекодировки. При остановке launcher
SSH завершается и ожидается; EOF/exit code передаются. tmux для ACP не используется.

Bridge **не перенаправляет файловые/терминальные callbacks локального клиента**.
Одна подмена command на ssh не делает Windows filesystem удалённой. Полный рабочий
процесс требует удалённого extension host либо клиента с собственными удалёнными
callbacks. Не переписывайте Windows cwd в JSON-RPC вслепую.

Возобновление ACP-истории уже реализовано через серверный SessionDB и требует
поддержки клиента. Текущая генерация не обязана продолжаться после обрыва ACP.
Уберите noninteractive `echo`/баннеры из startup-файлов shell сервера: launcher
не фильтрует stdout, чтобы не менять корректные protocol frames. Ошибки идут в stderr.

## 7. Doctor и проверка

Doctor проверяет папку/права записи, Git/tmux/Claude/Hermes, ACP runtime, системные
runtime, RAM/swap, disk/inodes, ulimit и cgroup memory/pids. Не устанавливает ПО
и не меняет настройки; `hermes acp --check` может выполнять штатную инициализацию
Hermes. RAM <1 GiB, disk <2 GiB, отсутствующие нужные инструменты/папка дают exit 1;
нет swap — предупреждение. Ошибка SSH — 255; неверные аргументы — 2;
отсутствующий executable — 127; Ctrl+C launcher — 130.

Probe не проверяет LLM-запрос, подписку или подходящий размер сервера для сборки.
Cgroup предков и quota могут требовать отдельной проверки при свободных host RAM/disk.

Standalone-lane требует pytest 9.0.2 и stdlib. Из checkout с тестовым interpreter:

```sh
export HERMES_PYTHON="$(python -c 'import sys; print(sys.executable)')"
bash scripts/run_tests.sh tests/hermes_cli/test_remote_dev.py -- --confcutdir=tests/hermes_cli -o addopts= -q
bash scripts/run_tests.sh tests/hermes_cli/test_remote_ssh_integration.py -- --confcutdir=tests/hermes_cli -o addopts= -m integration -q
```

Git Bash нужен только каноническому test runner на Windows. `--confcutdir` изолирует
эту lane от общего conftest, которому нужна полная среда Hermes. Полный suite/check
выполняются отдельно в PM dev/test-среде по CONTRIBUTING.md.

Интеграционные тесты создают временный Docker SSH-сервер, эфемерный ключ/known_hosts
и loopback-порт; удаляют только собственные контейнер/образ. Реальные OpenSSH и tmux
проверяют quoting, ACP initialize/session/new, большой Unicode payload, stderr,
EOF/exit, Ctrl+C/resize, reconnect и несколько проектов. Hermes/Claude — **заглушки**,
LLM не используется. Если Docker недоступен, тесты skipped.

CI `remote-development.yml` проверяет Windows/Linux и SSH-контейнер на Linux.
Реальный VS Code UI, extension-host placement и авторизованный Claude/Hermes —
отдельная приёмка; transport-тест их не доказывает.
Подробности: [аудит](REMOTE-DEVELOPMENT-AUDIT.md).

## 8. Закрытие сервера

`scripts/remote-dev/harden-server.sh` (запуск от root на сервере) оставляет
единственную входящую дверь — SSH по ключу с адресов из allowlist (выходные IP
вашего VPN). Проще всего — `hermes-deploy --lock [--allow ...]`: он сам применит
скрипт, добавит адрес, с которого вы подключены, и подтвердит из нового соединения
([scripts/deploy/README.md](../scripts/deploy/README.md), «Закрытие сервера»).
Debian/Ubuntu и RHEL-семейство (Oracle Linux, Rocky, Alma) с systemd. firewalld и ufw
остаются как есть: наша таблица только сужает то, что они пропускают.

- nftables, отдельная таблица `inet hermes_fw` (загружается при старте из `/etc/nftables.conf`,
  на RHEL — `/etc/sysconfig/nftables.conf`): input/forward `policy drop`,
  разрешены loopback, ответы на исходящие соединения, порт(ы) sshd (из `sshd -T`)
  и ping только из allowlist (IPv4 и IPv6), IPv6 ND. Исходящий трафик открыт (CLI, git, пакеты).
- sshd: только `publickey`, `AllowUsers <admin...> root` (`--admin-user` можно повторять:
  на Oracle Cloud это `hermes` и `opc`), root без пароля (если у root нет рабочего ключа,
  нужен sudo у одного из `<admin>`), без
  agent/X11/tunnel/remote-forwarding; local forwarding оставлен для VS Code.
- cloud-init `ssh_pwauth: false`, учётка `tmpuser` из образа заблокирована, автоматические
  обновления безопасности (unattended-upgrades; на RHEL — `dnf-automatic`), базовые sysctl.
  Где `sshd_config` не подключает `sshd_config.d` (Oracle Linux 8, Debian 10), в его начало
  добавляется `Include`; откат возвращает исходный файл.

```sh
scp scripts/remote-dev/harden-server.sh root@<server>:/root/
ssh root@<server> /root/harden-server.sh apply --allow <vpn-exit-ip> [--allow <ip2>] --admin-user hermes
# В НОВОЙ SSH-сессии, в течение 5 минут, иначе произойдёт автоматический откат:
ssh root@<server> /usr/local/sbin/hermes-hardening confirm
```

Сменить allowlist — повторить `apply` с новым набором `--allow` и снова `confirm`;
полный откат — `hermes-hardening rollback`. Если VPN недоступен, вход возможен
только с другого адреса из allowlist или через консоль VNC провайдера.
Не ставьте Docker поверх этой таблицы без пересмотра правил: forward закрыт.

### Веб-панель с VPN и приложение в Telegram

`scripts/remote-dev/web-access.sh` (от root на закрытом сервере) открывает Hermes в браузере без проброса портов:

- `https://<домен>/` — Webapp (рабочее место Desktop: чаты, файлы, настройки), `https://<домен>/panel/` — веб-панель
  (`hermes dashboard`: изображения, конфиг, логи, cron);
- вход по паролю — **только с адресов VPN** (`dashboard.auth_networks.basic`). С других адресов форма пароля
  не показывается, а уже открытая сессия получает 403 и снова работает, как только вы вернулись в VPN;
- кнопка **Hermes** в меню вашего бота открывает Webapp как Telegram Mini App. Пускает **только ваш аккаунт
  Telegram** (`dashboard.telegram_mini_app.allowed_users`; подпись Telegram проверяется токеном бота),
  с любой сети, без пароля.

```sh
ssh -t root@<server> /home/hermes/repos/hermes-agent/scripts/remote-dev/web-access.sh apply
```

По умолчанию адреса VPN берутся из allowlist SSH (раздел выше), ваш Telegram ID — из `TELEGRAM_ALLOWED_USERS`
(если там один ID), домен — `<IP через дефисы>.sslip.io` (сертификат Let's Encrypt выпускает сам Angie, по TLS-ALPN на 443). Переопределить:
`--vpn 198.51.100.7,203.0.113.0/24`, `--telegram-user 123456789`, `--domain hermes.example.com`. Пароль скрипт
создаёт сам и печатает **один раз** (логин `admin`); новый — `apply --reset-password`.

Что меняется на сервере: ставится Angie из его репозитория (если ещё не стоит) с сайтом
`/etc/angie/http.d/hermes-web-access.conf`, порт 443 открывается для всех (`hermes-hardening public-port add 443`,
SSH остаётся только с allowlist; без 443 Telegram не откроет Mini App, а Let's Encrypt не выдаст сертификат),
в `config.yaml` пишутся `webapp.*`, `dashboard.public_url`, `dashboard.auth_networks`,
`dashboard.telegram_mini_app` и пароль (scrypt-хеш), появляется служба `hermes-dashboard` пользователя `hermes`,
gateway перезапускается. Проверка — `web-access.sh status`, откат — `web-access.sh remove` (сайт удаляется, сам Angie остаётся).

Адрес VPN сменился — поправьте список без перезапуска: `hermes config set dashboard.auth_networks '{"basic": ["<ip>"]}'`
(и `harden-server.sh apply` для SSH). Telegram Web (в браузере) Mini App не откроет: только приложения Telegram
на телефоне и компьютере.

## 9. Резервные копии

`scripts/remote-dev/backup-pull.sh [--force] [ssh-alias]` запускается на локальной
машине (WSL) и кладёт архив в `~/.local/share/hermes-server-backups/<alias>/`
(или `$XDG_DATA_HOME/hermes-server-backups/<alias>/`), права 700. Каталог
намеренно вне checkout: `scripts/check` (проверка `profile-archives`) отвергает
архивы профиля в любом месте дерева, даже в `.gitignore`, — иначе их легко
унести в коммит через `git add -f` или в Docker-образ. Бэкапы, сделанные до
переноса в `<repo>/server-backups/`, переместите: `mv <repo>/server-backups/*
~/.local/share/hermes-server-backups/`. В архив входят `~/.hermes` (config, `.env`,
SOUL, память, skills, cron, kanban, сессии), конфиги и история Claude/Codex/Gemini,
`~/repos` и `~/work` с `.git`, а также `BACKUP-MANIFEST.txt` (ветка, HEAD, число
незакоммиченных и непушнутых изменений по каждому репозиторию) и копии
`/etc/nftables.conf` и sshd drop-ins. SQLite копируется через backup API, поэтому
снимок консистентен при работающем gateway.

Не входят: пересобираемые `~/.hermes/tools|cache|installs`, пакеты CLI,
`node_modules`/venv и OAuth-токены CLI — после восстановления выполните штатный
вход в каждый CLI. Один архив в сутки (`--force` — внеплановый), хранятся 14
последних (`KEEP=N`). Журнал — `backup.log` в том же каталоге.

Расписание в Windows (при входе в систему и ежедневно; пропущенный запуск
выполняется при первой возможности, нужен поднятый VPN):

```powershell
# conhost --headless: без консольного окна на время выгрузки.
$a = New-ScheduledTaskAction -Execute conhost.exe -Argument '--headless wsl.exe -d Ubuntu -- bash -lc "~/repos/hermes-agent/scripts/remote-dev/backup-pull.sh"'
$t = @((New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"), (New-ScheduledTaskTrigger -Daily -At 13:00))
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'Hermes server backup' -Action $a -Trigger $t -Settings $s
```

Восстановление на чистом сервере под пользователем Hermes:

```sh
sha256sum -c <stamp>.tar.gz.sha256
tar -xzf <stamp>.tar.gz -C ~        # затем codex/claude/gemini login
```

## 10. Синхронизация с локальным Hermes (`hermes sync`)

Hermes в WSL и Hermes на сервере держат одни и те же настройки, навыки и память. Запускается
на локальной машине (WSL), сервер — алиас из `~/.ssh/config`:

```sh
hermes sync run hermes-dev --dry-run   # что изменится
hermes sync run hermes-dev             # синхронизировать сейчас
hermes sync enable hermes-dev          # и дальше каждые 10 минут (--every N), systemd --user таймер
hermes sync status                     # хосты, последний синк, таймер
hermes sync disable hermes-dev
```

Что переносится — для основного агента и профилей из списка `sync.profiles`: `config.yaml`, `SOUL.md`, `profile.yaml`,
`memories/MEMORY.md` и `USER.md`, `skills/` (свои и изменённые встроенные; нетронутые встроенные
каждая машина берёт из своей версии Hermes), а также `spaces.yaml`, `spaces/SHARED.md` и заметки
пространств. Не переносятся: `.env`, `auth.json`, сессии, базы, логи, кеши и входы в CLI.

Именованные профили по умолчанию остаются на своей машине: рабочие команды агентов или бот на
сервере не попадут на другую сторону, пока вы их не перечислите. Список у каждой машины свой,
задаётся там, где запускается `hermes sync`:

```yaml
sync:
  profiles: [personal]   # плюс основной профиль, он синхронизируется всегда
```

Убрать профиль из списка безопасно: он просто перестаёт синхронизироваться и ни на одной из сторон
не удаляется.

В `config.yaml` у каждой машины остаются свои настройки машины: `terminal`, `gateway`, мессенджеры
(`telegram`, `discord`, …, `platforms`), пути (`browser.executable_path`, `skills.external_dirs`, …),
`mcp_servers`, `kanban`, `sync`. Дополнительные ключи — `sync.local_keys` (через точку, `*` — один
уровень). Привязанные к пространствам папки (`spaces.<имя>.paths`) тоже остаются у своей машины.

Синхронизация двусторонняя и трёхсторонняя: состояние после прошлого синка лежит в
`~/.hermes/sync/<хост>/state.json`. Изменилось с одной стороны — переносится; удалено с одной
стороны и не менялось с другой — удаляется; изменение побеждает удаление. Изменено с обеих
сторон: ключи `config.yaml` и записи памяти сливаются по отдельности (при споре одного ключа
выигрывает файл, изменённый позже), остальные файлы — побеждает более новая копия, другая
сохраняется в `~/.hermes/sync/<хост>/conflicts/<время>/`. Файл, изменившийся во время синка, не
перезаписывается — его подхватит следующий запуск. Агенты видят перенесённую память со
следующего разговора (кеш промпта текущего не трогается).

На сервере нужна та же версия Hermes (`hermes sync agent` вызывается по SSH) и вход по ключу без
пароля; без VPN из allowlist таймер просто пропускает запуск.
