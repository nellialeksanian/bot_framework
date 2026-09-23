"""TLS/proxy diagnostics without sending credentials or prompts."""

import argparse
import asyncio
import json

import httpx
from dotenv import dotenv_values


async def probe(url: str, verify: bool, trust_env: bool) -> dict:
    result = {"verify_ssl": verify, "trust_env": trust_env}
    try:
        async with httpx.AsyncClient(verify=verify, trust_env=trust_env, timeout=8) as client:
            response = await client.get(url.rstrip("/") + "/models")
            result["http_status"] = response.status_code
    except Exception as exc:
        result["error"] = type(exc).__name__
        cause = exc
        chain = []
        while cause is not None and len(chain) < 6:
            chain.append(type(cause).__name__)
            cause = cause.__cause__ or cause.__context__
        result["cause_types"] = chain
    return result


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", required=True)
    parser.add_argument("--allow-insecure-probe", action="store_true")
    args = parser.parse_args()
    config = dotenv_values(args.env)
    modes = [(True, True), (True, False)]
    if args.allow_insecure_probe:
        modes += [(False, True), (False, False)]
    results = await asyncio.gather(*(probe(config["LOCAL_HUB_API_BASE"], *m) for m in modes))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
