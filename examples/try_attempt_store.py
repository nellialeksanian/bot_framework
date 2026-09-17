"""
Интерактивная демонстрация botkit.attempts (B2) — вызывает РЕАЛЬНЫЕ функции
фреймворка (record_revision, attempt_count, get_lineage, record_response),
без переписывания логики. Создаёт настоящий SQLite-файл на диске, который
можно открыть в DB Browser for SQLite или через `sqlite3` в терминале и
посмотреть таблицы своими глазами.

Требует venv, где установлен botkit (editable), например:
    /Users/nellyaleksanyan/Desktop/hobsbawm2.0/venv/bin/python3

Запуск:
    python3 try_attempt_store.py
"""

import asyncio
import os

from botkit.attempts.base import attempt_count, record_revision
from botkit.attempts.sqlite_store import DuplicateResponseError, SQLiteAttemptStore

DB_PATH = os.path.join(os.path.dirname(__file__), "demo_attempts.sqlite3")


def section(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


async def main() -> None:
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)  # чистый старт при каждом запуске демо

    store = SQLiteAttemptStore(DB_PATH)
    print(f"База создана по пути: {DB_PATH}")

    # ------------------------------------------------------------------
    section("1. Первая попытка студента (feedback, actor_id='42')")
    # ------------------------------------------------------------------
    v1 = await record_revision(store, actor_id="42", task_ref="feedback", content="Черновик v1: Ушинский пишет о народности.")
    print("Attempt v1:", v1)
    print("parent_attempt_id:", v1.parent_attempt_id, "(None — это корень цепочки)")

    # ------------------------------------------------------------------
    section("2. Бот отвечает на эту попытку")
    # ------------------------------------------------------------------
    bot_reply_1 = await store.record_response(
        v1.attempt_id,
        "Обратите внимание: вы не связали цель воспитания со средством.",
        skill_context="FEEDBACK",
        support_policy_ref="feedback_v1",
    )
    print("BotResponse:", bot_reply_1)

    # ------------------------------------------------------------------
    section("3. Попытка второй раз записать ответ на тот же attempt -> ошибка")
    # ------------------------------------------------------------------
    try:
        await store.record_response(v1.attempt_id, "второй ответ на ту же попытку")
        print("ОШИБКА: не должно было пройти без исключения!")
    except DuplicateResponseError as e:
        print("Получили DuplicateResponseError, как и задокументировано:", e)

    # ------------------------------------------------------------------
    section("4. Студент правит черновик (v2) — та же (actor_id, task_ref)")
    # ------------------------------------------------------------------
    v2 = await record_revision(store, actor_id="42", task_ref="feedback", content="Черновик v2: связал цель со средством — родным языком в обучении.")
    print("Attempt v2:", v2)
    print("parent_attempt_id v2:", v2.parent_attempt_id, "== v1.attempt_id?", v2.parent_attempt_id == v1.attempt_id)

    await store.record_response(v2.attempt_id, "Хорошо, теперь связь видна явно.", skill_context="FEEDBACK")

    # ------------------------------------------------------------------
    section("5. Восстанавливаем всю цепочку ревизий через get_lineage()")
    # ------------------------------------------------------------------
    lineage = await store.get_lineage(v2.attempt_id)
    for i, a in enumerate(lineage, start=1):
        print(f"  v{i}: {a.content!r} (attempt_id={a.attempt_id[:8]}...)")

    # ------------------------------------------------------------------
    section("6. attempt_count() — сколько попыток уже сделано")
    # ------------------------------------------------------------------
    count = await attempt_count(store, actor_id="42", task_ref="feedback")
    print("attempt_count:", count)

    # ------------------------------------------------------------------
    section("7. Другой студент (actor_id='99') с тем же task_ref — не пересекается")
    # ------------------------------------------------------------------
    other = await record_revision(store, actor_id="99", task_ref="feedback", content="Черновик студента 99")
    print("Attempt другого студента:", other)
    print("parent_attempt_id:", other.parent_attempt_id, "(None — своя отдельная цепочка)")

    latest_42 = await store.latest("42", "feedback")
    latest_99 = await store.latest("99", "feedback")
    print("latest('42', 'feedback'):", latest_42.content)
    print("latest('99', 'feedback'):", latest_99.content)

    # ------------------------------------------------------------------
    section("8. Тот же студент, другой task_ref (другая работа) — тоже не пересекается")
    # ------------------------------------------------------------------
    other_work = await record_revision(store, actor_id="42", task_ref="text_analysis::abc123", content="Вопрос по другому тексту")
    print("Attempt в другой работе того же студента:", other_work)
    print("parent_attempt_id:", other_work.parent_attempt_id, "(None — не связано с feedback-цепочкой)")

    print(f"\nГотово. Открой файл {DB_PATH} в DB Browser for SQLite,")
    print("чтобы увидеть таблицы attempts/bot_responses своими глазами:")
    print(f"  sqlite3 \"{DB_PATH}\" \".tables\"")
    print(f"  sqlite3 \"{DB_PATH}\" \"SELECT * FROM attempts;\"")
    print(f"  sqlite3 \"{DB_PATH}\" \"SELECT * FROM bot_responses;\"")


if __name__ == "__main__":
    asyncio.run(main())
