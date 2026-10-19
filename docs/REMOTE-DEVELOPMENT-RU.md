# Hermes Remote Development: Windows + Linux

VS Code UI остаётся на Windows. Проект, Git, extension host, языковые серверы,
Hermes, Claude Code, зависимости, сборки и кэши работают на Linux через Remote-SSH.
Копия проекта на Windows, синхронизация файлов и WSL не нужны.

`hermes remote` — launcher на Python 3.11+ со стандартной библиотекой. Он не
импортирует локальный агент Hermes, PM, Node или Claude Code. Во время SSH
остаётся небольшой процесс launcher: полного отсутствия Python на Windows это
не означает. Сгенерированный `config --proxy` запускает ACP через `ssh` напрямую,
вообще без локального Python.

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
$base = 'https://raw.githubusercontent.com/StApZzZ/hermes-agent/feat/remote-development/hermes_cli/subcommands'
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

`start` использует предпочтительный `agent` (`hermes` по умолчанию); `claude` всегда
запускает Claude. `--workspace`/`--session` переопределяют закладку. `--config PATH`
выбирает другой JSON. Неизвестные поля и неверные типы — ошибка. `--ssh-config PATH`
передаёт OpenSSH `-F`; для VS Code отдельно настройте `remote.SSH.configFile`.
Закладки не синхронизируют серверную БД Projects и файлы проекта.

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
