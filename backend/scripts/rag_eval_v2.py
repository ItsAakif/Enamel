#!/usr/bin/env python
"""Run the v2 retrieval evaluation (100+ queries, splits, evidence, calibration)."""

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
    parser = argparse.ArgumentParser(prog="rag_eval_v2.py")
    parser.add_argument("--embedding-provider", default="mock", choices=["mock", "fastembed"])
    parser.add_argument("--embedding-model", default="")
    parser.add_argument("--reranker", default="lexical", choices=["fastembed", "lexical", "off"])
    parser.add_argument("--modes", default="vector,hybrid")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--chunk-tokens", type=int, default=120)
    parser.add_argument("--overlap", type=int, default=20)
    parser.add_argument("--out", default="artifacts/evals")
    parser.add_argument("--label", default="")
    args = parser.parse_args(argv)

    os.environ["AI_EMBEDDING_PROVIDER"] = args.embedding_provider
    if args.embedding_model:
        os.environ["AI_EMBEDDING_MODEL"] = args.embedding_model
    os.environ["RAG_RERANKER"] = args.reranker
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

    import django

    django.setup()

    from apps.ai.evals.rag_eval_v2 import render_markdown, run_rag_eval_v2

    modes = tuple(part.strip() for part in args.modes.split(",") if part.strip())
    report = run_rag_eval_v2(
        modes=modes, k=args.k, chunk_tokens=args.chunk_tokens, overlap=args.overlap
    )

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = BACKEND_DIR / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"-{args.label}" if args.label else ""
    json_path = out_dir / f"rag-v2-{stamp}{suffix}.json"
    md_path = out_dir / f"rag-v2-{stamp}{suffix}.md"
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")

    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    for label, data in report["modes"].items():
        test = data["splits"]["test"]
        print(
            f"  {label}: test recall@{args.k}={test[f'recall@{args.k}']:.4f} "
            f"mrr@{args.k}={test[f'mrr@{args.k}']:.4f} "
            f"ndcg@{args.k}={test[f'ndcg@{args.k}']:.4f} "
            f"evidence={data['evidence']['coverage']:.4f} "
            f"foreign={data['tenant']['foreign_document_hits']} "
            f"p50={data['latency_ms']['p50']}ms"
        )
    print("  refusal:", {name: data["auroc"] for name, data in report["refusal"]["features"].items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
