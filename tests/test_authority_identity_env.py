import pytest

from botkit.authority.base import Role
from botkit.authority.identity_env import EnvIdentityGate


async def test_known_teacher_id_on_matching_platform_resolves_teacher():
    gate = EnvIdentityGate({"vk": frozenset({"123"})})

    role = await gate.resolve_role("123", "vk")

    assert role == Role.TEACHER


async def test_unknown_id_defaults_to_student():
    gate = EnvIdentityGate({"vk": frozenset({"123"})})

    role = await gate.resolve_role("999", "vk")

    assert role == Role.STUDENT


async def test_teacher_id_on_wrong_platform_does_not_leak_role():
    # Тот же ID мог принадлежать реальному преподавателю на VK, но на
    # Telegram у него другой ID — списки платформ не должны смешиваться.
    gate = EnvIdentityGate({"vk": frozenset({"123"})})

    role = await gate.resolve_role("123", "telegram")

    assert role == Role.STUDENT


async def test_platform_name_is_case_insensitive():
    gate = EnvIdentityGate({"VK": frozenset({"123"})})

    role = await gate.resolve_role("123", "vk")

    assert role == Role.TEACHER


async def test_empty_env_value_yields_no_teachers():
    gate = EnvIdentityGate.from_env({"TEACHER_IDS_VK": ""})

    role = await gate.resolve_role("123", "vk")

    assert role == Role.STUDENT


def test_from_env_parses_comma_separated_ids_per_platform():
    gate = EnvIdentityGate.from_env({
        "TEACHER_IDS_VK": "111, 222,333",
        "TEACHER_IDS_TELEGRAM": "444",
        "UNRELATED_VAR": "should be ignored",
    })

    assert gate._teacher_ids_by_platform == {
        "vk": frozenset({"111", "222", "333"}),
        "telegram": frozenset({"444"}),
    }


async def test_from_env_reads_real_os_environ_by_default(monkeypatch):
    monkeypatch.setenv("TEACHER_IDS_VK", "555")
    gate = EnvIdentityGate.from_env()

    role = await gate.resolve_role("555", "vk")

    assert role == Role.TEACHER
