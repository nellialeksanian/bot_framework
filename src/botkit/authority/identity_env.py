# B4. Human Authority Gate — конкретная реализация IdentityGate (SC16).
#
# Источник инварианта: "Модули фреймворка — приоритет.md", раздел B4 —
# "IdentityGate.resolve_role() никогда не вызывает LLM — это чисто
# структурная проверка (список ID, токен приглашения, паролированный
# доступ)". Ближайший живой прецедент — hanskelsen_bot::REFERENCE_VERIFICATION
# (TEACHER_PASSWORD из .env) — единственный из 15 разобранных ботов с
# структурной, а не эвристической проверкой роли (см. "Общие принципы
# ботов и связь с SC.md", разделы SC14/SC16).
#
# Здесь тот же принцип реализован без пароля, списком допущенных
# platform_user_id: преподаватель — это тот, чей ID заранее внесён в env,
# а не тот, кто ввёл правильную фразу. Список ведётся отдельно на каждую
# платформу (TEACHER_IDS_<PLATFORM>), т.к. один и тот же человек имеет
# разные ID в VK и Telegram — общий список смешал бы их и мог случайно
# выдать чужому пользователю роль на другой платформе.

from __future__ import annotations

import os

from botkit.authority.base import Role

_ENV_PREFIX = "TEACHER_IDS_"


def _parse_id_list(raw: str) -> frozenset[str]:
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


class EnvIdentityGate:
    """IdentityGate поверх списков ID в переменных окружения.

    Роль по умолчанию — Role.STUDENT (fail-closed в сторону наименьших
    прав): ID, отсутствующий в TEACHER_IDS_<PLATFORM>, не получает
    повышенных прав молча ни при какой опечатке в env.
    """

    def __init__(self, teacher_ids_by_platform: dict[str, frozenset[str]]):
        # Ключи платформы приводятся к нижнему регистру один раз здесь,
        # чтобы resolve_role() не зависел от регистра platform на вызове.
        self._teacher_ids_by_platform = {
            platform.lower(): ids for platform, ids in teacher_ids_by_platform.items()
        }

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "EnvIdentityGate":
        """Читает все переменные вида TEACHER_IDS_<PLATFORM>=id1,id2,...

        Например: TEACHER_IDS_VK=123456,789 и TEACHER_IDS_TELEGRAM=42.
        <PLATFORM> в имени переменной — платформа в верхнем регистре,
        как её объявляет автор бота (совпадает с IncomingMessage.platform
        из A4, приведённым к верхнему регистру).
        """
        source = env if env is not None else os.environ
        teacher_ids_by_platform: dict[str, frozenset[str]] = {}
        for key, value in source.items():
            if not key.startswith(_ENV_PREFIX):
                continue
            platform = key[len(_ENV_PREFIX):]
            if not platform:
                continue
            teacher_ids_by_platform[platform] = _parse_id_list(value)
        return cls(teacher_ids_by_platform)

    async def resolve_role(self, platform_user_id: str, platform: str) -> Role:
        teacher_ids = self._teacher_ids_by_platform.get(platform.lower(), frozenset())
        if platform_user_id in teacher_ids:
            return Role.TEACHER
        return Role.STUDENT
