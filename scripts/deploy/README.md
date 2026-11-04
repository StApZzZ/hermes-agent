# Окружение Hermes одной командой

`hermes-deploy` собирает рабочее окружение этого форка на чистой машине или на удалённом Linux-сервере
и сразу открывает его в VS Code. Повторный запуск безопасен: им же доустанавливают недостающее
и подключают новый ноутбук к уже готовому серверу.

| Файл | Где запускается | Что делает |
|---|---|---|
| `hermes-deploy.sh` | Linux, macOS, WSL | Клиент: SSH, загрузка `bootstrap.sh` на цель, VS Code |
| `hermes-deploy.ps1` | Windows (PowerShell 5.1/7, встроенный OpenSSH) | То же для Windows; `-Local` ставит окружение в WSL |
| `bootstrap.sh` | На цели (Linux, macOS) | Стадии `system` (пакеты, пользователь), `user` (Hermes, CLI, состояние, настройки VS Code), `restore`, `check` |
| `vscode_settings.py` | Там, где правятся настройки | Слияние JSONC `settings.json` без потери чужих ключей |
| `ide/vscode/local.txt` | VS Code у вас | Расширения клиента (Remote-SSH) |
| `ide/vscode/remote.txt` | VS Code там, где Hermes | Расширения рабочего места: Claude Code, Codex, ACP-клиент для Hermes, YAML, Python, Ruff |
| `ide/machine-settings.json` | VS Code Server на цели | Исключения watcher/поиска (`.hermes/`, `node_modules`) |
| `check_ide_lists.py` | Где угодно | Проверяет, что каждый ID расширения есть в Marketplace |

## Что получится

То же, что на рабочем сервере `hermes-dev`:

- Hermes из `StApZzZ/hermes-agent` (`--ref`, по умолчанию `main`) в `~/repos/hermes-agent` штатным
  `scripts/install.sh` (`--channel main`, без браузерных инструментов; `--with-browser` их добавит);
  команды `hermes`, `hermes-acp` в `~/.local/bin`, PATH прописан и для неинтерактивных shell (ssh, VS Code, ACP).
- CLI подписок: `claude` (нативная установка), `codex` и `gemini` (npm с Node.js из `hermes pm`),
  `agy` (Antigravity) с обёрткой `agy-hermes`, которая не передаёт ему ключи и токены из `~/.hermes/.env`.
  Список — `--clis`; CLI, который не поставился, не останавливает остальное.
- Провайдер модели `claude-cli` для новой конфигурации (`--provider`, `none` — не трогать).
- Пользователь `hermes` с linger (gateway и tmux-сессии `hermes remote` переживают выход из SSH) — при `--root-login`
  или `--admin`.
- По желанию — служба gateway (`--gateway`) и состояние со старой машины (`--restore`, `--clone-from`).
- По желанию — закрытый сервер (`--lock`): входящий только SSH по ключу с разрешённых адресов.
- VS Code: Remote-SSH у вас, расширения рабочего места на сервере (`remote.SSH.defaultExtensions`),
  ACP-агент `Hermes Remote` в Machine settings сервера, папка открывается сама.

Входы в подписки скрипт не переносит и не запрашивает: каждый CLI входит своим штатным способом,
токены в чат и в командную строку не попадают.

## Быстрый старт

Новый VPS, вход `root` по ключу. Хостер выдал только пароль — сначала положите ключ
(`ssh-copy-id root@203.0.113.10`; Windows — [docs/HERMES-RU.md](../../docs/HERMES-RU.md), «Шаг 1»):

```bash
scripts/deploy/hermes-deploy.sh --host 203.0.113.10 --name hermes-dev --root-login
```

Скрипт поставит пакеты, создаст пользователя `hermes`, разрешит ему ваш ключ, установит всё под ним,
настроит VS Code и откроет `~/repos/hermes-agent` на сервере. Без `--root-login` нужен пользователь с ключом,
а для недостающих пакетов — `sudo` (пароль спросят).

Облако не пускает root по SSH (Oracle Cloud: «Please login as the user opc»; AWS, образы Ubuntu) — тот же
путь через пользователя образа и его `sudo`:

```bash
scripts/deploy/hermes-deploy.sh --host 203.0.113.10 --name hermes-dev --admin opc --lock   # Oracle Linux
scripts/deploy/hermes-deploy.sh --host 203.0.113.10 --name hermes-dev --admin ubuntu       # Ubuntu
```

Системы: Debian/Ubuntu (apt) и RHEL-семейство (dnf: Oracle Linux 8/9, Rocky, Alma, CentOS Stream, Fedora).

Сервер уже есть в `~/.ssh/config`:

```bash
scripts/deploy/hermes-deploy.sh --name hermes-dev
```

Windows (PowerShell), без клона репозитория — остальные файлы скрипт скачает сам:

```powershell
irm https://raw.githubusercontent.com/StApZzZ/hermes-agent/main/scripts/deploy/hermes-deploy.ps1 -OutFile hermes-deploy.ps1
powershell -ExecutionPolicy Bypass -File .\hermes-deploy.ps1 -HostName 203.0.113.10 -Name hermes-dev -RootLogin
powershell -ExecutionPolicy Bypass -File .\hermes-deploy.ps1 -Local          # всё в WSL этого ПК
```

Linux, macOS, WSL одним файлом:

```bash
curl -fsSL https://raw.githubusercontent.com/StApZzZ/hermes-agent/main/scripts/deploy/hermes-deploy.sh \
  | bash -s -- --host 203.0.113.10 --name hermes-dev --root-login
```

Локально (Linux, macOS, WSL): без `--host` и `--name`.

```bash
scripts/deploy/hermes-deploy.sh
```

После развёртывания — один вход в каждый CLI (браузер откроется у вас):

```bash
ssh -t hermes-dev claude auth login
ssh -t hermes-dev codex login --device-auth
ssh -t hermes-dev agy
```

`gemini` с личным аккаунтом Google больше не входит: Google закрыл Gemini CLI для личных подписок
18.06.2026, подписка теперь — через Antigravity (`agy`). Gemini по API-ключу — штатный провайдер Hermes.

## WSL и VS Code на Windows

VS Code на Windows ходит по SSH через Windows OpenSSH: со своим `%USERPROFILE%\.ssh\config` и своим ключом.
Запуск из WSL это учитывает: после развёртывания он разрешает на сервере и Windows-ключ
(`%USERPROFILE%\.ssh\id_ed25519.pub`), затем запускает `hermes-deploy.ps1 -IdeOnly`. Тот пишет блок Host
в Windows ssh config, ставит Remote-SSH и открывает папку. Отдельно ту же часть можно запустить и руками:

```powershell
powershell -ExecutionPolicy Bypass -File .\hermes-deploy.ps1 -IdeOnly -Name hermes-dev -HostName 203.0.113.10
```

Windows PowerShell не запускает `.ps1` с путей `\\wsl$` («AuthorizationManager check failed»),
поэтому WSL-половина копирует скрипт во временную папку Windows.

## Закрытие сервера

```bash
scripts/deploy/hermes-deploy.sh --host 203.0.113.10 --name hermes-dev --root-login --lock
scripts/deploy/hermes-deploy.sh --name hermes-dev --root-login --allow 198.51.100.7,2001:db8::/32   # --allow включает --lock
```

```powershell
powershell -ExecutionPolicy Bypass -File .\hermes-deploy.ps1 -HostName 203.0.113.10 -Name hermes-dev -RootLogin -Lock -Allow 198.51.100.7
```

После всех стадий скрипт загружает на сервер `scripts/remote-dev/harden-server.sh` и применяет его от
root (`--root-login`) или через `sudo` (`--admin`, либо `--user` с правом sudo). Что именно он закрывает — в
[REMOTE-DEVELOPMENT-RU.md](../../docs/REMOTE-DEVELOPMENT-RU.md), раздел 8. Здесь важно следующее:

- **Текущий адрес** скрипт берёт у самого сервера (`SSH_CLIENT`): это адрес, с которого вы реально
  подключены, с учётом NAT и VPN. Внешние сервисы «какой у меня IP» не используются. При наличии
  терминала скрипт спрашивает, разрешить ли его; без терминала (`curl … | bash`, CI) разрешает молча.
- **Порт SSH** берётся из `sshd -T`, поэтому `--port 2222` закрывается правильно. Адреса — IPv4 и IPv6,
  с маской или без.
- **Проверка входа:** после `apply` скрипт открывает новое соединение (`ControlMaster=no`) и только
  из него подтверждает изменения. Не вошло — выход с кодом 6, сервер откатывается сам через 5 минут.
- **Повторный запуск** с другим `--allow` заменяет список целиком; исходные настройки sshd и nftables
  сохранены при первом закрытии и возвращаются `hermes-hardening rollback` от root (скрипт в конце
  печатает готовую команду: `ssh -t -o User=root <alias> /usr/local/sbin/hermes-hardening rollback`).
  Повторный запуск закрытого сервера — с тем же `--root-login`/`--admin`: у пользователя `hermes` нет `sudo`.
- Debian/Ubuntu и RHEL-семейство с systemd; на других системах `--lock` останавливается до установки.
  firewalld (RHEL, Oracle Linux) и ufw не трогаются: пакет проходит все таблицы, наша только сужает.
  На Oracle Linux 8 sshd не читает `sshd_config.d` — скрипт добавит `Include` в начало `sshd_config`.
- `sshd` пускает только `--user`, `--admin` и root: другие учётные записи на сервере теряют вход по SSH.
- Облачный файрвол (Oracle Cloud: Security List / NSG у VCN) остаётся как есть: порт SSH в нём должен
  быть открыт, закрывает сам сервер.

## Перенос состояния

```bash
scripts/deploy/hermes-deploy.sh --name new-box --restore ~/.local/share/hermes-server-backups/hermes-dev/20261101-100001.tar.gz
scripts/deploy/hermes-deploy.sh --name new-box --clone-from hermes-dev     # снимок живого сервера прямо сейчас
scripts/deploy/bootstrap.sh restore ARCHIVE                                # на уже готовой машине
```

Архив — тот же, что делает `scripts/remote-dev/backup-pull.sh`. Из него берутся только `~/.hermes`,
`~/.claude`, `~/.claude.json`, `~/.codex`, `~/.gemini`, `~/.gitconfig`, `~/.config/git`: конфиг и `.env`,
SOUL, память, skills, cron, kanban, сессии, настройки CLI. `repos/` и `work/` не распаковываются —
это клоны старой машины. OAuth-токенов CLI в архиве нет, после переноса нужен вход в каждый CLI.
Перед распаковкой gateway останавливается, прежние `config.yaml`, `.env`, `SOUL.md` сохраняются в
`~/.hermes/backups/pre-restore-<время>/`. Если в `config.yaml` остались пути чужого домашнего каталога,
скрипт их покажет.

Два gateway с одним токеном бота мешают друг другу: при `--clone-from` с `--gateway` остановите
один из них (`hermes gateway stop`).

## Повторный запуск и обновление

- Существующий checkout, установленный этим скриптом, не трогается; `--update` перезапускает штатный
  установщик и обновляет CLI. Обычное обновление Hermes — `hermes update`.
- Checkout с незакоммиченными или непушнутыми изменениями без `--update` не трогается никогда:
  установщик убрал бы их в stash и мог бы сбросить ветку. Скрипт покажет команду для ручного запуска.
- Блок `Host` в ssh config ограничен маркерами `# >>> hermes-deploy <alias>` и заменяется целиком.
  Исходные ssh config и `settings.json` VS Code при первом запуске копируются в `~/.cache/hermes-deploy`
  (Windows: `%LOCALAPPDATA%\hermes-deploy`).
- `--dry-run` проверяет вход и печатает план, ничего не записывая.

## Флаги

| `hermes-deploy.sh` | `hermes-deploy.ps1` | Значение |
|---|---|---|
| `--host ADDR` | `-HostName` | Адрес сервера; пишет блок Host |
| `--name ALIAS` | `-Name` | Имя хоста в ssh config (по умолчанию `hermes-<адрес>`) |
| `--user NAME` | `-User` | Пользователь Hermes на сервере (`hermes`) |
| `--port N`, `--key FILE` | `-Port`, `-Key` | SSH; ключ по умолчанию — первый из `id_ed25519`, `id_ecdsa`, `id_rsa` |
| `--ssh-config FILE` | — | Другой ssh config; VS Code получит `remote.SSH.configFile` |
| `--root-login` | `-RootLogin` | Сначала войти root: пакеты, пользователь, ключ, linger |
| `--admin NAME` | `-Admin NAME` | То же через sudo-пользователя, где root не пускают (`opc` на Oracle Cloud) |
| `--skip-system` | `-SkipSystem` | Пропустить системную стадию |
| — | `-Local [-Distro D]` | Развернуть в WSL этого ПК |
| `--repo`, `--ref` | `-Repo`, `-Ref` | Источник Hermes (форк, `main`) |
| `--dir`, `--workspace` | `-Dir`, `-Workspace` | Checkout (`~/repos/hermes-agent`) и папка для VS Code (= checkout) |
| `--clis LIST` | `-Clis` | `claude,codex,gemini,agy` |
| `--provider NAME` | `-Provider` | Провайдер новой конфигурации (`claude-cli`; `none`) |
| `--update`, `--with-browser`, `--gateway` | `-Update`, `-WithBrowser`, `-Gateway` | См. выше |
| `--restore FILE`, `--clone-from ALIAS` | `-Restore`, `-CloneFrom` | Перенос состояния |
| `--lock`, `--allow ADDR[,ADDR]` | `-Lock`, `-Allow ADDR,ADDR` | Закрыть сервер: SSH по ключу только с этих адресов и с текущего |
| `--ide code\|none`, `--no-open` | `-Ide`, `-NoOpen` | VS Code; `-IdeOnly` — только Windows-часть |
| `--dry-run` | `-DryRun` | Только проверки и план |

## Другие IDE

Списки лежат по IDE: `ide/<ide>/local.txt` (клиент) и `ide/<ide>/remote.txt` (рабочее место).
Сейчас поддержан VS Code. Cursor (свой Remote-SSH `anysphere.remote-ssh`, расширения из Open VSX,
`defaultExtensions` не действует) и JetBrains Gateway подключаются добавлением `ide/cursor/`,
`ide/jetbrains/` и ветки в `--ide`.

## Проверки

```bash
python3 scripts/deploy/check_ide_lists.py                    # ID расширений есть в Marketplace
scripts/run_tests.sh tests/scripts/test_deploy_environment.py
ssh hermes-dev bash ~/.cache/hermes-deploy/bootstrap.sh check  # версии и состояние на сервере
```

Закрыть уже развёрнутый сервер — тот же скрипт с `--lock` (или `harden-server.sh` вручную,
[docs/REMOTE-DEVELOPMENT-RU.md](../../docs/REMOTE-DEVELOPMENT-RU.md), раздел 8).
