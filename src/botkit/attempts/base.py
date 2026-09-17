# B2. Attempt & Revision Store — SC07
# Источник: "Модули фреймворка — приоритет.md", раздел B2.
# Персистентная история попыток пользователя, неизменяемость исходной версии,
# связь parent -> child между ревизиями.

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol


@dataclass
class Attempt:
    attempt_id: str
    actor_id: str
    task_ref: str
    content: str
    content_hash: str
    created_at: datetime
    origin: Literal["HUMAN"]
    parent_attempt_id: str | None


@dataclass
class BotResponse:
    response_id: str
    attempt_id: str
    content: str
    created_at: datetime
    skill_context: str | None
    support_policy_ref: str | None
    llm_response_ref: str | None  # ссылка на UsageEvent/LLMResponse (A2) — не дублирует usage/cost здесь


class AttemptStore(Protocol):
    async def record(
        self, actor_id: str, task_ref: str, content: str, parent_id: str | None
    ) -> Attempt:
        ...

    async def get_lineage(self, attempt_id: str) -> list[Attempt]:
        ...

    async def latest(self, actor_id: str, task_ref: str) -> Attempt | None:
        ...

    async def record_response(
        self,
        attempt_id: str,
        content: str,
        *,
        skill_context: str | None = None,
        support_policy_ref: str | None = None,
        llm_response_ref: str | None = None,
    ) -> BotResponse:
        # инвариант: строго 1:1 с Attempt — повторный вызов на тот же attempt_id
        # обязан бросить ошибку (напр. DuplicateResponseError), а не перезаписать
        # существующий BotResponse и не завести второй. Ретраи LLM (A1) разрешаются
        # внутри генерации ответа, до этого вызова, а не через несколько BotResponse.
        ...

    async def get_response(self, attempt_id: str) -> BotResponse | None:
        ...


async def record_revision(store: AttemptStore, actor_id: str, task_ref: str, content: str) -> Attempt:
    """Записывает новую попытку, сама находя parent_attempt_id как текущий
    latest() для (actor_id, task_ref) — навык не обязан вручную вызывать
    latest() перед каждым record(), чтобы получить цепочку ревизий одной
    работы. Свободная функция поверх AttemptStore (как search_across() над
    VectorStore в A3), а не метод Protocol — правило склейки в родителя
    одинаково для любой реализации хранилища, переопределять его в каждом
    конкретном сторе не нужно.

    task_ref обязан однозначно определять "одну и ту же работу" — если
    вызывающий код (навык) не различает правку прежней работы от новой темы,
    это не решается здесь: он должен передать разные task_ref для разных тем.

    Для ботов, где task_ref всегда один и тот же и смена работы недопустима
    (экзамен/тест с фиксированным числом попыток), ActiveSessionResolver
    (sessions.py) не подключается вовсе — навык сам передаёт свой
    единственный, заранее известный task_ref в record_revision() напрямую.
    Ограничение числа попыток — см. attempt_count() ниже, вызывается ДО
    record_revision(), не после.
    """
    parent = await store.latest(actor_id, task_ref)
    parent_id = parent.attempt_id if parent is not None else None
    return await store.record(actor_id, task_ref, content, parent_id)


async def attempt_count(store: AttemptStore, actor_id: str, task_ref: str) -> int:
    """Сколько попыток уже сделано в этой (actor_id, task_ref) — т.е. длина
    цепочки ревизий. Для ботов с ограниченным числом попыток (экзамен/тест)
    вызывается ДО record_revision(), чтобы навык мог отказать в записи новой
    попытки, а не считать после факта — превышение лимита не должно молча
    создавать (N+1)-ю попытку, а не должно случаться вовсе.

    Пример использования в навыке:
        if await attempt_count(store, actor_id, task_ref) >= MAX_ATTEMPTS:
            return "Лимит попыток исчерпан"
        attempt = await record_revision(store, actor_id, task_ref, content)
    """
    latest_attempt = await store.latest(actor_id, task_ref)
    if latest_attempt is None:
        return 0
    return len(await store.get_lineage(latest_attempt.attempt_id))
