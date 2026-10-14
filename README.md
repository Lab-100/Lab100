# Lab100

Пул бесплатных ИИ-агентов веб-поиска с роутингом по компетенциям и доступным токенам.

Менеджер пула следит за квотами провайдеров и выбирает агента под задачу по двум фильтрам:

1. **Компетенция** — что агент умеет (web-search, deep-research, scrape, code, vision).
2. **Доступные токены/кредиты** — остаток бесплатной квоты у провайдера/аккаунта.

## Реализованные провайдеры (пул работает)

- **Firecrawl** — облачный веб-поиск и скрейпинг, ключ `FIRECRAWL_API_KEY`, расход — кредиты (месячная квота 1000). Основной и самый качественный источник.
- **Exa** — семантический веб-поиск (`/search` с highlights + `/contents`), ключ `EXA_API_KEY`, расход — кредиты. Подключается при наличии ключа, приоритет 45 (ниже Firecrawl).
- **local-browser** — локальный веб-агент Кейса 1: поиск и скрейпинг через headless Edge/Chrome (драйвер `driver.mjs`, `127.0.0.1:8123`), кредиты = 0. Движки: `wiby` (по умолчанию, без капчи), `bing`, `duckduckgo`, `wikipedia`. Скрейпинг разруливает JS и редиректы. Драйвер поднимается автоматически, если доступен `node.exe`.
- **plain-http** — самый лёгкий адаптер Кейса 1: поиск через Bing-HTML (URL из редиректов декодируются) и скрейпинг без браузера — только статические страницы, без JS. Кредиты = 0.
- **Yandex Search API** — веб-поиск по базе Яндекса (REST `POST /v2/web/search`, ответ `rawData` = base64/XML), только `web-search`, без скрейпинга. Ключ `YANDEX_SEARCH_API_KEY` (или `YANDEX_SEARCH_IAM_TOKEN` для сервисного аккаунта), `folderId` — `YANDEX_SEARCH_FOLDER_ID`. Платный (тарификация по запросам), поэтому приоритет 15 — последний в каскаде, месячный предохранитель 500 запросов. Полезен как источник по РУ-интернету; принудительно — `--search yandex-search`.
- **Ollama (hermes3:3b)** — быстрая локальная LLM по умолчанию (REST `/api/chat`, 0 кредитов).
- **Ollama-8b (hermes3:8b)** — локальная LLM-резерв для глубоких задач; принудительно через `--llm ollama-8b`, автоматически — если 3b в cooldown. На CPU ~10–15 ток/с, поэтому не дефолт.
- **Gemini** — модель `gemini-3.6-flash` (актуальная для новых пользователей), ключ `GEMINI_API_KEY`, квота 150 000 токенов/день. Подключается автоматически при ключе.

## Ограничения сети (актуально для этой машины)

- **Exa** и **Gemini** недоступны напрямую из РФ/по этому IP: Exa режется Cloudflare (HTTP 403), Gemini отдаёт `User location is not supported` (400). Ключи при этом **валидны** (проверено через GitHub Actions с US-IP). Пул корректно каскадирует: при блокировке провайдера автоматически переходит на следующего, задача не падает.
- **OpenAI** — не используется: ключ на этой машине отклоняется по региону (`unsupported_country_region_territory`).

## Пул агентов (архитектура)

```
Задача → Менеджер пула (orchestrator.py)
              ↓
       Фильтр 1: компетенция (registry.py)
              ↓
       Фильтр 2: доступные кредиты/токены (token_tracker.py)
              ↓
       Выбор агента → router.py (fallback-каскад) → провайдер
```

Каскад: при ошибке провайдера (429, 403, сеть) роутер переходит на следующего кандидата
по приоритету, поставив упавшего в cooldown (10 минут).

## Структура репозитория

```
Lab100/
├── README.md                 # это резюме
├── CHANGELOG.md              # журнал изменений (версии 0.x.y)
├── .github/workflows/
│   └── tests.yml             # непрерывная проверка тестов (push в main и pull request)
├── lab100/
│   ├── __init__.py
│   ├── registry.py           # реестр агентов по компетенциям
│   ├── token_tracker.py      # квоты и их учёт (кредиты/токены, окна, cooldown)
│   ├── router.py             # выбор агента по компетенциям и квоте, fallback-каскад
│   ├── orchestrator.py       # конвейер поиск/ответ/код/скрейпинг
│   ├── cli.py                # командный интерфейс (status/ask/search/chat/scrape)
│   ├── mcp_server.py         # MCP-сервер для opencode (единая точка подмены поиска)
│   └── providers/
│       ├── base.py           # интерфейсы SearchAdapter / LLMAdapter
│       ├── firecrawl.py      # веб-поиск и скрейпинг (REST v2)
│       ├── exa.py            # семантический поиск Exa (/search + /contents)
│       ├── local_browser.py  # локальный веб-агент через headless-браузер (Кейс 1)
│       ├── plain_http.py     # лёгкий HTTP-скрейпинг/поиск без браузера (Кейс 1)
│       ├── yandex_search.py  # веб-поиск по базе Яндекса (REST v2, base64+XML)
│       ├── ollama.py         # локальная LLM (REST /api/chat)
│       └── gemini.py         # Google Gemini
├── config/
│   └── agents.toml           # конфигурация агентов, провайдеров и квот
└── tests/
    ├── test_pool.py          # тесты на моках (без сети)
    ├── test_mcp_server.py    # тесты MCP-сервера (без сети)
    └── test_yandex_search.py # тесты Yandex Search API (сеть замокана)
```

## MCP-сервер (интеграция с opencode)

Пул Lab100 отдаётся ИИ-агентам opencode как MCP-сервер — единая точка подмены
поиска: вместо нескольких поисковых MCP подключается один `lab100`, а ротация
провайдеров (каскад с cooldown при 429/403/сети) работает внутри. По смыслу это
то же, что агрегатор `multitool` для моделей.

Запуск (транспорт stdio):

```powershell
python -m lab100.mcp_server --config config/agents.toml   # из клона
lab100-mcp                                                # после pip install .[mcp]
```

Инструменты:

- `lab100_search(query, limit=5, capability="web-search")` — веб-поиск с ротацией;
- `lab100_scrape(url)` — markdown по URL;
- `lab100_ask(question, limit=5, capability="answer")` — ответ LLM по контексту;
- `lab100_status()` — состояние пула: агенты, остаток квоты, отключённые адаптеры.

Подключение в `opencode.jsonc` (секция `mcp`):

```jsonc
"lab100": {
  "type": "local",
  "command": ["lab100-mcp", "--config", "C:\\path\\to\\Lab100\\config\\agents.toml"],
  "enabled": true,
  "timeout": 120000
}
```

Для платных источников ключи берутся из окружения MCP-процесса:
`FIRECRAWL_API_KEY`, `EXA_API_KEY`, `YANDEX_SEARCH_API_KEY`, `GEMINI_API_KEY`.
Без ключей пул сам опускается на бесплатные адаптеры (`plain-http`, `local-browser`).

## Установка

```powershell
# из клона — обычный запуск без установки
python -m lab100.cli status

# установка пакета (появляется команда lab100)
pip install .
pip install -e .   # режим разработки: правки в коде применяются сразу
```

Зависимостей нет — только стандартная библиотека Python 3.11+. Все ключи
провайдеров берутся из переменных окружения, в репозитории их нет.

## Быстрый старт (рабочий вариант)

```powershell
# пул, квоты, активные адаптеры
python -m lab100.cli status
python -m lab100.cli providers

# веб-поиск (каскад: Firecrawl/Exa → local-browser → plain-http → yandex-search) + ответ локальным LLM
python -m lab100.cli ask "вопрос" --limit 5

# принудительно глубоким локальным LLM (hermes3:8b, медленнее на CPU)
python -m lab100.cli ask "сложный вопрос" --llm ollama-8b

# только поиск, без ответа LLM
python -m lab100.cli search "вопрос" --limit 5 --no-llm

# Кейс 1: локальный поиск БЕЗ Firecrawl и ключей (кредиты = 0)
python -m lab100.cli search "вопрос" --search local-browser --no-llm   # headless-браузер
python -m lab100.cli search "вопрос" --search plain-http --no-llm     # Bing-HTML
python -m lab100.cli scrape "https://site" --search local-browser     # JS-рендеринг
python -m lab100.cli scrape "https://site" --search plain-http        # статические стр.

# принудительно базой Яндекса (нужны YANDEX_SEARCH_API_KEY и YANDEX_SEARCH_FOLDER_ID; 1 запрос = 1 кредит)
python -m lab100.cli search "русскоязычный запрос" --search yandex-search --no-llm

# только генерация текста / кода
python -m lab100.cli chat "промпт" --capability answer
python -m lab100.cli chat "промпт" --capability code

# извлечение содержимого страницы в markdown (каскад провайдеров)
python -m lab100.cli scrape "https://example.com"

# тесты (моки, без сети)
python -m pytest tests -q
```

Приоритет по умолчанию: Firecrawl → Exa → local-browser → plain-http → yandex-search (поиск);
ollama (3b) → ollama-8b → gemini (LLM). Если провайдер блокируется сетью
(Exa/Gemini), лимит исчерпан или он в cooldown — менеджер автоматически переходит
к следующему кандидату, кредиты не тратятся зря. Движки/порт/квоты настраиваются
в `config/agents.toml`.

### Yandex Search API (платный резерв)

```powershell
# ключ кабинета (роль search-api.user / executor на сервисный аккаунт)
$env:YANDEX_SEARCH_API_KEY = "AQVN..."
$env:YANDEX_SEARCH_FOLDER_ID = "b1g..."   # нужен для сервисного аккаунта
# либо IAM-токен сервисного аккаунта вместо API-ключа
$env:YANDEX_SEARCH_IAM_TOKEN = "t1...."
```

Ограничения сервиса, которые учитывает адаптер: запрос ≤ 400 символов и ≤ 40 слов,
`groupsOnPage` 1..100, `maxPassages` 1..5, плоская группировка по домену
(`GROUP_MODE_FLAT`). Скрейпинга нет — агент отдаёт только `web-search`.

Квоты задаются в `config/agents.toml` (кредиты/токены + окно пополнения). Агент,
у которого квота на исходе или ключ в cooldown после 429, автоматически пропускается;
пул переключается на следующего кандидата по приоритету.

Зависимости: только стандартная библиотека Python 3.11+ (`tomllib`, `urllib`).
`pytest` — только для тестов.

## Непрерывная проверка тестов (CI)

Workflow `.github/workflows/tests.yml` запускает тесты при push в `main`
и при pull request в `main`, на Python 3.11/3.12 (`requires-python >=3.11`).
В лог выводится `python -m lab100.cli providers` — какие адаптеры пула поднялись,
а какие отключены и почему (нет ключа или недоступен локальный сервис).

Известные ограничения (падания намеренно не скрываются):

- `tests/test_pool.py::test_config_has_case1_local_providers` требует секрет
  `FIRECRAWL_API_KEY` (иначе `firecrawl` не попадает в пул), поэтому без него
  тест честно падает.
- `tests/test_pool.py::test_config_loads_and_build` требует LLM-агента в пуле
  (локальная Ollama, Gemini с ключом или docker-agent), иначе падает на проверке
  `len(by_kind("llm")) >= 1`. В CI (ubuntu с Docker) проходит.

## Рабочий цикл

Проект хранится в приватном репозитории `Lab-100/Lab100`. Публикация — только по
явной команде владельца («в релиз»). Версия проекта: см. теги/коммиты.