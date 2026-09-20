"""
Демонстрация C1 Simulation & Scenario Engine (SC03) С РЕАЛЬНЫМ LLM (302.ai) —
LLMScenarioGenerator генерирует контролируемый набор дефектов в домене
FD42-006 "Гельминтолог-детектив" (ветеринарная паразитология, Fasciola
hepatica) — тот самый первоисточник, на который опирается схема DefectSet.

Проверяет весь SM09 цикл: DEFECT_SET_GENERATED (draft) -> DEFECT_SET_GUARDED
(validated -> released) -> STUDENT_DIAGNOSIS (record_diagnosis), и явно
смотрит, вставлен ли сам "испорченный" учебный текст куда-то в результат —
LLMScenarioGenerator.generate_defect_set() парсит из ответа LLM только
структурированный список {defect_type, locator, description}, не сам текст
с ошибками, который R0301 FD42-006 требует показать студенту как объект
анализа. Это демо явно проверяет, не потерялся ли текст.

Переиспользует model_loading.py из gb_edu_bot (не заводит свой LLM-клиент).
Не встраивается в живого VK-бота — SC03 не диалоговый навык, для этого
теста достаточно прямого вызова.

Требует venv, где установлен botkit (editable) и есть доступ к gb_edu_bot:
    /Users/nellyaleksanyan/Desktop/hobsbawm2.0/venv/bin/python3

Запуск:
    python3 try_scenario_generator_real_llm.py
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, "/Users/nellyaleksanyan/Desktop/gb_edu_bot/app")

from botkit.simulation.scenario import DefectGenerationParseError, LLMScenarioGenerator
from botkit.simulation.sqlite_store import (
    DefectCountMismatchError,
    GuardNotReleasedError,
    SQLiteScenarioGenerator,
)

DB_PATH = os.path.join(os.path.dirname(__file__), "demo_scenario_real_llm.sqlite3")

SOURCE_STANDARD_PROMPT = """
Ты — эксперт по ветеринарной паразитологии, составляющий учебный материал
для студентов ветеринарного факультета по теме "жизненный цикл Fasciola
hepatica (печёночный сосальщик/двуустка)".

Эталонный жизненный цикл (source standard):
1. Яйца фасциолы выделяются с фекалиями окончательного хозяина (крупный
   рогатый скот, овцы) в воду.
2. Из яйца выходит мирацидий, который активно внедряется в промежуточного
   хозяина — пресноводного моллюска (малый прудовик, Galba truncatula).
3. Внутри моллюска происходит развитие: мирацидий -> спороциста -> редии ->
   церкарии.
4. Церкарии покидают моллюска, инцистируются на водной растительности,
   превращаясь в адолескарии.
5. Окончательный хозяин заражается алиментарно — поедая траву с
   адолескариями.
6. В кишечнике хозяина адолескарии эксцистируются, мигрируют через стенку
   кишечника и брюшину в печень, где созревают во взрослых мариты в жёлчных
   протоках.
""".strip()


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

    store = SQLiteScenarioGenerator(DB_PATH)
    generator = LLMScenarioGenerator(
        llm, store, SOURCE_STANDARD_PROMPT, model_config_version=llm.model_name,
    )

    # ------------------------------------------------------------------
    section("1. generate_defect_set() — R0201 FD42-006: ровно 4 намеренных ошибки")
    # ------------------------------------------------------------------
    try:
        defect_set = await generator.generate_defect_set(
            task_ref="fasciola-1",
            source_standard_ref="fasciola_lifecycle_v1",
            intended_defect_count=4,
        )
    except DefectGenerationParseError as exc:
        print(f"ОШИБКА ПАРСИНГА: {exc}")
        return
    except DefectCountMismatchError as exc:
        print(f"НЕСОВПАДЕНИЕ ЧИСЛА ДЕФЕКТОВ: {exc}")
        return

    print(f"defect_set_id: {defect_set.defect_set_id}")
    print(f"guard_status: {defect_set.guard_status}")
    print(f"intended_defect_count: {defect_set.intended_defect_count}")
    print(f"фактическое число дефектов: {len(defect_set.defects)}")
    print("\nstimulus_text (то, что реально увидит студент):")
    print(defect_set.stimulus_text)
    print("\nСтруктурированный список defects[] (ключ ответа, студенту не показывается):")
    print(json.dumps(defect_set.defects, ensure_ascii=False, indent=2))

    # ------------------------------------------------------------------
    section("2. Проверка: каждый locator реально встречается в stimulus_text?")
    # ------------------------------------------------------------------
    for i, d in enumerate(defect_set.defects):
        locator = d.get("locator", "")
        found = locator in defect_set.stimulus_text
        print(f"  defect-{i} locator={locator!r} -> найден в тексте буквально: {found}")

    # ------------------------------------------------------------------
    section("3. Guard-цикл — validate() -> release()")
    # ------------------------------------------------------------------
    try:
        await store.record_diagnosis(defect_set.defect_set_id, "student-1", [])
    except GuardNotReleasedError as exc:
        print(f"Ожидаемо отклонено (draft, не released): {exc}")

    validated = await store.validate_defect_set(defect_set.defect_set_id)
    print(f"После validate_defect_set(): guard_status={validated.guard_status}")

    released = await store.release_defect_set(defect_set.defect_set_id)
    print(f"После release_defect_set(): guard_status={released.guard_status}")

    # ------------------------------------------------------------------
    section("4. record_diagnosis() — студент называет найденные дефекты")
    # ------------------------------------------------------------------
    real_ids = [d["defect_id"] for d in defect_set.defects]
    print(f"Реальные defect_id в этом наборе: {real_ids}")

    # Студент нашёл первую и третью ошибку правильно, придумал несуществующую
    # "defect-99", и не нашёл оставшиеся две — типичный частичный результат.
    student_found = [real_ids[0], real_ids[2], "defect-99"]
    diagnosis = await generator.record_diagnosis(
        defect_set_ref=defect_set.defect_set_id,
        actor_id="student-1",
        found_items=student_found,
        correction_prompt_ref=None,
    )
    print(f"diagnosis_id: {diagnosis.diagnosis_id}")
    print(f"found_items (что назвал студент): {diagnosis.found_items}")

    # ------------------------------------------------------------------
    section("5. check_diagnosis() — сверка найденного с реальным ключом")
    # ------------------------------------------------------------------
    score = await generator.check_diagnosis(diagnosis.diagnosis_id)
    print(f"correct_defect_ids: {score.correct_defect_ids}")
    print(f"missed_defect_ids: {score.missed_defect_ids}")
    print(f"false_positive_items: {score.false_positive_items}")
    print(f"\nИтог: {len(score.correct_defect_ids)}/{defect_set.intended_defect_count} верно найдено")

    print(f"\nБД сохранена в: {DB_PATH}")

    if hasattr(llm, "aclose"):
        await llm.aclose()


if __name__ == "__main__":
    asyncio.run(main())
