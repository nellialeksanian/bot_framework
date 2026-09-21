from datetime import datetime, timezone

import pytest

from botkit.attempts.base import Attempt
from botkit.authority.base import Role
from botkit.dialog_policy.base import FadingRule, Intent, SkillDescriptor, SupportPolicy
from botkit.dialog_policy.engine import (
    DefaultDialoguePolicyEngine,
    UnknownSkillError,
)

# Fake AttemptStore/IntentRouter — no mocks needed, these protocols are small
# enough that a real in-memory implementation is simpler than mocking.


class FakeAttemptStore:
    def __init__(self, attempts: list[Attempt] | None = None):
        self._attempts = attempts or []

    async def record(self, actor_id, task_ref, content, parent_id):
        raise NotImplementedError

    async def get_lineage(self, attempt_id):
        raise NotImplementedError

    async def latest(self, actor_id, task_ref):
        matching = [a for a in self._attempts if a.actor_id == actor_id and a.task_ref == task_ref]
        return matching[-1] if matching else None


class FakeAuthorityGate:
    def __init__(self, actual_role: Role):
        self._actual_role = actual_role

    async def require_role(self, platform_user_id, platform, required):
        return self._actual_role == required

    async def record_decision(self, event):
        raise NotImplementedError


class FakeIntentRouter:
    def __init__(self, intent: Intent):
        self._intent = intent
        self.last_available_skills: list[SkillDescriptor] | None = None

    async def classify(self, query, history, anchor, available_skills):
        self.last_available_skills = available_skills
        return self._intent


def _make_attempt(actor_id="student-1", task_ref="task-1") -> Attempt:
    return Attempt(
        attempt_id="a1",
        actor_id=actor_id,
        task_ref=task_ref,
        content="draft",
        content_hash="hash",
        created_at=datetime.now(timezone.utc),
        origin="HUMAN",
        parent_attempt_id=None,
    )


NO_GATE_POLICY = SupportPolicy(
    policy_id="position_clarification_v1",
    version=1,
    target_action="сформулировать собственный тезис",
    protected_difficulty="переход от смутной идеи к чёткой формулировке позиции",
    attempt_gate=False,
    allowed_action_classes=["reflect", "affirm", "question"],
    deny_patterns=["не формулируй тезис за студента"],
    fading_rule=None,
)

GATED_POLICY = SupportPolicy(
    policy_id="refine_argument_v1",
    version=1,
    target_action="развить собственную аргументацию",
    protected_difficulty="связать посылки в цепочку рассуждения",
    attempt_gate=True,
    allowed_action_classes=["question"],
    deny_patterns=["не переписывай аргумент за студента"],
    fading_rule=FadingRule(levels=["full_support", "reduced", "no_support"], turns_per_level=2),
)


@pytest.mark.asyncio
async def test_check_attempt_gate_is_noop_when_not_applicable():
    engine = DefaultDialoguePolicyEngine(intent_router=FakeIntentRouter(Intent("x", None, False)))
    engine.register_skill("position_clarification", handler=None, support_policy=NO_GATE_POLICY, description="help the student clarify a vague position")

    allowed = await engine.check_attempt_gate(
        "position_clarification", "student-1", "task-1", attempts=FakeAttemptStore()
    )

    assert allowed is True  # CP11A N/A — no attempt required, none exists


@pytest.mark.asyncio
async def test_check_attempt_gate_blocks_without_prior_attempt():
    engine = DefaultDialoguePolicyEngine(intent_router=FakeIntentRouter(Intent("x", None, False)))
    engine.register_skill("refine_argument", handler=None, support_policy=GATED_POLICY, description="help the student strengthen an existing argument")

    allowed = await engine.check_attempt_gate(
        "refine_argument", "student-1", "task-1", attempts=FakeAttemptStore()
    )

    assert allowed is False


@pytest.mark.asyncio
async def test_check_attempt_gate_allows_with_prior_attempt():
    engine = DefaultDialoguePolicyEngine(intent_router=FakeIntentRouter(Intent("x", None, False)))
    engine.register_skill("refine_argument", handler=None, support_policy=GATED_POLICY, description="help the student strengthen an existing argument")
    store = FakeAttemptStore([_make_attempt()])

    allowed = await engine.check_attempt_gate("refine_argument", "student-1", "task-1", attempts=store)

    assert allowed is True


@pytest.mark.asyncio
async def test_check_attempt_gate_scoped_by_actor_and_task():
    engine = DefaultDialoguePolicyEngine(intent_router=FakeIntentRouter(Intent("x", None, False)))
    engine.register_skill("refine_argument", handler=None, support_policy=GATED_POLICY, description="help the student strengthen an existing argument")
    store = FakeAttemptStore([_make_attempt(actor_id="student-2", task_ref="task-1")])

    allowed = await engine.check_attempt_gate("refine_argument", "student-1", "task-1", attempts=store)

    assert allowed is False  # attempt belongs to a different actor


@pytest.mark.asyncio
async def test_check_authority_gate_is_noop_when_no_required_role():
    engine = DefaultDialoguePolicyEngine(intent_router=FakeIntentRouter(Intent("x", None, False)))
    engine.register_skill("position_clarification", handler=None, support_policy=NO_GATE_POLICY, description="help the student clarify a vague position")

    allowed = await engine.check_authority_gate(
        "position_clarification", "student-1", "vk", authority=FakeAuthorityGate(Role.STUDENT)
    )

    assert allowed is True  # required_role=None (default) — открыт любой роли


@pytest.mark.asyncio
async def test_check_authority_gate_blocks_wrong_role():
    engine = DefaultDialoguePolicyEngine(intent_router=FakeIntentRouter(Intent("x", None, False)))
    engine.register_skill(
        "grade_release", handler=None, support_policy=NO_GATE_POLICY,
        description="release a grade to the student", required_role=Role.TEACHER,
    )

    allowed = await engine.check_authority_gate(
        "grade_release", "student-1", "vk", authority=FakeAuthorityGate(Role.STUDENT)
    )

    assert allowed is False


@pytest.mark.asyncio
async def test_check_authority_gate_allows_matching_role():
    engine = DefaultDialoguePolicyEngine(intent_router=FakeIntentRouter(Intent("x", None, False)))
    engine.register_skill(
        "grade_release", handler=None, support_policy=NO_GATE_POLICY,
        description="release a grade to the student", required_role=Role.TEACHER,
    )

    allowed = await engine.check_authority_gate(
        "grade_release", "teacher-1", "vk", authority=FakeAuthorityGate(Role.TEACHER)
    )

    assert allowed is True


@pytest.mark.asyncio
async def test_check_authority_gate_unknown_skill_raises():
    engine = DefaultDialoguePolicyEngine(intent_router=FakeIntentRouter(Intent("x", None, False)))

    with pytest.raises(UnknownSkillError):
        await engine.check_authority_gate(
            "never_registered", "student-1", "vk", authority=FakeAuthorityGate(Role.STUDENT)
        )


def test_render_support_block_includes_deny_patterns():
    engine = DefaultDialoguePolicyEngine(intent_router=FakeIntentRouter(Intent("x", None, False)))
    engine.register_skill("position_clarification", handler=None, support_policy=NO_GATE_POLICY, description="help the student clarify a vague position")

    block = engine.render_support_block("position_clarification", turn_index=0)

    assert "не формулируй тезис за студента" in block
    assert "reflect, affirm, question" in block


def test_render_support_block_without_fading_omits_level():
    engine = DefaultDialoguePolicyEngine(intent_router=FakeIntentRouter(Intent("x", None, False)))
    engine.register_skill("position_clarification", handler=None, support_policy=NO_GATE_POLICY, description="help the student clarify a vague position")

    block = engine.render_support_block("position_clarification", turn_index=5)

    assert "Current support level" not in block


@pytest.mark.parametrize(
    "turn_index,expected_level",
    [
        (0, "full_support"),
        (1, "full_support"),
        (2, "reduced"),
        (3, "reduced"),
        (4, "no_support"),
        (100, "no_support"),  # clamps to last level, never index error
    ],
)
def test_render_support_block_fading_by_turn_index(turn_index, expected_level):
    engine = DefaultDialoguePolicyEngine(intent_router=FakeIntentRouter(Intent("x", None, False)))
    engine.register_skill("refine_argument", handler=None, support_policy=GATED_POLICY, description="help the student strengthen an existing argument")

    block = engine.render_support_block("refine_argument", turn_index=turn_index)

    assert f"Current support level: {expected_level}" in block


@pytest.mark.asyncio
async def test_unknown_skill_raises_before_gate_check():
    engine = DefaultDialoguePolicyEngine(intent_router=FakeIntentRouter(Intent("x", None, False)))

    with pytest.raises(UnknownSkillError):
        await engine.check_attempt_gate("never_registered", "student-1", "task-1", attempts=FakeAttemptStore())


def test_unknown_skill_raises_on_render():
    engine = DefaultDialoguePolicyEngine(intent_router=FakeIntentRouter(Intent("x", None, False)))

    with pytest.raises(UnknownSkillError):
        engine.render_support_block("never_registered", turn_index=0)


@pytest.mark.asyncio
async def test_route_delegates_to_intent_router():
    expected = Intent(skill="position_clarification", confidence=0.9, fallback=False)
    engine = DefaultDialoguePolicyEngine(intent_router=FakeIntentRouter(expected))

    intent = await engine.route("query", "history", anchor=None)

    assert intent is expected


@pytest.mark.asyncio
async def test_route_collects_available_skills_from_registrations():
    router = FakeIntentRouter(Intent("x", None, False))
    engine = DefaultDialoguePolicyEngine(intent_router=router)
    engine.register_skill(
        "position_clarification", handler=None, support_policy=NO_GATE_POLICY,
        description="help the student clarify a vague position",
    )
    engine.register_skill(
        "refine_argument", handler=None, support_policy=GATED_POLICY,
        description="help the student strengthen an existing argument",
    )

    await engine.route("query", "history", anchor=None)

    assert router.last_available_skills is not None
    names = {s.name for s in router.last_available_skills}
    assert names == {"position_clarification", "refine_argument"}
    by_name = {s.name: s.description for s in router.last_available_skills}
    assert by_name["position_clarification"] == "help the student clarify a vague position"


def test_load_policy_overrides_replaces_registered_policy(tmp_path):
    engine = DefaultDialoguePolicyEngine(intent_router=FakeIntentRouter(Intent("x", None, False)))
    engine.register_skill("position_clarification", handler=None, support_policy=NO_GATE_POLICY, description="help the student clarify a vague position")

    override_path = tmp_path / "position_clarification.yaml"
    override_path.write_text(
        """
policy_id: position_clarification_v2
version: 2
target_action: "сформулировать собственный тезис"
protected_difficulty: "переход от смутной идеи к чёткой формулировке позиции"
attempt_gate: false
allowed_action_classes:
  - reflect
deny_patterns:
  - "новая формулировка запрета"
fading_rule: null
""",
        encoding="utf-8",
    )

    pytest.importorskip("yaml")
    engine.load_policy_overrides(tmp_path)

    block = engine.render_support_block("position_clarification", turn_index=0)
    assert "новая формулировка запрета" in block
    assert "не формулируй тезис за студента" not in block


def test_load_policy_overrides_ignores_unregistered_skills(tmp_path):
    engine = DefaultDialoguePolicyEngine(intent_router=FakeIntentRouter(Intent("x", None, False)))
    # no register_skill() call at all

    override_path = tmp_path / "some_other_skill.yaml"
    override_path.write_text(
        """
policy_id: x
version: 1
target_action: "x"
protected_difficulty: "x"
attempt_gate: false
allowed_action_classes: []
deny_patterns: []
fading_rule: null
""",
        encoding="utf-8",
    )

    pytest.importorskip("yaml")
    engine.load_policy_overrides(tmp_path)  # must not raise

    with pytest.raises(UnknownSkillError):
        engine.render_support_block("some_other_skill", turn_index=0)
