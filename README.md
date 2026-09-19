# Lab100

Пул бесплатных ИИ-агентов веб-поиска с роутингом по компетенциям и доступным токенам.

Менеджер пула следит за квотами провайдеров (Gemini, Groq, Kimi, Cerebras, Cloudflare AI
и др.) и выбирает агента под задачу по двум фильтрам:

1. **Компетенция** — что агент умеет (web-search, deep-research, scrape, code, vision).
2. **Доступные токены** — остаток бесплатной квоты у провайдера/аккаунта.

## Краткое резюме исследования (кейсы)

### Кейс 1 — Полностью бесплатный стек (офлайн)
- **WebBrain** (MIT) + локальная модель через **Ollama** — браузерный агент-расширение.
- Альтернативы: `browserbash-cli` (локально), **Hound** (MCP, поиск+парсинг), **Cloudflare Kitesurf** (облачный браузер, бета).
- Плата — только ресурсы своего ПК, карта не нужна.

### Кейс 2 — Стек с бесплатным ограниченным доступом (облако)
- Браузерная инфраструктура: **Browserbase** (60 мин/мес), **Hyperbrowser** (1 000 кредитов), **Notte** ($10).
- LLM-провайдеры: **Gemini** (1 500 req/день), **Groq** (1 000 req/день), **Kimi** (1 000 вызовов/мес), **Cerebras** (1 млн ток/день), **Cloudflare Workers AI** (10K нейронов/день), **Pollinations** (без ключа).

### Пул агентов (архитектура)
```
Задача → Менеджер пула (orchestrator.py)
              ↓
       Фильтр 1: компетенция (registry.py)
              ↓
       Фильтр 2: доступные токены (token_tracker.py)
              ↓
       Выбор агента → router.py → провайдер
```

Готовые open-source компоненты, на которые опирается каркас:
`llm-keypool` (ротация ключей + 429-cooldown), `OmniRoute` (fallback-цепочки),
`Orquesta-IA` (учёт квот по окнам), `Mission Control` (дашборд).

### Железо
- Облачный вариант (кейс 2): любой современный ПК, 16 ГБ RAM, SSD, GPU не нужна.
- Гибрид с локальной моделью: 32 ГБ RAM + GPU 16–24 ГБ VRAM (или Apple Silicon 32+ GB unified).

## Структура репозитория

```
Lab100/
├── README.md                 # это резюме
├── docs/
│   ├── case-analysis.md      # анализ кейсов 1 и 2
│   ├── architecture.md       # архитектура пула агентов
│   └── hardware.md           # требования к железу
├── lab100/
│   ├── __init__.py
│   ├── registry.py           # реестр агентов по компетенциям
│   ├── token_tracker.py      # квоты и их учёт (кредиты/токены, окна, cooldown)
│   ├── router.py             # выбор агента по компетенциям и квоте, fallback-каскад
│   ├── orchestrator.py       # конвейер поиск/ответ/код/скрейпинг
│   ├── cli.py                # командный интерфейс (ask/search/chat/scrape)
│   └── providers/
│       ├── base.py           # интерфейсы SearchAdapter / LLMAdapter
│       ├── firecrawl.py      # веб-поиск и скрейпинг (REST v2), кейс 2
│       ├── local_browser.py  # локальный веб-агент через headless-браузер (кейс 1)
│       ├── plain_http.py     # лёгкий HTTP-скрейпинг/поиск без браузера (кейс 1)
│       ├── ollama.py         # локальный LLM (REST /api/chat)
│       └── gemini.py         # Google Gemini (подключается при наличии ключа)
├── config/
│   └── agents.toml           # конфигурация агентов, провайдеров и квот
└── tests/
    └── test_pool.py          # тесты на моках (без сети)
```

## Быстрый старт (рабочий вариант)

```powershell
# пул, квоты, активные адаптеры
python -m lab100.cli status
python -m lab100.cli providers

# веб-поиск (Firecrawl) + ответ локальным LLM (Ollama)
python -m lab100.cli ask "вопрос" --limit 5

# принудительно глубоким локальным LLM (hermes3:8b, медленнее на CPU)
python -m lab100.cli ask "сложный вопрос" --llm ollama-8b

# только поиск, без ответа LLM
python -m lab100.cli search "вопрос" --limit 5 --no-llm

# Кейс 1: локальные поиск/скрейпинг БЕЗ Firecrawl и ключей (кредиты = 0)
python -m lab100.cli search "вопрос" --search local-browser --no-llm   # headless-браузер (Wiby/Bing/Wikipedia)
python -m lab100.cli search "вопрос" --search plain-http --no-llm     # лёгкий HTTP (Bing-HTML)
python -m lab100.cli scrape "https://site" --search local-browser     # JS-рендеринг
python -m lab100.cli scrape "https://site" --search plain-http        # статические страницы

# только генерация текста / код
python -m lab100.cli chat "промпт" --capability answer
python -m lab100.cli chat "промпт" --capability code

# извлечение контента страницы в markdown
python -m lab100.cli scrape "https://example.com"

# тесты (моки, без сети)
python -m pytest tests -q
```

Активные провайдеры (через REST, без SDK):
- **Firecrawl** — веб-поиск и скрейпинг, ключ `FIRECRAWL_API_KEY`, расход — кредиты (кейс 2).
- **local-browser** — локальный веб-агент Кейса 1: поиск и скрейпинг через headless Edge/Chrome
  (драйвер `driver.mjs`, `127.0.0.1:8123`). Кредиты = 0. Движки: `wiby` (по умолчанию, без капчи),
  `bing`, `duckduckgo`, `wikipedia`. Скрейпинг разруливает JS и редиректы. Драйвер поднимается
  автоматически, если доступен `node.exe` (Windows).
- **plain-http** — самый лёгкий адаптер Кейса 1: поиск также через Bing-HTML (URL из redirect'ов
  декодируются) и скрейпинг без браузера — только статические страницы, без JS.
- **Ollama (hermes3:3b)** — быстрая локальная LLM по умолчанию.
- **Ollama-8b (hermes3:8b)** — локальная LLM-резерв для глубоких задач; принудительно
  через `--llm ollama-8b`, автоматически — если 3b в cooldown. На CPU ~10–15 ток/с,
  поэтому не стоит как дефолт (узкое место — 16 ГБ RAM, одна модель в памяти).
- **Gemini** — подключается автоматически, если в окружении появится `GEMINI_API_KEY`.
- **OpenAI** — не используется: ключ на этой машине отклоняется по региону
  (`unsupported_country_region_territory`).

Приоритет по умолчанию: Firecrawl (кейс 2) > local-browser > plain-http (кейс 1). Если
Firecrawl в cooldown/нет кредитов — менеджер пула автоматически свайтчнется на локальный
веб-агент, не тратя ничего. Движок/порт настраиваются в `config/agents.toml`.

Квоты задаются в `config/agents.toml` (кредиты/токены + окно пополнения). Агент,
у которого квота на исходе или клёв в cooldown после 429, автоматически пропускается;
пул переключается на следующего кандидата по приоритету.

Зависимости: только стандартная библиотека Python 3.10+ (`tomllib`, `urllib`).
`pytest` — только для тестов.

## Рабочий цикл

Проект хранится в приватном репозитории `Lab-100/Lab100`. Публикация — только по
явной команде владельца («в релиз»). Версия проекта: см. теги/коммиты.