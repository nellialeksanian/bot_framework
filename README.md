# botkit — фреймворк для чат-ботов

Фреймворк по документу [«Модули фреймворка — приоритет.md»](docs/Модули%20фреймворка%20—%20приоритет.md).
Обновления B1–B5/C1 загружены из итогового репозитория (HEAD `9cc6f76`).
Локально добавлены A1/A2/A4 и A5; доменная логика остаётся в конкретных ботах.
Изменения подготовлены для review в отдельной ветке по запросу владельца.
Статус проверок и открытые ограничения — в [PR validation](docs/PR%20validation.md).

Пакет называется `botkit`, устанавливается как обычная Python-библиотека
(`pip install`) — см. «Установка» ниже.

## Структура

```
pyproject.toml    манифест пакета — имя, версия, зависимости

src/botkit/
├── llm/          A1  LLM Provider Gateway              — реализован локально
├── usage/        A2  Usage & Cost Tracker               — реализован локально
├── rag/          A3  RAG Ingestion & Retrieval           — реализован (chroma_store.py + др.)
├── transport/    A4  Messenger Transport Adapter        — реализован локально
├── extraction/   A5  Content Extraction Layer            — реализован локально
├── dialog_policy/ B1 Dialogue Policy Engine   (SC01+SC06) — реализован (engine.py, intent_router.py)
├── attempts/     B2  Attempt & Revision Store (SC07)      — реализован (sqlite_store.py, sessions.py)
├── rubric/       B3  Rubric & Taxonomy Store  (SC08)      — реализован (sqlite_store.py)
├── authority/    B4  Human Authority Gate     (SC14+SC16) — реализован (gate.py, identity_env.py)
├── evidence/     B5  Evidence & Citation Layer (SC04+SC05) — реализован (llm_checker.py, sqlite_store.py)
└── simulation/   C1  Simulation & Scenario Engine (SC02+SC03) — реализован (persona.py, scenario.py, routing.py)

policies/         данные SupportPolicy / CriterionPackage (YAML) — по одной на навык
tests/            тесты по модулям, зеркалят структуру src/botkit/
docs/             исходные документы спецификации (см. ниже)
```

Код лежит под `src/`, а не прямо в корне репозитория — это стандартный
`src`-layout: он не даёт случайно импортировать неустановленный пакет из
текущей директории и гарантирует, что `pip install` действительно
устанавливает то же самое дерево, которое потом тестируется.

## Установка

Локально, в режиме разработки (правки в `src/botkit/` сразу видны без
переустановки):

```bash
pip install -e ".[dev,messengers,office,huggingface]"
# либо окружение с зафиксированными версиями:
uv sync --extra dev --extra messengers --extra office --extra huggingface
```

Из другого проекта, без публикации в PyPI — прямо по пути или по git:

```bash
pip install /путь/к/bot_framework
# или
pip install git+https://github.com/nellialeksanian/bot_framework.git
```

Для проверки ещё не слитого PR добавьте `@имя-ветки` в конец Git URL.
Репозиторий приватный: потребуется настроенная GitHub-аутентификация.

После установки импорт одинаковый в любом случае:

```python
from botkit.llm.base import LLMProvider, load_llm
from botkit.dialog_policy.base import SupportPolicy
```

## docs/

Копии документов, на которых основан каркас — чтобы спецификация физически
лежала рядом с кодом, а не только в родительской папке портфеля:

**Спецификация и происхождение контрактов:**

- [«Модули фреймворка — приоритет.md»](docs/Модули%20фреймворка%20—%20приоритет.md) —
  первоисточник структуры `framework/`, порядка внедрения и всех интерфейсов.
- [«Модули фреймворка — будущее.md»](docs/Модули%20фреймворка%20—%20будущее.md) —
  SC-контракты вне первой волны и оценка сложности их добавления позже.
- [«Общие принципы ботов и связь с SC.md»](docs/Общие%20принципы%20ботов%20и%20связь%20с%20SC.md) —
  разбор 15 реально реализованных ботов, на основе которого отобраны модули A1–A5.
- [«Реестр контрактов SC01-SC20.md»](docs/Реестр%20контрактов%20SC01-SC20.md) —
  SC-каталог, источник контрактов и приоритетов для модулей B1–B5.

«B1 — Dialogue Policy Engine — assumption doc.md» (почему интерфейс B1
выведен из живого кода 15 ботов, а не из SC-каталога — SC01 не извлечён
формально) в `.gitignore` намеренно: рабочая заметка, не для публикации в
репозитории. Она существует только локально на диске автора, не как копия
в этом чекауте — ссылки на неё здесь нет, файла не будет ни у кого другого,
кто клонирует репозиторий.

**Практические руководства («как пользоваться») по уже реализованным модулям:**

- [«RAG — как пользоваться.md»](docs/RAG%20—%20как%20пользоваться.md) —
  `botkit.rag`: как вызывать ingest/search, как выглядит финальный `Chunk`
  со всеми метаданными, что идёт в промпт модели.
- [«Attempts — как пользоваться.md»](docs/Attempts%20—%20как%20пользоваться.md) —
  `botkit.attempts`: `SQLiteAttemptStore`, `record_revision`, два режима
  подключения `ActiveSessionResolver`.
- [«Dialog Policy — как пользоваться.md»](docs/Dialog%20Policy%20—%20как%20пользоваться.md) —
  `botkit.dialog_policy`: регистрация навыков, `IntentRouter`, `check_attempt_gate`/`render_support_block`.
- [«Rubric — как пользоваться.md»](docs/Rubric%20—%20как%20пользоваться.md) —
  `botkit.rubric`: `CriterionPackage`, `RaterRecord`, синхронизация версий критериев.
- [«Evidence — как пользоваться.md»](docs/Evidence%20—%20как%20пользоваться.md) —
  `botkit.evidence`: `LLMEvidenceChecker`, `SQLiteEvidenceStore`, `verify_and_record`,
  связь с A3 (RAG retrieval vs entailment).
- [«Authority — как пользоваться.md»](docs/Authority%20—%20как%20пользоваться.md) —
  практическое руководство по `botkit.authority`: EnvIdentityGate + AuthorityGate,
  привязка required_role к навыку, и почему DecisionEvent (SC14) пока реализован,
  но не подключён ни к одному навыку.
- [«Simulation — как пользоваться.md»](docs/Simulation%20—%20как%20пользоваться.md) —
  `botkit.simulation`: `PersonaSimulator`, `ScenarioGenerator`, интеграция с B1 через `route_or_continue`.

При обновлении документа-первоисточника в родительской папке портфеля —
копию в `docs/` нужно обновлять вручную (`cp`), автосинхронизации нет.

## Что не входит в каркас

Доменная логика конкретного бота (например, проверка RACI-матриц) не является частью фреймворка — она пишется поверх него в самом боте, используя эти модули как строительные блоки.

## Платформа A1 / A2 / A4 / A5

- [Ручной тест перед merge: env, модели, Telegram/VK](docs/Acceptance%20smoke%20test.md)
- [LLM, учёт и мессенджеры](docs/Platform%20A1%20A2%20A4.md)
- [Извлечение документов, PDF и изображений](docs/Extraction%20A5.md)
- [Подключение существующих ботов](docs/Platform%20migration.md)
- [Проверки A1/A2/A4 от 17 сентября](docs/Platform%20validation.md)
- [Обновление и проверки A5 от 23 сентября](docs/Extraction%20validation.md)

```bash
python -m botkit demo
# После заполнения .env по .env.example; отвечает в реальных чатах:
python -m botkit run --platform telegram vk
python -m botkit stats --db data/usage.sqlite3
python -m botkit export --db data/usage.sqlite3 --output data/export.jsonl
```

`demo` работает без сети и ключей. `run` — демонстрационный бот с обработкой
вложений A5, не замена всех навыков исходных ботов. Для запуска нужны токены.
Реальные тарифы задаются в `config/prices.json`; неизвестная цена — не ноль.
Ключи старых ботов в репозиторий не копируются.

PR предназначен для review, не является подтверждением полной приёмки или
разрешением на production deploy. Известные ограничения перечислены в `docs/PR validation.md`.
