# B2. Attempt & Revision Store — SC07 — "активная сессия работы" поверх AttemptStore.
#
# Закрывает разрыв, обнаруженный при подключении B2 к gb_edu_bot: одного
# record_revision() достаточно, ЕСЛИ вызывающий код уже знает правильный
# task_ref. Но "к какой работе относится ЭТО сообщение" — вопрос, который
# встаёт перед КАЖДЫМ навыком с версионированием одинаково: студент может
# либо продолжать прежнюю работу, либо начинать новую, никак явно об этом
# не сообщая. Без общего решения этот вопрос-ответный UX (bot_vk.py:
# resolve_document_session) пришлось бы копировать в каждый навык/бота
# почти дословно — тот же паттерн дублирования, что и у A1/A4 в приоритетном
# документе, только для B2.
#
# Первая версия этого модуля решала это только явным да/нет-вопросом при
# КАЖДОМ повторном сообщении — непрактично (студент печатал бы десятки
# сообщений одной правки, и каждое второе спрашивало бы "продолжаем?").
# Вторая версия пробовала эмбеддинги (косинусное сходство) — отброшена:
# семантическая близость текстов не то же самое, что "это правка или новая
# работа" (переписанный с нуля черновик той же темы даёт низкое сходство;
# два разных коротких черновика на близкую тему курса — обманчиво высокое),
# а порог similarity_threshold — цифра без реальной калибровки.
#
# Текущая версия: классификация через LLM (тот же LLMProvider из A1) — LLM
# видит оба текста целиком и должна решить "это правка предыдущей работы,
# или значимо другая работа?", а не мерить векторное расстояние. Это
# по-прежнему эвристика (LLM может ошибиться), но качественно точнее
# эмбеддингов — ей доступна семантика, а не только близость в векторном
# пространстве. Явный да/нет-вопрос студенту остаётся, но теперь как
# fallback на действительно неуверенный случай ("не уверена" от классификатора
# или отсутствие классификатора вовсе), а не основной путь каждого сообщения.
#
# Что вынесено сюда (доменно-нейтрально, одинаково для любого бота):
#   - где хранится "текущая активная работа" — в отдельной таблице
#     active_sessions внутри AttemptStore (get/set_active_session), а не
#     смешана с записями настоящих попыток студента и не в отдельной БД/файле;
#   - LLM-классификация "правка/новая работа" + state machine "ждём ответа"
#     как fallback, когда классификатор не уверен;
#   - разбор да/нет-ответа студента как чистая функция, без LLM.
#
# Что НЕ вынесено (остаётся в коде бота):
#   - текст вопроса и способ его отправки (message.answer, конкретный
#     мессенджер) — платформенная/языковая специфика, не часть B2;
#   - слова для да/нет на конкретном языке — параметр, а не константа;
#   - какой LLMProvider использовать — параметр, боты без классификатора
#     вовсе могут не передавать его (тогда resolver всегда спрашивает явно).

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Literal, Protocol

from botkit.attempts.base import AttemptStore


@dataclass
class SessionWords:
    continue_words: frozenset[str]
    new_words: frozenset[str]


DEFAULT_RU_SESSION_WORDS = SessionWords(
    continue_words=frozenset({"да", "продолжить", "продолжаем", "то же", "тот же", "старый", "прежний"}),
    new_words=frozenset({"нет", "новый", "новая", "другой", "другая", "новый документ", "новую тему"}),
)


def classify_session_answer(text: str, words: SessionWords) -> bool | None:
    """True = продолжить прежнюю работу, False = начать новую, None = ответ
    неоднозначен (вызывающий код должен переспросить, не гадать через LLM)."""
    answer = text.strip().lower()
    if answer in words.continue_words:
        return True
    if answer in words.new_words:
        return False
    return None


def new_task_ref(skill_name: str) -> str:
    # Один task_ref = одна работа в терминах B2 — непрозрачный идентификатор,
    # студент никогда не вводит и не видит его. Префикс skill_name — только
    # для читаемости сырых данных в БД, AttemptStore это не разбирает.
    return f"{skill_name}::{uuid.uuid4().hex[:12]}"


class RevisionClassifier(Protocol):
    """Решает, является ли new_text правкой/продолжением previous_text, или
    значимо другой, несвязанной работой. None = не уверен — вызывающий код
    (ActiveSessionResolver) в этом случае задаёт явный вопрос студенту,
    вместо того чтобы гадать."""

    async def is_revision(self, previous_text: str, new_text: str) -> bool | None:
        ...


class LLMRevisionClassifier:
    """RevisionClassifier поверх LLMProvider (A1) — не заводит отдельный
    LLM-клиент, переиспользует тот же провайдер, что и остальные навыки."""

    def __init__(self, llm) -> None:
        self._llm = llm

    async def is_revision(self, previous_text: str, new_text: str) -> bool | None:
        prompt = f"""
Ты помогаешь определить, относится ли новое сообщение студента к той же
работе, что и предыдущее, или это уже другая, несвязанная работа.

Предыдущий текст студента:
{previous_text}

Новый текст студента:
{new_text}

Это правка/продолжение ТОЙ ЖЕ работы (тот же документ, тот же черновик,
та же тема), или это уже ДРУГАЯ, несвязанная работа?

Ответь СТРОГО в формате JSON, без markdown:
{{"same_work": true}} — если это продолжение/правка той же работы
{{"same_work": false}} — если это другая, несвязанная работа
{{"same_work": null}} — если по тексту невозможно уверенно определить
""".strip()

        response = await self._llm.ainvoke(prompt)
        # LLMProvider.ainvoke() по контракту A1 возвращает LLMResponse, но
        # некоторые живые боты (см. _LegacyLLMAdapter в gb_edu_bot/skills.py)
        # пока оборачивают легаси-клиент, чьи .ainvoke() отдаёт str
        # напрямую — принимаем оба варианта, а не только строгий LLMResponse.
        if isinstance(response, str):
            raw = response
        else:
            raw = getattr(response, "response", None) or getattr(response, "raw", "") or ""
        match = re.search(r'"same_work"\s*:\s*(true|false|null)', raw, re.IGNORECASE)
        if match is None:
            return None  # не удалось распарсить — не гадаем, fallback на вопрос
        value = match.group(1).lower()
        if value == "true":
            return True
        if value == "false":
            return False
        return None


@dataclass
class SessionResolution:
    task_ref: str | None
    """task_ref для этого сообщения, либо None если нужно сначала спросить
    студента и дождаться ответа (сам вопрос ещё не отправлен — это должен
    сделать вызывающий код, используя question_kind ниже)."""

    question_kind: Literal["ask_continue_or_new", "reprompt_ambiguous"] | None
    """Только когда task_ref is None — какой именно вопрос нужен:
    "ask_continue_or_new" — первый вопрос в этой сессии ("продолжаем/новый");
    "reprompt_ambiguous" — предыдущий ответ студента был неоднозначным,
    нужно переспросить, а не задавать вопрос заново с нуля."""


class ActiveSessionResolver:
    """Отвечает на вопрос "к какому task_ref относится это сообщение?" для
    навыков, где одна работа может состоять из нескольких попыток (ревизий),
    и где новая, не связанная работа должна начинать отдельную цепочку, а не
    молча приклеиваться к последней как её ревизия.

    Основной путь — автоматический: если передан classifier, новый текст
    сравнивается с последней попыткой в активном task_ref через
    RevisionClassifier.is_revision(); True -> продолжаем без вопроса; False
    или None (не уверен) -> задаём явный да/нет-вопрос (fallback). Без
    classifier резолвер всегда переходит сразу к вопросу при отличном от
    первого сообщении — это грубое, но строго корректное поведение (полезно
    для ботов, где A1 LLMProvider ещё не подключён к B2 вовсе).

    Один экземпляр — на один бот-процесс (не на навык): внутреннее in-memory
    состояние ("кто сейчас ждёт да/нет") ключуется по (actor_id, skill_name)
    самостоятельно, так что один и тот же resolver можно передавать во все
    навыки с версионированием сразу, их сессии не пересекутся.

    In-memory состояние ожидания ответа НЕ переживает рестарт процесса — это
    осознанный компромисс: после рестарта студент, чей вопрос был задан, но
    не отвечен, просто получит вопрос заново при следующем сообщении,
    вместо потери прогресса. Сам выбор task_ref (после ответа или
    авто-продолжения) переживает рестарт, потому что хранится в
    AttemptStore, а не только в памяти.
    """

    def __init__(
        self,
        store: AttemptStore,
        words: SessionWords = DEFAULT_RU_SESSION_WORDS,
        classifier: RevisionClassifier | None = None,
    ) -> None:
        self._store = store
        self._words = words
        self._classifier = classifier
        self._awaiting_choice: set[tuple[str, str]] = set()
        # task_ref, предложенный на случай ответа "новый" — держим отдельно
        # от подтверждённого активного task_ref в AttemptStore, чтобы не
        # путать "предложенное" с "решённым".
        self._pending_new_ref: dict[tuple[str, str], str] = {}

    def is_awaiting_choice(self, actor_id: str, skill_name: str) -> bool:
        return (actor_id, skill_name) in self._awaiting_choice

    async def _active_task_ref(self, actor_id: str, skill_name: str) -> str | None:
        return await self._store.get_active_session(actor_id, skill_name)

    async def _is_revision(self, task_ref: str, actor_id: str, text: str) -> bool | None:
        if self._classifier is None:
            return None  # без классификатора нет автоматического решения — всегда fallback на вопрос
        previous = await self._store.latest(actor_id, task_ref)
        if previous is None:
            return None
        return await self._classifier.is_revision(previous.content, text)

    async def resolve(self, actor_id: str, skill_name: str, text: str) -> SessionResolution:
        """Возвращает SessionResolution: либо разрешённый task_ref (готово,
        можно вызывать навык), либо task_ref=None с question_kind — какой
        именно вопрос отправить студенту (сам вопрос отправляет вызывающий
        код, у него текст и способ отправки для конкретного мессенджера)."""
        key = (actor_id, skill_name)

        if key in self._awaiting_choice:
            decision = classify_session_answer(text, self._words)
            self._awaiting_choice.discard(key)
            pending_new_ref = self._pending_new_ref.pop(key, None)

            if decision is False:
                task_ref = pending_new_ref or new_task_ref(skill_name)
            elif decision is True:
                task_ref = await self._active_task_ref(actor_id, skill_name) or new_task_ref(skill_name)
            else:
                # неоднозначный ответ — переспрашиваем, не гадаем через LLM
                if pending_new_ref is not None:
                    self._pending_new_ref[key] = pending_new_ref
                self._awaiting_choice.add(key)
                return SessionResolution(task_ref=None, question_kind="reprompt_ambiguous")

            await self._store.set_active_session(actor_id, skill_name, task_ref)
            return SessionResolution(task_ref=task_ref, question_kind=None)

        active = await self._active_task_ref(actor_id, skill_name)
        if active is None:
            # первая работа этого actor_id в этом навыке вообще — вопрос не нужен
            task_ref = new_task_ref(skill_name)
            await self._store.set_active_session(actor_id, skill_name, task_ref)
            return SessionResolution(task_ref=task_ref, question_kind=None)

        is_revision = await self._is_revision(active, actor_id, text)
        if is_revision is True:
            return SessionResolution(task_ref=active, question_kind=None)  # продолжаем без вопроса

        # is_revision is False (уверенно другая работа) или None (не уверен/нет
        # классификатора) — в обоих случаях переспрашиваем явно, не рискуя
        # молча приклеить или молча разорвать реальную цепочку ревизий.
        self._pending_new_ref[key] = new_task_ref(skill_name)
        self._awaiting_choice.add(key)
        return SessionResolution(task_ref=None, question_kind="ask_continue_or_new")
