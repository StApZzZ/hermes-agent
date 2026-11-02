# Hermes для чайников

Простое руководство по нашей сборке Hermes: что это, что ставится, как запустить и как пользоваться
каждый день. Основной способ работы — **VS Code на вашем ПК, подключённый к серверу**. Подробности по
серверу — в [REMOTE-DEVELOPMENT-RU.md](REMOTE-DEVELOPMENT-RU.md), по установке одной командой — в
[scripts/deploy/README.md](../scripts/deploy/README.md).

## 1. Что это такое

**Hermes** — личный AI-агент. С ним можно говорить в VS Code, в терминале, в Telegram и других
мессенджерах. Он помнит факты о вас между разговорами, умеет работать с файлами, терминалом и
браузером, выполняет задачи по расписанию.

**Наш форк** (`StApZzZ/hermes-agent`) — это обычный Hermes от Nous Research плюс четыре вещи:

| Что | Зачем |
|---|---|
| Приватность | Ничего не уходит к разработчику Hermes, пока вы сами это не включите и не укажете адрес |
| Подписки вместо API-ключей | Hermes думает через ваши подписки Claude и ChatGPT (Codex), без оплаты API |
| Удалённый сервер | Hermes и проекты живут на Linux-сервере, вы работаете из VS Code на своём ПК |
| Пространства | Отдельные агенты и память для работы, личного и проектов, с переключением одной командой |

Раз в какое-то время мы забираем все исправления из оригинального Hermes, сохраняя эти отличия.

### Как это устроено

```
 Ваш ПК (Windows, macOS, Linux)              Сервер hermes-dev (Linux)
┌──────────────────────────────┐          ┌──────────────────────────────────────┐
│ VS Code + Remote-SSH         │ ── SSH ─▶│ Hermes, его память и пространства    │
│ (окно, клавиатура, браузер)  │          │ claude, codex, agy — подписки        │
└──────────────────────────────┘          │ ваши проекты, терминал, бот Telegram │
                                          └──────────────────────────────────────┘
```

VS Code только показывает окно. Всё остальное работает на сервере, поэтому:

- на ПК ничего, кроме VS Code, ставить не нужно — ни Hermes, ни Python, ни отдельное приложение;
- можно закрыть ноутбук: бот в Telegram и задачи по расписанию продолжат работать;
- с другого ПК вы попадёте в то же окружение с той же памятью.

## 2. Что ставится

### На ваш ПК

- **VS Code** с расширением **Remote-SSH** — открывает папки на сервере как у себя.
- Запись `hermes-dev` в вашем `~/.ssh/config` и SSH-ключ.

### На сервер

| Компонент | Откуда | Чей код |
|---|---|---|
| **Hermes** (`hermes`, `hermes-acp`) в `~/repos/hermes-agent` | Наш GitHub, ветка `main` | **Наш форк** |
| Терминальный интерфейс (`hermes --tui`), веб-панель (`hermes dashboard`) | Собираются из кода Hermes при первом запуске | **Наш форк** |
| Бот для Telegram и других мессенджеров (gateway) | Часть Hermes | **Наш форк** |
| `claude` — Claude Code | claude.ai, официальный установщик | Anthropic, без изменений |
| `codex` — Codex CLI | npm `@openai/codex` | OpenAI, без изменений |
| `gemini` — Gemini CLI | npm `@google/gemini-cli` | Google, без изменений |
| `agy` — Antigravity CLI | antigravity.google, официальный установщик | Google, без изменений |
| Node.js | Через `hermes pm` (менеджер окружения Hermes) | Официальная сборка |
| Расширения VS Code: Claude Code, Codex (ChatGPT), **ACP Client** (чат с Hermes), YAML, Python, Ruff | Marketplace, VS Code ставит их сам при подключении | Официальные |

Наш код — только Hermes. CLI подписок (`claude`, `codex`, `gemini`, `agy`) — официальные программы
вендоров: мы их не меняем, Hermes просто вызывает их.

### Где что лежит на сервере

| Путь | Что там |
|---|---|
| `~/repos/hermes-agent` | Код Hermes (git-checkout форка) |
| `~/repos/<проект>` | Ваши проекты — сюда их удобно клонировать |
| `~/.hermes/config.yaml` | Настройки |
| `~/.hermes/.env` | Секреты: токены ботов, API-ключи. Никому не показывайте |
| `~/.hermes/memories/` | Память агента: `MEMORY.md` (заметки агента) и `USER.md` (что он знает о вас) |
| `~/.hermes/profiles/<имя>/` | Другие агенты (профили), у каждого своя память и настройки |
| `~/.hermes/spaces/` | Пространства и общая память |
| `~/.hermes/logs/` | Логи (`hermes logs`) |

> **Отдельного приложения для Windows у нас нет, и оно не нужно:** всё доступно из VS Code.
> Desktop-приложение с сайта Nous Research — **их** сборка с их кодом и их обновлениями, не наша.

## 3. Установка

Нужен Linux-сервер (VPS) с входом `root` по SSH-ключу и VS Code на вашем ПК. Всё остальное ставит одна
команда, которую вы запускаете **на своём ПК**. В конце она сама откроет VS Code, уже подключённый
к серверу.

**Windows** (PowerShell):

```powershell
irm https://raw.githubusercontent.com/StApZzZ/hermes-agent/main/scripts/deploy/hermes-deploy.ps1 -OutFile hermes-deploy.ps1
powershell -ExecutionPolicy Bypass -File .\hermes-deploy.ps1 -HostName 203.0.113.10 -Name hermes-dev -RootLogin
```

**macOS, Linux, WSL:**

```bash
curl -fsSL https://raw.githubusercontent.com/StApZzZ/hermes-agent/main/scripts/deploy/hermes-deploy.sh \
  | bash -s -- --host 203.0.113.10 --name hermes-dev --root-login
```

Вместо `203.0.113.10` — адрес сервера, `hermes-dev` — короткое имя, под которым он будет у вас в SSH
и в VS Code. Скрипт создаст на сервере пользователя `hermes`, поставит всё из таблицы выше, поставит
Remote-SSH в ваш VS Code и откроет папку `~/repos/hermes-agent` на сервере.

Повторный запуск с теми же параметрами безопасен: он доставит недостающее и ничего не сломает.

### Войти в подписки (один раз)

В VS Code откройте терминал: меню **Terminal → New Terminal** (или ``Ctrl+` ``). Он уже на сервере —
внизу слева в VS Code написано `SSH: hermes-dev`. Каждый CLI входит сам, через браузер на вашем ПК;
пароли и коды в Hermes и в чат не вводятся.

```bash
claude auth login           # подписка Claude (Pro/Max)
codex login --device-auth   # подписка ChatGPT (Plus/Pro)
agy                         # Google AI Pro/Ultra через Antigravity
```

> Gemini CLI с личным аккаунтом Google больше не работает (Google закрыл это 18.06.2026).
> Подписку Google используйте через `agy`, а Gemini по API-ключу — как обычный провайдер Hermes.

### Проверить, что всё работает

Там же, в терминале VS Code:

```bash
hermes doctor              # проверка настроек и зависимостей
hermes auth status claude-cli
hermes -z "Привет! Ответь одним словом."
```

## 4. Работа в VS Code

### Подключиться к серверу

- **Обычно:** VS Code сам предложит последнюю папку — **File → Open Recent**.
- **Вручную:** `F1` → **Remote-SSH: Connect to Host** → `hermes-dev`, затем **File → Open Folder**.
- Подключение видно внизу слева: `SSH: hermes-dev`. Всё, что открыто в этом окне, — на сервере.

### Открыть проект

В терминале VS Code склонируйте проект в `~/repos` и откройте его:

```bash
git clone https://github.com/you/myproject ~/repos/myproject
code -r ~/repos/myproject     # откроет папку в этом же окне VS Code
```

### Где говорить с Hermes

| Где | Как открыть | Для чего |
|---|---|---|
| **Чат ACP Client** | Значок **ACP Client** на левой панели (или `Ctrl+Shift+A`) → в списке **Agents** выбрать **Hermes Remote** | Чат рядом с кодом: Hermes правит файлы проекта и спрашивает подтверждение |
| **Терминал VS Code** | `hermes` или `hermes --tui` | Все возможности Hermes, все `/`-команды |
| **Claude Code, Codex** | Их значки на левой панели | Сами CLI подписок, без Hermes: отдельные агенты со своей историей |
| **Веб-панель** | В терминале `hermes dashboard`, затем щёлкнуть по ссылке `http://127.0.0.1:9119` | Настройки, сессии, навыки, задачи по расписанию — мышкой, в браузере ПК |

Чат ACP Client и терминал — это один и тот же Hermes с одной памятью. В чате работают основные
`/`-команды; если какая-то отклонена — выполните её в терминале.

Веб-панель запускается на сервере, а VS Code сам пробрасывает её порт на ваш ПК (вкладка **Ports**
внизу). Остановить — `Ctrl+C` в том терминале.

### Команды в терминале

```bash
hermes             # чат в терминале
hermes --tui       # то же, но в более удобном полноэкранном интерфейсе
hermes -c          # продолжить последний разговор
hermes -z "вопрос" # один вопрос — один ответ, без чата
```

В чате работают команды через `/`: `/help` — список, `/model` — сменить модель в этом разговоре
(`/model ... --global` — насовсем), `/new` — новый разговор, `/space` — пространства.
Выйти — `/exit` или Ctrl+D.

### Через какую модель он думает

Установщик сразу ставит подписку Claude (`claude-cli`). Поменять:

```bash
hermes model                                  # выбор из списка
hermes config set model.provider codex-cli    # подписка ChatGPT
hermes config set model.provider claude-cli   # подписка Claude
```

Через подписку Hermes остаётся «главным»: он сам ведёт историю, память и инструменты, а CLI только
пишет следующий ответ модели. Ограничение: только текст, картинки и звук через подписку не идут.

### Долгие задачи: чтобы не оборвались

Если закрыть VS Code, терминалы на сервере через какое-то время закрываются вместе с тем, что в них
запущено. Для долгой работы запускайте агента в `tmux` — он живёт на сервере независимо от окна:

```bash
tmux new -s work       # новая сессия; внутри запустите hermes, claude или codex
                       # отключиться, не останавливая: Ctrl+B, затем D
tmux attach -t work    # вернуться (хоть с другого ПК)
```

Бот в Telegram и задачи по расписанию работают сами по себе, им tmux не нужен.

## 5. Hermes в Telegram

1. Создайте бота у [@BotFather](https://t.me/BotFather) и скопируйте токен.
2. В терминале VS Code: `hermes gateway setup` — мастер спросит платформу и токен.
3. `hermes gateway install` — бот будет работать всегда, даже когда VS Code закрыт.
4. `hermes gateway status` — проверить; `hermes gateway restart` — перезапустить после изменений.

Писать боту может только тот, кого вы разрешили в мастере. Токен хранится в `~/.hermes/.env`.
Память у бота та же, что в VS Code: что рассказали в одном месте, агент знает и в другом.

## 6. Пространства: работа, личное, проекты

Пространство — это группа агентов для одной части жизни. У каждого агента **своя** память и история,
поэтому рабочие факты не смешиваются с личными. Команды — в терминале VS Code:

```bash
# Рабочее пространство с новым агентом: настройки и ключи копируются, память пустая
hermes space create work --title "Работа" --new-agent --clone-from default

# Личное — вокруг агента, который у вас уже есть
hermes space create personal --title "Личное" --agent default

# Проект с отдельным агентом, привязанный к папке
hermes space create acme --kind project --new-agent acme --path ~/repos/acme

hermes space list          # что есть
hermes space use work      # теперь `hermes` запускает рабочего агента
hermes space use personal  # обратно к личному
```

- **Привязанная папка:** `hermes` внутри `~/repos/acme` сам запускает агента проекта — удобно, когда
  в VS Code открыт этот проект.
- **В Telegram:** `/space work` — ваши сообщения в этом чате пойдут рабочему агенту, `/space off` —
  обратно. Переключение касается только вас: остальные участники чата остаются со своим агентом.
- **Заметки пространства:** `hermes space note work "Деплой по четвергам"` — это узнают все агенты
  пространства.

## 7. Память

У каждого агента два хранилища: **заметки агента** (`MEMORY.md`) и **что он знает о вас** (`USER.md`).
Агент заполняет их сам; можно и вручную. Файлы лежат в `~/.hermes/memories/` — их можно открыть прямо
в VS Code (**File → Open File**), но надёжнее менять через команды ниже.

**Общая память** — факты, которые должны знать все ваши агенты:

```bash
hermes memory add shared "Меня зовут Алекс. Отвечай по-русски. Часовой пояс — Москва."
hermes memory list shared
hermes space set work --no-shared-memory   # рабочему боту, с которым говорят коллеги, — не показывать
```

**Посмотреть и перенести память:**

```bash
hermes memory list                         # все хранилища и их заполненность
hermes memory list default                 # записи агента default с номерами
hermes memory copy default work            # скопировать всю память агенту work
hermes memory move default:memory work --entries 2-4      # перенести записи 2, 3, 4
hermes memory move work space:acme --match acme           # всё про ACME — в заметки проекта
hermes memory move default:user shared --match имя --dry-run   # сначала посмотреть, что будет
hermes memory remove shared 2              # удалить запись 2
```

Адреса хранилищ: `<агент>` (обе части), `<агент>:memory`, `<агент>:user`, `space:<имя>`, `shared`.
Изменения агенты увидят в **следующем** разговоре. Это специально: так дешевле — модель повторно
использует уже обработанное начало разговора.

## 8. Обновление и резервные копии

В терминале VS Code:

```bash
hermes update             # обновить Hermes из нашего форка
hermes gateway restart    # чтобы бот тоже перешёл на новую версию
```

Обновление берётся из нашего репозитория, не из оригинального. Если `hermes update` предложит добавить
«official repo as upstream» — можно согласиться: свои изменения форк сохранит, автоматически ничего
не перезапишется. `claude` обновляется сам; `codex`, `gemini` и `agy` — повторным запуском
установщика с `-Update` (`--update`).

Резервная копия сервера на ваш ПК (настройки, память, сессии; без токенов CLI). Запускается **на ПК**
(Linux, macOS или WSL, где работает `ssh hermes-dev`) из клона нашего репозитория:

```bash
scripts/remote-dev/backup-pull.sh hermes-dev
```

Копии лежат в `~/.local/share/hermes-server-backups/hermes-dev/`. Восстановить на новом сервере:
`hermes-deploy.sh --name new-box --restore <архив>`. После этого — снова войти в каждый CLI.

## 9. Приватность

По умолчанию в нашем форке:

| Что | Как у нас | Как в оригинале |
|---|---|---|
| Статистика использования | Не собирается и никуда не отправляется. Можно включить отправку только **на свой** сервер: `hermes setup telemetry` | Предлагает отправлять в Nous |
| Анонимный аккаунт Nous | Не создаётся (`nous.guest: false`) | Создаётся при первом обращении к сервисам |
| Повтор неудачного веб-поиска у сторонних сервисов | Выключен (`web.keyless_rescue: false`) | Включён |
| Бот Telegram | Свой токен от BotFather | Предлагает сервис Nous, который видит токен бота |
| Проверка обновлений | Смотрит на наш форк | Смотрит на Nous |

Проверить свои настройки:

```bash
hermes config get telemetry.shared_metrics.send
hermes config get nous.guest
hermes config get web.keyless_rescue
```

Если Hermes ставили до этих изменений, в `config.yaml` могли остаться старые значения — выставьте их
вручную: `hermes config set nous.guest false`, `hermes config set web.keyless_rescue false`.

## 10. Если что-то не работает

| Симптом | Что делать |
|---|---|
| VS Code не подключается к `hermes-dev` | В PowerShell или терминале ПК: `ssh hermes-dev` — должно пустить без пароля. Если нет — перезапустите установщик с теми же параметрами, что при установке |
| В чате ACP Client нет агента **Hermes Remote** | Окно должно быть подключено к серверу (`SSH: hermes-dev` внизу слева); затем `F1` → **Developer: Reload Window** |
| `hermes: command not found` | Откройте новый терминал VS Code |
| Ошибка про лимит подписки | Лимит исчерпан у самого CLI — подождите или смените провайдера (`hermes model`) |
| `claude`/`codex` просит войти | `hermes auth status claude-cli`, затем `claude auth login` или `codex login --device-auth` |
| Бот в Telegram молчит | `hermes gateway status`, затем `hermes logs` |
| Gemini пишет «client is no longer supported» | Личная подписка Google в Gemini CLI закрыта — используйте `agy` |
| После обновления команды ведут себя по-старому | `hermes gateway restart` — фоновый gateway работает со старым кодом |
| Непонятно, что сломалось | `hermes doctor`, затем `hermes logs` |

## 11. Без VS Code

VS Code — основной путь, но не единственный:

- **Только SSH:** `ssh hermes-dev`, дальше всё то же — `hermes`, `hermes --tui`, `tmux`.
- **Команды `hermes remote` с вашего ПК** (если Hermes стоит и на ПК): `hermes remote claude hermes-dev
  --workspace ~/repos/myproject` запускает Claude Code на сервере сразу в tmux. Подробно —
  [REMOTE-DEVELOPMENT-RU.md](REMOTE-DEVELOPMENT-RU.md).
- **Всё на своём компьютере** (Linux, macOS, WSL), без сервера:
  `curl -fsSL https://raw.githubusercontent.com/StApZzZ/hermes-agent/main/scripts/deploy/hermes-deploy.sh | bash`
  (Windows: `hermes-deploy.ps1 -Local` — всё в WSL). Агент в ACP Client тогда называется **Hermes**,
  а терминал VS Code — ваш локальный.

## Шпаргалка

Всё — в терминале VS Code (``Ctrl+` ``), подключённого к `hermes-dev`:

```bash
hermes / hermes --tui / hermes -c      # чат / удобный интерфейс / продолжить
hermes -z "вопрос"                      # один ответ
hermes model                            # выбрать модель
hermes dashboard                        # веб-панель в браузере ПК
hermes space list | use <имя>           # пространства
hermes memory list | add | copy | move  # память
hermes gateway setup | status | restart # мессенджеры
tmux new -s work / tmux attach -t work  # долгая работа, не зависящая от окна
hermes update                           # обновиться
hermes doctor / hermes logs             # диагностика
```

Чат с Hermes рядом с кодом — `Ctrl+Shift+A` (ACP Client → **Hermes Remote**).
