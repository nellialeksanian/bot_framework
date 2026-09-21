# B5. Evidence & Citation Layer — SC04 + SC05
# Источник: "Модули фреймворка — приоритет.md", раздел B5; инвариант 5.7
# "Grounding: citation не равна evidence" карты v2.2 — retrieval score и
# entailment state обязаны быть разными объектами, "источники расходятся"
# и "в корпусе нет основания" (UNKNOWN) — легитимные ответы, не ошибки.
#
# SC04 (Authorized Corpus / RAG) сам корпус и retrieval — это A3
# (botkit.rag), не B5. B5 добавляет отдельный шаг ПОСЛЕ retrieval: проверяет,
# что найденный chunk действительно поддерживает claim, а не просто оказался
# рядом по семантической близости — и хранит эту проверку (SC05, Source +
# Evidence Graph) как append-only лог, а не только возвращает её один раз.

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

Entailment = Literal["SUPPORTED", "PARTIAL", "CONFLICTING", "UNKNOWN"]


@dataclass
class EvidenceLink:
    evidence_link_id: str
    claim: str
    chunk_ref: str
    source: str
    # source и chunk_ref дублируют то, что уже есть в botkit.rag.base.Chunk
    # (chunk.source), но EvidenceLink не держит ссылку на сам Chunk — тот
    # живёт в VectorStore (A3), переживать рестарт процесса обязан только
    # сам факт проверки, не содержимое чанка.
    entailment: Entailment
    created_at: str  # ISO 8601 UTC — как AttemptVersion.created_at в B2


class EvidenceChecker(Protocol):
    async def verify(self, claim: str, chunk) -> EvidenceLink:
        """chunk: botkit.rag.base.Chunk. Отдельный (более дешёвый/быстрый)
        LLM-вызов или эвристика, ДО того как claim попадёт в финальный
        ответ. Не пишет в EvidenceStore сама — см. verify_and_record()."""
        ...


class EvidenceStore(Protocol):
    async def record(self, link: EvidenceLink) -> EvidenceLink:
        # инвариант: append-only, как Attempt в B2 и RaterRecord в B3 —
        # никогда не перезаписывает существующую EvidenceLink, только
        # добавляет новую. Повторная проверка того же claim/chunk_ref
        # (например, после обновления корпуса) — отдельная запись, не апдейт.
        ...

    async def get_for_claim(self, claim: str) -> list[EvidenceLink]:
        ...

    async def get_conflicting(self, claim: str) -> list[EvidenceLink]:
        # инвариант 5.7: система должна уметь явно сказать "источники
        # расходятся" — это отдельный, часто более важный запрос, чем
        # get_for_claim() целиком (тот включает и SUPPORTED, и UNKNOWN).
        ...


async def verify_and_record(checker: EvidenceChecker, store: EvidenceStore, claim: str, chunk) -> EvidenceLink:
    """Связывает EvidenceChecker (проверка) и EvidenceStore (лог) в один
    шаг — обычный порядок вызова в навыке: проверить chunk ДО того, как
    claim попадёт в ответ, и сразу записать результат, а не только вернуть
    его один раз и потерять при следующем сообщении. Тонкая функция, а не
    метод на сторе или чекере — оба остаются независимыми Protocol,
    подставимыми по отдельности (см. sync_package() в B3 для того же приёма)."""
    link = await checker.verify(claim, chunk)
    return await store.record(link)
