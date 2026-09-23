# A1 / A2 / A4: использование

Код встроен в существующий пакет `botkit`. Исходные импорты `*.base`, порядок
полей `LLMResponse` и `UsageEvent` сохранены; новые поля добавлены после старых.
Реализации RAG и Dialogue Policy не заменялись.

## A1. Провайдеры и fallback

Поддерживаются `hub` (также старое имя `local_hub`), `302ai`, `vllm`
(алиас `vlm`) и `openai_compatible`. Требуется Chat Completions API.
Для изображений нужна модель с vision-возможностями; совместимый API сам по
себе не добавляет их текстовой модели. Coze в этой реализации не добавлен.

```python
from dotenv import load_dotenv
from botkit.llm import load_llm
from botkit.usage import PriceBook, SQLiteUsageTracker, usage_context

load_dotenv()  # Явная загрузка .env в точке входа приложения.
tracker = SQLiteUsageTracker("data/usage.sqlite3", prices=PriceBook("config/prices.json"))

async def ask(prompt: str):
    async with load_llm(tracker=tracker) as llm:
        with usage_context(bot_id="organizer", user_id="test-user", skill_context="MATRIX_CHECK"):
            result = await llm.ainvoke(prompt, temperature=0, max_tokens=None)
            return result.response
```

Для длительно работающего бота создавайте один клиент при запуске и закрывайте
его через `aclose()` при остановке, а не создавайте новый на каждое сообщение.
`max_tokens=None` не отправляется провайдеру: сохраняется поведение organizer.

### Конфигурация

Все переменные приведены в `.env.example`. Важные группы:

| Назначение | Переменные |
|---|---|
| Хаб | `LLM_PROVIDER=hub`, `LOCAL_HUB_API_BASE`, `LOCAL_HUB_API_KEY`, `LOCAL_HUB_MODEL_NAME` |
| Резервная модель хаба | `LOCAL_HUB_FALLBACK_MODEL_NAME`, `LLM_FALLBACK_ENABLED=true` |
| Резерв с другим провайдером | `LLM_FALLBACK_PROVIDER`, `LLM_FALLBACK_MODEL` |
| Прямой 302.ai | `API_302AI_KEY`, `A302_API_BASE`, `A302_MODEL_NAME`, `A302_MODE=chat` или `async` |
| Дополнительный прямой резерв | `LLM_DIRECT_FALLBACK=true` — только явное включение |
| vLLM | `LLM_PROVIDER=vllm`, `VLLM_BASE_URL`, `VLLM_MODEL`, `VLLM_API_KEY` |
| Таймауты | `LLM_TIMEOUT` — общий бюджет, `LLM_PRIMARY_TIMEOUT` — верхний предел первой попытки |
| Повторы | `LLM_MAX_RETRIES` — ограниченные повторы 429/503 |

Путь `/ai/v1` хаба сохраняется. `/v1` добавляется только к адресу без пути.
Фабрика без явного `config` собирает цепочку из переменных окружения:
Qwen на хабе → резервная модель хаба → прямой 302.ai (если включён).
Первые два шага используют одну точку доступа: при падении всего хаба оба
могут быть недоступны. Прямой резерв меняет маршрут данных и оплачивается
отдельно, поэтому по умолчанию выключен.

`load_llm(config=LLMConfig(...))` создаёт ровно одного провайдера.
`load_llm(fallback=False)` также отключает сборку цепочки.
Собственную цепочку можно собрать через `FallbackLLM(primary, [backup], tracker=tracker)`.

При сетевой/HTTP-ошибке, таймауте, пустом или обрезанном ответе (`finish_reason=length`)
цепочка переходит к следующей модели. Общий дедлайн делится так, чтобы оставить
время резервам. Ошибка программирования и отмена задачи не запускают новую модель.
Неоднозначно завершившийся POST не повторяется автоматически на том же провайдере;
fallback после потери соединения всё же может означать два оплаченных вызова.

Проверка TLS включена по умолчанию. Для частного сертификата используйте
`LOCAL_HUB_CA_BUNDLE` / `LLM_CA_BUNDLE`. Старый organizer отключал проверку;
здесь это возможно только явной настройкой `LOCAL_HUB_VERIFY_SSL=false`,
что небезопасно и не исправляет обрыв TLS самим сервером/сетью.
`LLM_TRUST_ENV=false` отключает использование proxy-настроек окружения.

### Текст, изображения, полный ответ

```python
# История, system prompt, tools и response_format — через тот же шлюз:
result = await llm.achat([
    {"role": "system", "content": "Отвечай кратко."},
    {"role": "user", "content": "Объясни RACI."},
], temperature=0)

# Attachment.read_bytes() либо bytes локального изображения:
image_result = await llm.ainvoke_with_image(
    "Извлеки таблицу в JSON.", image_bytes, mime_type="image/png",
)
# Вариант для уже закодированного изображения:
image_result = await llm.ainvoke_with_image_b64(
    "Извлеки таблицу в JSON.", image_base64, mime_type="image/png",
)
```

Изображение отправляется inline `data:<mime>;base64,...`, как в organizer_bot2.
Не требуется, чтобы сервер модели мог скачать внешнюю ссылку.
История с несколькими изображениями передаётся через `achat` в формате content-parts.
`result.response` содержит извлечённое поле `response` (или исходный текст,
если такого поля нет); `result.raw` всегда сохраняет полный текст ответа модели.
Парсинг: JSON → JSON в markdown → regex для повреждённого JSON → исходный текст.
`parse_status` явно показывает результат; fallback по JSON-схеме задаёт вызывающий код.

Также доступны `astream(messages)` (SSE), `aembed(texts)` и `list_models()`.
Для внутреннего хаба по уточнению от 23 сентября streaming по умолчанию
отключён (`LOCAL_HUB_STREAMING=false`), а между завершением одного запроса и
началом следующего выдерживается 2 секунды (`LOCAL_HUB_REQUEST_INTERVAL=2`).
Это задержка между запросами, не таймаут. Остальные провайдеры сохраняют SSE.
В режиме 302.ai `async` поддерживается polling обычных вызовов, но не SSE.
Streaming переключает провайдера только до выдачи первой содержательной части:
частично показанный ответ не повторяется. Эмбеддинги не переключаются автоматически,
чтобы не смешивать несовместимые векторные пространства.

### Проверка ответа и предметные ошибки

Передайте `validator=callable` в `ainvoke`, `achat` или вызов с картинкой.
Валидатор получает `LLMResponse` и либо завершается, либо выбрасывает:

- `InvalidLLMResponse`: ответ непригоден, попробовать следующую модель;
- `DoNotFallbackError`: корректный предметный исход, не тратить резервный вызов.

Например, `{"grid": null}` («на картинке нет таблицы») — не сбой Qwen.
Готовый пример — `examples/hub_fallback_vision.py` (принимает PNG).
Один провайдер тоже выполняет валидатор, но без резервов передаёт ошибку вызывающему коду.

## A2. Учёт и диагностика

`SQLiteUsageTracker` использует append-only таблицу SQLite/WAL, отдельные короткие
соединения в worker-thread; параллельные запросы не перезаписывают весь журнал.
`usage_context(...)` привязывает события к боту, пользователю, чату, платформе,
навыку и request ID; вложенный контекст сохраняет внешнюю привязку.

Сохраняются модель/провайдер, статус, доступные input/output/cached/reasoning tokens,
latency и время первого токена, HTTP status, класс ошибки, retry/poll counters,
request IDs, parse/finish status, параметры генерации и fallback chain/index/reason.
Каждый вызванный провайдер учитывается отдельно. Ошибки доменной валидации имеют
`kind=validation` и не дублируют стоимость уже записанного вызова.
Встроенные транспортные операции и обработчики также записывают события.

Это данные, доступные клиенту: загрузку GPU, внутренние очереди vLLM и реальные
списания со счёта нельзя получить из обычного Chat Completions response.
Оценка стоимости не является сверкой счёта провайдера.

По умолчанию тексты и изображения не сохраняются: только длины и SHA-256 текстов.
`LOG_TEXT=true` / `log_text=True` включает ограниченные по длине тексты;
до включения учитывайте персональные данные. Ключи конфигурации не включаются
в события, секретные поля metadata редактируются. Не передавайте секреты внутри
произвольных строк metadata: автоматическая маскировка не заменяет контроль данных.
Ошибка записи не ломает ответ LLM: есть предупреждение и `tracker.write_failures`.

### Цены и статистика

`config/prices.json` намеренно пуст: тарифы проекта не подтверждены.
`prices.example.json` показывает формат, **не реальные цены**.
Ключ тарифа — `provider:model`, например `hub:qwen-model-name`.
Валюта и ставки за миллион токенов задаются строками, расчёт — `Decimal`.
Конфиг перечитывается при записи каждого события, снимок использованных ставок
сохраняется в нём. Прошлые события не переоцениваются автоматически.

Неизвестная модель → `UNKNOWN_MODEL`, нет usage → `UNKNOWN_USAGE`,
сломанный/отсутствующий конфиг → `PRICE_CONFIG_ERROR`; сумма в этих случаях
`null`, а не фиктивный ноль. Валюты суммируются раздельно, без выдуманного курса.

```python
stats = await tracker.stats(user_id="123", skill_context="MATRIX_CHECK")
events = await tracker.events(bot_id="organizer", kind="llm")
await tracker.export_jsonl("data/export-new.jsonl", bot_id="organizer")
```

Экспорт не перезаписывает существующий файл. CLI `stats`/`export` поддерживает
`--user`, `--skill`, `--bot`, `--platform`, `--kind` (по умолчанию `llm`).

## A4. Telegram и VK

`TelegramAdapter` использует aiogram, `VKAdapter` — vkbottle. Навыки получают
`IncomingMessage`, а не SDK-объекты. Общие операции:

- `on_command`, `on_message`, `on_callback`: одна общая маршрутизация;
- `send`: разбивка plain text (4000 Telegram / 4096 VK), возвращает список ID;
- `send_status`, `start_typing` с остановкой через async context manager;
- `send_attachment`, `edit`, `delete`, `answer_callback`, inline-кнопки `Button`;
- `Attachment.read_bytes()`: ограниченная по размеру загрузка, без извлечения текста;
- `run` / `aclose`: polling и управляемое завершение фоновых задач.

Команда не попадает повторно в общий обработчик. Telegram-команды для другого
бота игнорируются после определения своего username. Вложенные данные исходного
сообщения остаются доступны через `message.raw`. Нет скрытой транскрипции аудио
или парсинга DOCX/PDF — это задачи A5 или существующих функций бота.

Полный API не ограничен списком переносимых методов:

```python
from botkit.transport.telegram import TelegramAdapter
from botkit.transport.vk import VKAdapter

# При наличии настроенных экземпляров:
await telegram.call_api("get_chat", chat_id=chat_id)  # имена методов aiogram
await vk.call_api("users.get", user_ids=user_id)    # имена VK API
# SDK: telegram.native, telegram.dispatcher; vk.native, vk.api, vk.labeler
```

`call_api` включает журналирование и ограниченный retry при явном rate limit.
Вызовы SDK напрямую доступны для специфичных функций, но обходят эту обёртку
и её учёт. Новые типы событий/webhooks подключаются средствами SDK;
переносимый интерфейс не обещает одинаковую семантику всех функций платформ.
Форматированный Telegram-текст (entities/HTML) отправляйте через `call_api`,
так как автоматическая разбивка `send` предназначена для plain text.
В переносимом VK API файлы audio/video отправляются как документы; для
проигрываемых медиа используйте соответствующие SDK-uploaders и права API.

См. `examples/custom_bot.py`: один обработчик запускается с Telegram или VK.
Новая платформа реализует контракт из `transport/base.py` (для удобства —
наследник `BaseAdapter`); существующие навыки при этом не меняются.
