# Подключение существующих ботов

## Источники и границы

Реализация находится в структуре итогового репозитория, не в отдельном пакете:

- `nellialeksanian/bot_framework`, исходный HEAD `616b25a71090d4c4e4ce51bbda35cb3d37d1c9e5`;
- локальный `organizer_bot2/organizer_bot`: хаб, Qwen, резерв, изображения и RACI;
- `nellialeksanian/reflector_kolb`, HEAD `a763f222b842a649a41aadbc41ce2a2ebd72cd16`;
- `nellialeksanian/customs_bot`, HEAD `66ff104cd9ac4a435b43d67cd1f1d6d2369e4805`.

Исходные боты не переписаны и не запущены в боевых чатах. Их prompts, RAG,
состояния диалога и педагогические правила не становятся частью платформенного
слоя. Пакет устанавливается в окружение выбранного бота через `pip install -e /path/to/bot_framework_repo`.

## Замена model_loading

Новый API возвращает объект `LLMResponse`. Новый код навыков использует
`result.response` для пользовательского ответа и `result.raw` для JSON с метаданными.
Для постепенного переноса старых навыков предусмотрен строковый мост:

```python
from botkit.llm import LegacyStringLLM, load_llm

gateway = load_llm(tracker=tracker)
text_llm = LegacyStringLLM(gateway)
json_llm = LegacyStringLLM(gateway, raw=True)
```

| Старый потребитель | Какой мост | Причина |
|---|---|---|
| `customs_bot.intent_recognition` | `text_llm` | Ожидает извлечённую строку `REFERENCE` |
| `reflector_kolb.p1_goals` и навыки со структурированным ответом | `json_llm` | Нужны `response`, `layer_complete`, `layer_data` вместе |
| `organizer_bot2.check_matrix` | `json_llm` | Нужен полный JSON проверки матрицы |
| Извлечение grid из изображения organizer | `ainvoke_with_image_b64` / полный ответ | Нельзя потерять поле `grid` |

Это существенное различие: извлечение только `response` перед JSON-парсером
рефлексии теряет управляющие поля. Не заменяйте все старые модели одним мостом
без проверки контракта навыка.

Старые `LOCAL_HUB_*` и `A302_*` имена поддерживаются. Ключи берутся из окружения,
не копируются в библиотеку. `load_dotenv` вызывается точкой входа бота.
`max_tokens=None` сохраняет старое значение «не указывать ограничение».

## Два уровня fallback

Предпочтительный новый путь — одна фабричная цепочка и `validator=` для
предметной проверки ответа. Сетевой fallback для Qwen уже внутри неё.

Если старая функция сама вызывает модель, парсит результат и только потом
выбрасывает исключение, можно временно сохранить её внешнюю обёртку:

```python
from botkit.llm import LegacyStringLLM, call_with_fallback, load_llm

primary_gateway = load_llm("hub", fallback=False)
backup_gateway = load_llm("302ai", fallback=False)
primary = LegacyStringLLM(primary_gateway, raw=True)
backup = LegacyStringLLM(backup_gateway, raw=True)

result = await call_with_fallback(
    primary,
    lambda model: original_parse_and_validate(model),
    backup,
    label="matrix_vision",
    no_fallback_for=(NotATableError,),  # класс исключения из исходного бота
)
```

Здесь `original_parse_and_validate` и `NotATableError` — существующая доменная
функция и её исключение, не объекты botkit. Закройте оба gateway при завершении.
Обёртка совместимости, как старая, ловит широкий `Exception`; не используйте её
вместо более строгого нового API без необходимости. Отмена задачи не ловится.
Не включайте одновременно вложенные цепочки и внешний повтор без контроля:
это создаёт лишние оплаченные обращения.

## Журнал и транспорт

Передавайте один `SQLiteUsageTracker` в gateway и адаптеры. Внутри навыка задавайте
только `usage_context(skill_context="...")`; платформа/user/chat уже берутся
из контекста обработчика. Не логируйте тот же ответ повторно старым счётчиком.
Цены перенесите в `config/prices.json` по фактическим тарифам, отдельно для хаба
и прямого 302.ai. Старые журналы библиотека автоматически не мигрирует.

Переносите обработчики из `bot.py` и `bot_vk.py` в одну функцию с аргументом
`IncomingMessage`. Изменяется транспорт, но не промпты и правила навыка.
Вложения теперь отдают bytes; передавайте их существующим извлекателям.
Запускайте один polling-процесс на токен, чтобы не конкурировать с боевым ботом.

Порядок приёмки: unit/integration → прямые вызовы навыков на fixtures →
выделенные чаты обеих платформ → полные пользовательские сценарии →
отдельная ветка и review. Текущий статус — в `Platform validation.md`.
