from __future__ import annotations

import json
import re
from typing import Literal

ParseStatus = Literal["clean_json", "regex_fallback", "raw_text_fallback"]


def parse_response(raw: str) -> tuple[str, ParseStatus]:
    """Extract response without losing Cyrillic, escaped quotes or nested JSON."""
    text = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    candidate = fenced.group(1) if fenced else text
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict) and isinstance(parsed.get("response"), str):
            return parsed["response"], "clean_json"
    except (json.JSONDecodeError, ValueError):
        pass
    # Tolerates prose around a JSON field and missing outer braces. Does not guess
    # unterminated string values or manufacture a reply from a tool call.
    match = re.search(r'"response"\s*:\s*("(?:[^"\\]|\\.)*")', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1)), "regex_fallback"
        except json.JSONDecodeError:
            pass
    return text, "raw_text_fallback"
