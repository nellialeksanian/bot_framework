# C1. Simulation & Scenario Engine — SC02 — "продолжение диалога с персоной"
# поверх PersonaSimulator, зеркало botkit.attempts.sessions.ActiveSessionResolver
# для B2.
#
# Закрывает тот же разрыв, что ActiveSessionResolver закрывает для B2: без
# общего решения вопрос "это продолжение уже идущего stateful-диалога, или
# нужно звать B1 route()?" пришлось бы копировать в bot_vk.py каждого бота
# почти дословно (как это и было сделано при первом подключении — см.
# oral_defense в gb_edu_bot/app/bot_vk.py, git-история до выноса сюда).
#
# Что вынесено сюда (доменно-нейтрально, одинаково для любого бота с C1):
#   - дешёвая проверка "есть ли активный run для этого actor_id" ДО route();
#   - разбор явной команды выхода из роли (без неё status="started" никогда
#     не меняется сам — студент навсегда застревал бы в одном навыке);
#   - вызов respond() на продолжении.
#
# Что НЕ вынесено (остаётся у вызывающего бота/навыка):
#   - какой именно task_ref использовать для конкретного навыка — вызывающий
#     код передаёт его явно, как и в B2 (см. task_ref в ActiveSessionResolver);
#   - что показать студенту при выходе/продолжении — деловой текст ответа
#     решает бот, эта функция отдаёт только структурированный результат;
#   - сам LLM-клиент/промпт персоны — это PersonaSimulator, передаётся
#     вызывающим кодом, не создаётся здесь.

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from botkit.simulation.base import PersonaSimulator

DEFAULT_EXIT_PHRASES: frozenset[str] = frozenset({
    "закончить", "завершить", "завершить защиту", "закончить защиту",
    "хватит", "стоп", "выйти", "выход", "/exit", "/stop",
})


@dataclass
class SimulationTurnResult:
    outcome: Literal["continued", "exited", "not_active"]
    response: str | None
    """Ответ персоны — заполнен только когда outcome == "continued"."""

    run_id: str | None
    """run_id вовлечённого SimulationRun — заполнен для "continued"/"exited",
    None для "not_active" (в этом случае активного run не было вовсе)."""


async def route_or_continue(
    simulator: PersonaSimulator,
    actor_id: str,
    task_ref: str,
    text: str,
    *,
    exit_phrases: frozenset[str] = DEFAULT_EXIT_PHRASES,
) -> SimulationTurnResult:
    """Зеркало ActiveSessionResolver.resolve() из B2, но для PersonaSimulator.

    Вызывающий код (бот) зовёт это ДО B1 route() — как is_awaiting_choice()
    для B2. Три исхода:

    - "not_active": для (actor_id, task_ref) нет активного run — вызывающий
      код должен звать обычный B1 route() сам, эта функция ничего не решает.
    - "exited": text совпал с exit_phrases (без учёта регистра/пробелов) —
      run завершён (complete()), response=None; бот сам решает текст
      прощального сообщения.
    - "continued": text ушёл в simulator.respond() того же run, response —
      готовый ответ персоны, который можно сразу отправлять студенту.
    """
    active_run = await simulator.get_active_run(actor_id, task_ref)
    if active_run is None:
        return SimulationTurnResult(outcome="not_active", response=None, run_id=None)

    if text.strip().lower() in exit_phrases:
        await simulator.complete(active_run.run_id)
        return SimulationTurnResult(outcome="exited", response=None, run_id=active_run.run_id)

    response = await simulator.respond(active_run.run_id, text)
    return SimulationTurnResult(outcome="continued", response=response, run_id=active_run.run_id)
