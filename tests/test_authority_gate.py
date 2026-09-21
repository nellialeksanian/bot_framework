from datetime import datetime, timezone

import pytest

from botkit.authority.base import DecisionEvent, Role
from botkit.authority.gate import DefaultAuthorityGate


class FakeIdentityGate:
    """No mocks — a real in-memory IdentityGate, same style as
    FakeAttemptStore/FakeIntentRouter in test_dialog_policy_engine.py."""

    def __init__(self, roles_by_user: dict[tuple[str, str], Role]):
        self._roles_by_user = roles_by_user

    async def resolve_role(self, platform_user_id: str, platform: str) -> Role:
        return self._roles_by_user.get((platform_user_id, platform), Role.STUDENT)


def _make_decision(candidate_ref="candidate-1", decision="ACCEPT") -> DecisionEvent:
    return DecisionEvent(
        decision_id="d1",
        candidate_ref=candidate_ref,
        decision_class="PEDAGOGICAL_JUDGMENT",
        actor_role=Role.TEACHER,
        decision=decision,
        evidence_refs=[],
        occurred_at=datetime.now(timezone.utc),
    )


async def test_require_role_true_when_identity_matches():
    identity = FakeIdentityGate({("123", "vk"): Role.TEACHER})
    gate = DefaultAuthorityGate(identity)

    allowed = await gate.require_role("123", "vk", Role.TEACHER)

    assert allowed is True


async def test_require_role_false_when_identity_does_not_match():
    identity = FakeIdentityGate({("123", "vk"): Role.STUDENT})
    gate = DefaultAuthorityGate(identity)

    allowed = await gate.require_role("123", "vk", Role.TEACHER)

    assert allowed is False


async def test_require_role_false_for_unknown_user_defaults_via_identity_gate():
    identity = FakeIdentityGate({})
    gate = DefaultAuthorityGate(identity)

    allowed = await gate.require_role("unknown", "vk", Role.TEACHER)

    assert allowed is False


async def test_record_decision_is_append_only():
    gate = DefaultAuthorityGate(FakeIdentityGate({}))
    first = _make_decision(decision="ACCEPT")
    second = _make_decision(decision="EDIT")

    await gate.record_decision(first)
    await gate.record_decision(second)

    recorded = await gate.get_decisions("candidate-1")
    assert recorded == [first, second]


async def test_get_decisions_scoped_by_candidate_ref():
    gate = DefaultAuthorityGate(FakeIdentityGate({}))
    await gate.record_decision(_make_decision(candidate_ref="candidate-1"))
    await gate.record_decision(_make_decision(candidate_ref="candidate-2"))

    recorded = await gate.get_decisions("candidate-1")

    assert len(recorded) == 1
    assert recorded[0].candidate_ref == "candidate-1"


async def test_absence_of_decision_is_not_accept():
    # Инвариант SC14: отсутствие DecisionEvent != ACCEPT — get_decisions()
    # на кандидата, для которого record_decision() ни разу не вызывался,
    # возвращает пустой список, а не что-то трактуемое как согласие.
    gate = DefaultAuthorityGate(FakeIdentityGate({}))

    recorded = await gate.get_decisions("never-decided")

    assert recorded == []
