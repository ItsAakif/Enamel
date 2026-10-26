"""Labelled retrieval evaluation for the knowledge base.

The corpus and questions live in ``datasets/rag_corpus.jsonl`` and
``datasets/rag_questions.jsonl``. Every question names the documents that
contain its answer; unanswerable questions have an empty ``relevant`` list and
are scored by whether the best cosine score crosses the refusal threshold
(they should not).

The evaluator ingests the corpus into a throwaway clinic inside one
transaction and rolls it back, so the development database is untouched. It is
model-agnostic: run it with the mock embeddings for a deterministic smoke, or
with ``fastembed`` for the real semantic vectors.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import transaction

from .rag_metrics import aggregate

DATASET_DIR = Path(__file__).resolve().parent / "datasets"
CATEGORIES = ("lexical", "paraphrase", "multi-hop", "unanswerable")
MODES = {
    "vector": ("vector", False),
    "lexical": ("lexical", False),
    "hybrid": ("hybrid", False),
    "hybrid+rerank": ("hybrid", True),
}


def load_corpus() -> list[dict[str, Any]]:
    rows = []
    for line in (DATASET_DIR / "rag_corpus.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def load_questions() -> list[dict[str, Any]]:
    rows = []
    for line in (DATASET_DIR / "rag_questions.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def run_rag_eval(
    *,
    modes: tuple[str, ...] = ("vector", "hybrid", "hybrid+rerank"),
    k: int = 5,
    limit: int | None = None,
) -> dict[str, Any]:
    """Ingest the corpus, run every question per mode and return the report."""

    from apps.ai.rag.ingest import ingest_document
    from apps.ai.rag.retrieve import best_vector_score, search
    from apps.clinics.models import Clinic

    corpus = load_corpus()
    questions = load_questions()
    if limit:
        questions = questions[: max(1, int(limit))]
    for label in modes:
        if label not in MODES:
            raise ValueError(f"Unknown mode '{label}'; expected one of {', '.join(MODES)}.")

    threshold = float(getattr(settings, "RAG_MIN_SCORE", 0.2))
    report: dict[str, Any] = {
        "version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "embedding_provider": str(getattr(settings, "AI_EMBEDDING_PROVIDER", "mock")),
        "embedding_model": str(getattr(settings, "AI_EMBEDDING_MODEL", "")),
        "embedding_dim": int(getattr(settings, "AI_EMBEDDING_DIM", 384)),
        "reranker": str(getattr(settings, "RAG_RERANKER", "fastembed")),
        "k": int(k),
        "corpus_documents": len(corpus),
        "questions": len(questions),
        "threshold": threshold,
        "modes": {},
    }

    with transaction.atomic():
        clinic = Clinic.objects.create(
            name="RAG Evaluation",
            slug=f"rag-eval-{uuid.uuid4().hex[:10]}",
            timezone="Asia/Kolkata",
            currency="INR",
        )
        slug_by_doc: dict[str, str] = {}
        chunk_count = 0
        for doc in corpus:
            result = ingest_document(
                clinic,
                title=str(doc["title"]),
                text=str(doc["text"]),
                kind=str(doc.get("kind") or "policy"),
            )
            slug_by_doc[str(result.document.id)] = str(doc["slug"])
            chunk_count += int(result.document.chunk_count or 0)
        report["corpus_chunks"] = chunk_count

        for label in modes:
            mode, rerank = MODES[label]
            rankings: list[tuple[list[str], set[str]]] = []
            by_category: dict[str, list[tuple[list[str], set[str]]]] = {
                name: [] for name in CATEGORIES
            }
            unanswerable_above = 0
            unanswerable_total = 0
            latencies: list[int] = []
            per_question: list[dict[str, Any]] = []

            for question in questions:
                started = time.perf_counter()
                if mode == "lexical":
                    from apps.ai.rag.lexical import search_lexical

                    hits = search_lexical(clinic, str(question["question"]), top_k=k)
                else:
                    hits = search(
                        clinic,
                        str(question["question"]),
                        top_k=k,
                        mode=mode,
                        rerank=rerank,
                        log=False,
                    )
                latency_ms = int((time.perf_counter() - started) * 1000)
                latencies.append(latency_ms)
                ranked_docs = _dedupe(
                    [slug_by_doc[str(hit.document_id)] for hit in hits if str(hit.document_id) in slug_by_doc]
                )
                relevant = set(question.get("relevant") or [])
                if relevant:
                    rankings.append((ranked_docs, relevant))
                    by_category[str(question.get("category"))].append((ranked_docs, relevant))
                else:
                    unanswerable_total += 1
                    best = best_vector_score(clinic, str(question["question"]))
                    above = best is not None and float(best) >= threshold
                    if above:
                        unanswerable_above += 1
                    per_question.append(
                        {
                            "id": question["id"],
                            "category": "unanswerable",
                            "ranked": ranked_docs,
                            "best_cosine": round(float(best), 4) if best is not None else None,
                            "above_threshold": above,
                        }
                    )

            metrics = aggregate(rankings, k=k)
            by_cat = {
                name: aggregate(rows, k=k) for name, rows in by_category.items() if rows
            }
            latencies.sort()
            report["modes"][label] = {
                "mode": mode,
                "rerank": rerank,
                **metrics,
                "by_category": by_cat,
                "unanswerable": {
                    "total": unanswerable_total,
                    "above_threshold": unanswerable_above,
                    "false_positive_rate": (
                        round(unanswerable_above / unanswerable_total, 4)
                        if unanswerable_total
                        else 0.0
                    ),
                },
                "latency_ms": {
                    "p50": latencies[len(latencies) // 2] if latencies else 0,
                    "p95": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
                    if latencies
                    else 0,
                },
                "per_question": per_question,
            }

        # Calibration evidence for the refusal gate: a single cosine threshold
        # only works when the answerable and unanswerable score ranges do not
        # overlap. Record the observed ranges so the finding is part of the
        # artifact rather than a footnote.
        answerable: list[float] = []
        unanswerable: list[float] = []
        for question in questions:
            score = best_vector_score(clinic, str(question["question"]))
            if score is None:
                continue
            (answerable if question.get("relevant") else unanswerable).append(float(score))
        report["calibration"] = {
            "answerable_min": round(min(answerable), 4) if answerable else None,
            "answerable_max": round(max(answerable), 4) if answerable else None,
            "unanswerable_min": round(min(unanswerable), 4) if unanswerable else None,
            "unanswerable_max": round(max(unanswerable), 4) if unanswerable else None,
            "separated": bool(answerable and unanswerable and min(answerable) > max(unanswerable)),
            "note": (
                "When separated is false, no cosine threshold can reliably refuse an "
                "unanswerable question on this corpus; answer-level grounding checks "
                "are required instead of a similarity cutoff."
            ),
        }

        transaction.set_rollback(True)

    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# RAG retrieval evaluation",
        "",
        f"- generated_at: `{report['generated_at']}`",
        f"- embeddings: `{report['embedding_provider']}` / `{report['embedding_model']}` "
        f"({report['embedding_dim']}d)",
        f"- reranker: `{report['reranker']}`",
        f"- corpus: {report['corpus_documents']} documents, "
        f"{report.get('corpus_chunks', 0)} chunks",
        f"- questions: {report['questions']} (k={report['k']})",
        "",
        "| mode | recall@k | MRR@k | nDCG@k | unanswerable above threshold | p50 ms | p95 ms |",
        "|---|---|---|---|---|---|---|",
    ]
    for label, data in report["modes"].items():
        lines.append(
            f"| {label} | {data[f'recall@{report['k']}']:.4f} | "
            f"{data[f'mrr@{report['k']}']:.4f} | {data[f'ndcg@{report['k']}']:.4f} | "
            f"{data['unanswerable']['above_threshold']}/{data['unanswerable']['total']} | "
            f"{data['latency_ms']['p50']} | {data['latency_ms']['p95']} |"
        )
    lines += ["", "## Per-category recall@k", "", "| mode | lexical | paraphrase | multi-hop |", "|---|---|---|---|"]
    for label, data in report["modes"].items():
        cats = data.get("by_category", {})
        key = f"recall@{report['k']}"
        lines.append(
            f"| {label} | {cats.get('lexical', {}).get(key, 0.0):.4f} | "
            f"{cats.get('paraphrase', {}).get(key, 0.0):.4f} | "
            f"{cats.get('multi-hop', {}).get(key, 0.0):.4f} |"
        )
    lines += [
        "",
        "## Refusal-threshold calibration",
        "",
    ]
    calibration = report.get("calibration") or {}
    lines += [
        f"- answerable cosine range: {calibration.get('answerable_min')} .. "
        f"{calibration.get('answerable_max')}",
        f"- unanswerable cosine range: {calibration.get('unanswerable_min')} .. "
        f"{calibration.get('unanswerable_max')}",
        f"- separated: **{calibration.get('separated')}**",
        "",
        str(calibration.get("note") or ""),
        "",
        "Re-run with:",
        "",
        "```",
        "python scripts/rag_eval.py --embedding-provider fastembed "
        "--modes vector,lexical,hybrid,hybrid+rerank",
        "```",
    ]
    return "\n".join(lines) + "\n"


__all__ = ["CATEGORIES", "MODES", "load_corpus", "load_questions", "render_markdown", "run_rag_eval"]
