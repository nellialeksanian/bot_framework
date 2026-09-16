# A3. RAG Ingestion & Retrieval — проверки качества извлечённого текста.
# Источник наработок: /Users/nellyaleksanyan/Desktop/utils/croma_db_with_bge_model.ipynb
# (детект сканов через pdfplumber/PyMuPDF, эвристика text-extractable по числу
# символов на странице) — перенесено и обобщено из page-report в переиспользуемые
# проверки, применяемые к уже извлечённому тексту одной страницы.

from __future__ import annotations

import unicodedata

MIN_TEXT_CHARS_PER_PAGE = 30
"""Ниже этого порога страница считается сканом (нет текстового слоя)."""

MIN_PRINTABLE_RATIO = 0.85
"""Доля "нормальных" символов, ниже которой текст считается кракозябрами."""


def looks_like_scan(page_text: str, *, min_chars: int = MIN_TEXT_CHARS_PER_PAGE) -> bool:
    """True, если на странице практически нет извлекаемого текста —
    типичный признак скана без текстового слоя (изображение вместо текста)."""
    cleaned = " ".join((page_text or "").split())
    return len(cleaned) < min_chars


def looks_garbled(page_text: str, *, min_printable_ratio: float = MIN_PRINTABLE_RATIO) -> bool:
    """True, если в тексте много "непечатных"/неожиданных символов —
    типичный признак сломанной кодировки или нестандартного шрифта
    (PDF, где вместо букв каждому глифу назначен произвольный код).

    Эвристика по соотношению символов: считаем долю символов, которые
    являются буквой, цифрой, пробелом или обычной пунктуацией; текст
    ниже порога считается подозрительным на "кракозябры".
    """
    text = page_text or ""
    stripped = text.strip()
    if not stripped:
        return False

    total = len(stripped)
    bad = 0
    for ch in stripped:
        if ch == "�":  # Unicode replacement character — почти всегда мусор
            bad += 1
            continue
        category = unicodedata.category(ch)
        # Cc = control chars, Co = private-use area (частый источник "кракозябр"
        # при неверном кодировании шрифтовых кодов как текста), Cs = surrogate.
        if category in ("Cc", "Co", "Cs"):
            bad += 1

    printable_ratio = 1 - (bad / total)
    return printable_ratio < min_printable_ratio


def page_quality_warning(page_text: str, *, page_number: int) -> str | None:
    """Единая проверка одной страницы. Возвращает готовую строку для
    IngestReport.warnings, либо None если страница в порядке.
    Порядок проверки важен: скан (почти нет текста) проверяется раньше
    кракозябр (текст есть, но он "грязный") — иначе пустая строка может
    ложно не сработать ни на одну эвристику и молча пройти дальше.
    """
    if looks_like_scan(page_text):
        return f"likely_scanned_page_{page_number}"
    if looks_garbled(page_text):
        return f"garbled_text_page_{page_number}"
    return None
