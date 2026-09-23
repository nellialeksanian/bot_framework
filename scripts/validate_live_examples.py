"""Opt-in real API validation using original bot prompts and supplied RACI fixtures.

This sends model requests (billable on 302.ai), never messenger messages. Keys are
read from the supplied dotenv file; reports contain no keys, endpoints or raw text.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import base64
import hashlib
import importlib.util
import json
import logging
import sys
import time
import types
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from botkit.llm import FallbackLLM, LLMConfig, LLMGateway
from botkit.llm.compat import LegacyStringLLM
from botkit.usage import PriceBook, SQLiteUsageTracker, usage_context


def load_source(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_check_matrix(path: Path, artifacts: Path):
    # Execute the original module, replacing only its legacy infrastructure import.
    # Rules, prompt builders, validators and domain transformations stay unchanged.
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "model_loading":
            node.module = "botkit.llm"
    module = types.ModuleType("live_original_check_matrix")
    module.__file__ = str(path)
    exec(compile(tree, str(path), "exec"), module.__dict__)
    module._FAILURES_LOG_PATH = artifacts / "matrix_failures.jsonl"
    return module


def load_customs_intent(path: Path):
    # Only this exact function is needed; importing the whole legacy module would
    # create its unrelated global tokenizer, logger and usage tracker.
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    function = next(
        n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "intent_recognition"
    )
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    selected = ast.fix_missing_locations(ast.Module(body=[future, function], type_ignores=[]))
    namespace = {}
    exec(compile(selected, str(path), "exec"), namespace)
    return namespace["intent_recognition"]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", required=True)
    parser.add_argument("--organizer", required=True, type=Path)
    parser.add_argument("--reflector", required=True, type=Path)
    parser.add_argument("--customs", required=True, type=Path)
    parser.add_argument("--cases", nargs="+", default=["all"])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    load_dotenv(args.env, override=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or Path(f"artifacts/live-{stamp}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError("Choose a new output file to preserve earlier evidence")
    tracker = SQLiteUsageTracker(output.with_suffix(".sqlite3"), prices=PriceBook("config/prices.json"))
    direct_config = replace(LLMConfig.from_env("302ai"), timeout=150, max_retries=0, mode="chat")
    hub_config = replace(LLMConfig.from_env("hub"), timeout=12, max_retries=0)
    import os

    backup_config = replace(hub_config, model=os.environ["LOCAL_HUB_FALLBACK_MODEL_NAME"])
    direct = LLMGateway(direct_config, tracker=tracker)
    direct_async = LLMGateway(replace(direct_config, mode="async", poll_interval=0.5), tracker=tracker)
    qwen = LLMGateway(hub_config, tracker=tracker)
    hub_backup = LLMGateway(backup_config, tracker=tracker)
    chain = FallbackLLM(qwen, [hub_backup, direct], tracker=tracker, timeout=180, primary_timeout=12)
    sys.path.insert(0, str(args.organizer.resolve() / "app"))
    from raci import matrix_parsing, matrix_vision

    check_matrix = load_check_matrix(args.organizer / "app/raci/check_matrix.py", output.parent)
    reflector = load_source("live_reflector_skills", args.reflector / "app/skills.py")
    customs_intent = load_customs_intent(args.customs / "app/skills.py")
    image_path = args.organizer / "matrices_examples/image.png"
    image_bytes = image_path.read_bytes()
    image_b64 = base64.b64encode(image_bytes).decode("ascii")

    async def text_probe(provider):
        response = await provider.ainvoke(
            'Верни ровно JSON: {"response":"OK"}', max_tokens=256, temperature=0
        )
        assert response.response.strip().upper() == "OK", "Unexpected text response"
        return {
            "model": response.model,
            "parse_status": response.parse_status,
            "usage_known": response.usage_known,
            "fallback_used": response.meta.get("fallback_used", False),
            "fallback_failures": response.meta.get("fallback_failures", []),
        }

    async def image_probe(provider):
        response = await provider.ainvoke_with_image_b64(
            matrix_vision.QWEN_TABLE_PROMPT, image_b64, mime_type="image/png", max_tokens=4000, temperature=0
        )
        payload = matrix_vision._extract_json_object(response.raw)
        grid = matrix_vision._validate_grid(payload.get("grid"))
        parsed = matrix_parsing.parse_table_grid(grid, source="vision")
        assert len(grid) == 15 and all(len(row) == 5 for row in grid), "Expected the supplied 15x5 grid"
        assert len(parsed.tasks) == 14 and len(parsed.participants) == 4
        roles = [
            ["И", "И", "О", "Э"],
            ["О", "И", "У", "И"],
            ["Э", "И", "О", "Э"],
            ["О", "У", "И", "Э"],
            ["Э", "У", "О", "Э"],
            ["Э", "О", "И", "У"],
            ["У", "И", "О", "У"],
            ["И", "И", "О", "Э"],
            ["У", "Э", "О", "У"],
            ["У", "О", "И", "И"],
            ["Э", "И", "О", "Э"],
            ["Э", "У", "О", "И"],
            ["Э", "О", "И", "Э"],
            ["У", "О", "И", "У"],
        ]
        recognized = [[str(cell).strip().upper() for cell in row[1:]] for row in grid[1:]]
        assert recognized == roles, "Recognized role cells differ from the supplied image"
        return {
            "model": response.model,
            "fixture": image_path.name,
            "fixture_sha256": hashlib.sha256(image_bytes).hexdigest(),
            "rows": len(grid),
            "columns": 5,
            "role_cells_exact": 56,
            "fallback_used": response.meta.get("fallback_used", False),
            "polls": response.meta.get("poll_count", 0),
        }

    async def matrix_case():
        path = args.organizer / "matrices_examples/matrices_with_mistakes.docx"
        matrices = matrix_parsing.parse_matrices_from_docx(str(path))
        assert matrices
        result = await check_matrix.check_matrix(LegacyStringLLM(direct, raw=True), matrices[0])
        assert result["status"] in {"PASS", "FAIL"} and isinstance(result["errors"], list) and result["reply"]
        return {
            "fixture": path.name,
            "matrices_parsed": len(matrices),
            "checked_matrix_index": 0,
            "status_from_model": result["status"],
            "findings": len(result["errors"]),
            "schema_valid": True,
            "pedagogical_correctness_asserted": False,
        }

    async def reflector_case():
        result = await reflector.p1_goals(
            LegacyStringLLM(direct, raw=True),
            "На этой неделе я хотел подготовить план командного проекта и согласовать роли с тремя участниками.",
        )
        assert isinstance(result["response"], str) and result["response"]
        assert isinstance(result["layer_complete"], bool) and "layer_data" in result
        return {
            "source_function": "reflector_kolb.app.skills.p1_goals",
            "schema_valid": True,
            "layer_complete": result["layer_complete"],
        }

    async def customs_case():
        result = await customs_intent(LegacyStringLLM(direct), "Что такое инспекционно-досмотровый комплекс?")
        assert result == "REFERENCE", "Unexpected customs intent"
        return {"source_function": "customs_bot.app.skills.intent_recognition", "intent": result}

    cases = {
        "hub_qwen_text": lambda: text_probe(qwen),
        "hub_302_text": lambda: text_probe(hub_backup),
        "hub_qwen_image": lambda: image_probe(qwen),
        "hub_302_image": lambda: image_probe(hub_backup),
        "direct_302_image": lambda: image_probe(direct),
        "direct_302_async_image": lambda: image_probe(direct_async),
        "fallback_text": lambda: text_probe(chain),
        "fallback_image": lambda: image_probe(chain),
        "organizer_docx_check": matrix_case,
        "reflector_goals": reflector_case,
        "customs_intent": customs_case,
    }
    selected = list(cases) if args.cases == ["all"] else args.cases
    if any(name not in cases for name in selected):
        raise ValueError("Unknown case name")
    report = {"timestamp": stamp, "cases": [], "publication_allowed": False, "messenger_live_verified": False}
    try:
        for name in selected:
            started = time.perf_counter()
            with usage_context(bot_id="live-validation", user_id="test-fixture", skill_context=name):
                try:
                    details = await cases[name]()
                    row = {"name": name, "status": "passed", **details}
                except Exception as exc:
                    row = {
                        "name": name,
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "http_status": getattr(exc, "status_code", None),
                    }
                    if isinstance(exc, AssertionError):
                        row["assertion"] = str(exc)
                row["elapsed_seconds"] = round(time.perf_counter() - started, 3)
                report["cases"].append(row)
                report["usage"] = asdict(await tracker.stats())
                # Checkpoint evidence after every case, including failed cases.
                output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
                )
                print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        await chain.aclose()
        await direct_async.aclose()
    print(f"Report: {output}", flush=True)
    return int(any(row["status"] != "passed" for row in report["cases"]))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
