from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .orchestrator import run_all
from .utils import utc_now_iso


def main():
    parser = argparse.ArgumentParser(description="Browser-based multi-LLM research orchestrator (Playwright).")
    parser.add_argument("--brief", required=True, help="Path to brief YAML file")
    parser.add_argument("--run-id", default=None, help="Run id (default: utc timestamp)")
    parser.add_argument("--headless", action="store_true", help="Run headless (NOT recommended for LLM sites)")
    args = parser.parse_args()

    brief_path = Path(args.brief).expanduser().resolve()
    run_id = args.run_id or utc_now_iso().replace(":", "").replace("+", "_")

    run_index_path, results = asyncio.run(run_all(brief_path, run_id=run_id, headless=args.headless))

    ok = sum(1 for r in results if r.ok)
    total = len(results)
    print(f"\nDone. OK {ok}/{total}")
    print(f"Run index note: {run_index_path}\n")


if __name__ == "__main__":
    main()
