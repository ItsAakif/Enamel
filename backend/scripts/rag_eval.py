#!/usr/bin/env python
"""Run the labelled retrieval evaluation.

Examples::

    python scripts/rag_eval.py --embedding-provider mock
    python scripts/rag_eval.py --embedding-provider fastembed \\
        --reranker fastembed --modes vector,hybrid,hybrid+rerank

Writing JSON and Markdown under ``artifacts/evals``. The run is transactional:
the throwaway clinic is rolled back, so no data is left behind.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rag_eval.py")
    parser.add_argument("--embedding-provider", default="mock", choices=["mock", "fastembed"])
    parser.add_argument("--embedding-model", default="")
    parser.add_argument("--reranker", default="fastembed", choices=["fastembed", "lexical", "off"])
    parser.add_argument(
        "--modes",
        default="vector,hybrid,hybrid+rerank",
        help="Comma-separated subset of vector,hybrid,hybrid+rerank.",
    )
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None, help="Optional question cap.")
    parser.add_argument("--out", default="artifacts/evals")
    parser.add_argument("--label", default="", help="Suffix for the artifact filenames.")
    args = parser.parse_args(argv)

    os.environ["AI_EMBEDDING_PROVIDER"] = args.embedding_provider
    if args.embedding_model:
        os.environ["AI_EMBEDDING_MODEL"] = args.embedding_model
    os.environ["RAG_RERANKER"] = args.reranker
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

    import django

    django.setup()

    from apps.ai.evals.rag_eval import render_markdown, run_rag_eval

    modes = tuple(part.strip() for part in args.modes.split(",") if part.strip())
    report = run_rag_eval(modes=modes, k=args.k, limit=args.limit)

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = BACKEND_DIR / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"-{args.label}" if args.label else ""
    json_path = out_dir / f"rag-{stamp}{suffix}.json"
    md_path = out_dir / f"rag-{stamp}{suffix}.md"
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")

    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    for label, data in report["modes"].items():
        print(
            f"  {label}: recall@{args.k}={data[f'recall@{args.k}']:.4f} "
            f"mrr@{args.k}={data[f'mrr@{args.k}']:.4f} "
            f"ndcg@{args.k}={data[f'ndcg@{args.k}']:.4f} "
            f"unanswerable_above={data['unanswerable']['above_threshold']}/"
            f"{data['unanswerable']['total']} "
            f"p50={data['latency_ms']['p50']}ms"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
