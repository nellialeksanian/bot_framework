"""
Демонстрация botkit.attempts (B2) С РЕАЛЬНЫМ LLM (302.ai) — вызывает
настоящие функции фреймворка (record_revision, ActiveSessionResolver,
LLMRevisionClassifier), реальный сетевой вызов к 302.ai, реальный SQLite
на диске.

Переиспользует model_loading.py из gb_edu_bot (не заводит свой LLM-клиент)
— требует настроенный gb_edu_bot/.env (API_302AI_KEY и т.д.), тот же, что
использует сам бот. Делает несколько реальных запросов к LLM.

Требует venv, где установлен botkit (editable) и есть доступ к gb_edu_bot,
например:
    /Users/nellyaleksanyan/Desktop/hobsbawm2.0/venv/bin/python3

Запуск:
    python3 try_attempt_store_real_llm.py
"""

import asyncio
import os
import sys

sys.path.insert(0, "/Users/nellyaleksanyan/Desktop/gb_edu_bot/app")

from botkit.attempts.base import record_revision
from botkit.attempts.sessions import ActiveSessionResolver, LLMRevisionClassifier
from botkit.attempts.sqlite_store import SQLiteAttemptStore

DB_PATH = os.path.join(os.path.dirname(__file__), "demo_attempts_real_llm.sqlite3")


def section(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


async def main() -> None:
    from model_loading import load_302_llm

    llm = load_302_llm()
    if llm is None:
        print("A302_API_KEY не настроен в gb_edu_bot/.env — не могу продолжить.")
        return
    print(f"Реальный LLM загружен: {llm.model_name}")

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    store = SQLiteAttemptStore(DB_PATH)

    classifier = LLMRevisionClassifier(llm)
    resolver = ActiveSessionResolver(store, classifier=classifier)

    actor_id = "42"
    skill = "feedback"

    # ------------------------------------------------------------------
    section("1. Первое сообщение вообще — вопрос не задаётся (продолжать нечего)")
    # ------------------------------------------------------------------
    result = await resolver.resolve(actor_id, skill, "Ушинский пишет о народности как основе воспитания.")
    print("task_ref:", result.task_ref)
    print("question_kind:", result.question_kind)
    task_ref_1 = result.task_ref
    attempt_v1 = await record_revision(store, actor_id, task_ref_1, "Ушинский пишет о народности как основе воспитания.")
    print("Attempt v1 записан:", attempt_v1.attempt_id[:8], "parent:", attempt_v1.parent_attempt_id)

    # ------------------------------------------------------------------
    section("2. Похожий текст (правка того же черновика) — реальный LLM-запрос")
    # ------------------------------------------------------------------
    similar_text = (
        "Ушинский пишет о народности как основе воспитания, "
        "и я хочу добавить, что он связывает это с ролью родного языка в обучении."
    )
    print("Отправляю в LLM запрос на классификацию... (может занять 5-15 секунд)")
    result2 = await resolver.resolve(actor_id, skill, similar_text)
    print("task_ref:", result2.task_ref)
    print("question_kind:", result2.question_kind)
    if result2.task_ref == task_ref_1:
        print(">>> LLM решил: это ПРАВКА того же черновика (task_ref не изменился)")
        attempt_v2 = await record_revision(store, actor_id, result2.task_ref, similar_text)
        print("Attempt v2 записан:", attempt_v2.attempt_id[:8], "parent:", attempt_v2.parent_attempt_id)
    else:
        print(">>> LLM не был уверен / решил, что это другая работа — задан вопрос студенту")

    # ------------------------------------------------------------------
    section("3. Совсем другая тема — реальный LLM-запрос")
    # ------------------------------------------------------------------
    unrelated_text = "Расскажи мне про рецепт борща с говядиной, сколько варить свёклу."
    print("Отправляю в LLM запрос на классификацию... (может занять 5-15 секунд)")
    result3 = await resolver.resolve(actor_id, skill, unrelated_text)
    print("task_ref:", result3.task_ref)
    print("question_kind:", result3.question_kind)
    if result3.task_ref is None:
        print(">>> LLM решил: это ДРУГАЯ работа (или не уверен) — бот должен задать вопрос:")
        if result3.question_kind == "ask_continue_or_new":
            print('    "У вас уже есть работа в процессе... Продолжаем (да) или новая (новый)?"')
        # студент отвечает "новый"
        result3b = await resolver.resolve(actor_id, skill, "новый")
        print("После ответа 'новый' -> task_ref:", result3b.task_ref, "(!= task_ref_1:", result3b.task_ref != task_ref_1, ")")
        attempt_new = await record_revision(store, actor_id, result3b.task_ref, unrelated_text)
        print("Attempt в новой цепочке:", attempt_new.attempt_id[:8], "parent:", attempt_new.parent_attempt_id)
    else:
        print(">>> LLM почему-то решил, что это тоже правка (неожиданно для этого примера)")

    # ------------------------------------------------------------------
    section("4. Проверяем, что старая цепочка (Ушинский) не пострадала")
    # ------------------------------------------------------------------
    latest_old = await store.latest(actor_id, task_ref_1)
    lineage_old = await store.get_lineage(latest_old.attempt_id)
    print(f"Цепочка '{task_ref_1}': {len(lineage_old)} попыток")
    for a in lineage_old:
        print("  -", a.content[:60])

    print(f"\nБД сохранена в: {DB_PATH}")

    if hasattr(llm, "aclose"):
        await llm.aclose()


if __name__ == "__main__":
    asyncio.run(main())
