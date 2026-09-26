# Архитектура пула агентов поиска (кейс 2)

## Проблема

Бесплатные квоты провайдеров исчерпываются (429/limit), различаются по компетенциям
(web search, deep research, scrape, code, vision) и по окнам пополнения (день/месяц/разово).
Нужен менеджер, который выбирает агента под задачу с учётом двух фильтров.

## Схема

```
Задача (prompt + требования по компетенции)
        │
        ▼
┌─────────────────────┐
│   orchestrator.py   │  менеджер пула (точка входа)
└─────────┬───────────┘
          │ 1. компетенции
          ▼
┌─────────────────────┐
│   registry.py       │  реестр агентов: name, capabilities, provider, quota policy
└─────────┬───────────┘
          │ 2. доступные токены/квоты
          ▼
┌─────────────────────┐
│   token_tracker.py  │  учёт: remaining, window, locked_until (cooldown 429)
└─────────┬───────────┘
          │ 3. выбор лучшего кандидата
          ▼
┌─────────────────────┐
│   router.py         │  приоритизация: квота → скорость → надёжность
└─────────┬───────────┘
          │
          ▼
    провайдер/адаптер (Gemini, Groq, Kimi, Ollama, ...)
```

## Компоненты

### registry.py — реестр агентов

Агент = запись с полями:
- `name` — уникальный id;
- `provider` — к какому пулу ключей относится;
- `capabilities` — список компетенций (web-search, deep-research, scrape, code, vision);
- `quota_policy` — откуда брать остаток (ежедневная/ежемесячная/разовая квота).

### token_tracker.py — учёт квот

- хранит остатки в состоянии (по умолчанию in-memory, позже — файл/Redis);
- знает окно пополнения (day/month/forever) и `locked_until` для cooldown после 429;
- может «предсказать»: сколько останется к началу окна.

### router.py — выбор исполнителя

Фильтр 1 — компетенция обязательна:
    candidate = агент, у которого запрошенная компетенция ∈ capabilities

Фильтр 2 — остаток > порога замещения (напр. > 10% окна или > N токенов):
    candidate = сортировка по (remaining desc, price asc, reliability desc)

Бонус: fallback-цепочка — при исчерпании первого агента переключиться на
следующего кандидата (как OmniRoute `Subscription → API Key → Cheap → Free`).

### orchestrator.py — менеджер пула

- принимает задачу;
- вызывает registry → candidate-список;
- вызывает token_tracker → исключает «пустых»;
- вызывает router → выбирает лучшего;
- резервирует часть квоты (проактивный учёт, не только post-factum);
- регистрирует результат (успех/ошибка) и обновляет состояние.

## Сопоставление с готовыми open-source решениями

| Слой | Этот проект | Готовое решение |
|---|---|---|
| Ротация ключей / 429 | token_tracker (переиспользовать) | llm-keypool (ротация, теги компетенций) |
| Маршрутизация/fallback | router | OmniRoute (4-tier fallback, 150+ провайдеров) |
| Учёт квот по окнам | token_tracker | Orquesta-IA (state/ledger.jsonl) |
| Дашборд | (позже) | Mission Control (self-hosted, SQLite) |

## Адаптеры провайдеров

Каждый провайдер подключается через интерфейс `ProviderAdapter`:
- `name`, `check_quota()` → остаток;
- `run(prompt)` → выполнить задачу;
- `cooldown(reason)` → зафиксировать 429/limit.

Список адаптеров для реализации: gemini, groq, kimi, cerebras, cloudflare-workers,
pollinations (без ключа), ollama (локальный резерв).

### Реализовано в `lab100/providers/`

| Файл | Провайдер | Компетенции | Расход |
|---|---|---|---|
| `firecrawl.py` | Firecrawl (REST v2) | web-search, deep-research, scrape | кредиты |
| `exa.py` | Exa (REST) | web-search, deep-research, scrape | кредиты |
| `local_browser.py` | local-browser (headless Edge/Chrome) | web-search, deep-research, scrape | 0 |
| `plain_http.py` | plain-http (без браузера) | web-search, scrape | 0 |
| `yandex_search.py` | Yandex Search API (REST v2) | web-search | 1 за запрос |
| `ollama.py` | Ollama 3b/8b | answer, code | 0 |
| `gemini.py` | Gemini | answer, code, vision | токены |
| `docker_agent.py` | Docker Agent (Гордон) | answer, code | 0 |

### Yandex Search API: особенности подключения

- `POST https://searchapi.api.cloud.yandex.net/v2/web/search`; тело — `query`
  (`searchType`, `queryText`, `page`), `groupSpec` (`GROUP_MODE_FLAT`,
  `groupsOnPage`, `docsInGroup`), `maxPassages`, `folderId`, `responseFormat`.
- Ответ `200`: `{"rawData": base64(XML)}` — XML разбирается `xml.etree.ElementTree`,
  документы берутся из `<response><results><grouping><group><doc>`, дедупликация по URL.
- Авторизация: `Authorization: Api-Key <KEY>` (кабинет) либо `Bearer <IAM-токен>`
  (сервисный аккаунт); `folderId` обязателен для сервисного аккаунта.
- Адаптер не умеет скрейпинг → `capabilities = ["web-search"]`, `scrape()` бросает
  `AdapterError`, поэтому в каскаде скрейпинга агент не участвует.
- Валидация до сети: запрос ≤ 400 символов и ≤ 40 слов, `groupsOnPage` ограничен 100.
- Ошибки HTTP 400/401/403/429/сетевые → `AdapterError` → cooldown 10 минут и каскад
  на следующего провайдера; ключ в текст ошибок не попадает.
- Платный сервис: тарификация по числу запросов, поэтому `priority = 15` (последний
  в поисковом каскаде) и месячная квота-предохранитель 500 запросов.


## Принципы

- Проактивный учёт: резервируем квоту ДО вызова, а не списываем после.
- Всегда падаем на локальный резерв (Ollama), если облачная квота пуста.
- Состояние переживаемо (файл/Redis), чтобы перезапуск не терял остатки.