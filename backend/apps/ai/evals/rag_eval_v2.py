"""RAG evaluation v2: 100+ labelled queries, splits, evidence and calibration.

The v1 benchmark (36 questions) remains untouched as the versioned baseline.
This module adds:

* 39 documents (including long guides and superseded policies) and 106 queries
  across direct fact, paraphrase, multi-document, conflicting/superseded,
  long-document/chunk-boundary, unanswerable and cross-tenant categories;
* explicit tuning / validation / test splits, so thresholds and mode choices
  are never tuned on the held-out test set;
* document relevance from the dataset and **chunk** relevance derived by
  locating the labelled answer string inside the ingested chunks (verified,
  not hand-waved);
* retrieval metrics separated from answer evidence: evidence coverage and
  evidence precision over the retrieved context;
* a cross-tenant attack set: Clinic B documents are ingested and must never
  appear in Clinic A results;
* a refusal-rule comparison by AUROC (cosine vs query-term coverage), with the
  threshold chosen on tuning and reported on validation/test.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import transaction

from .rag_metrics import aggregate

DATASET_DIR = Path(__file__).resolve().parent / "datasets"
MODES = {"vector": ("vector", False), "hybrid": ("hybrid", False), "hybrid+rerank": ("hybrid", True)}
CONTENT_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    "a an and are as at be by can do does for from has have how i in is it of on or "
    "that the this to was what when where which who why will with you your".split()
)


def _load(name: str) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (DATASET_DIR / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_corpus_v2() -> list[dict[str, Any]]:
    return _load("rag_corpus_v2.jsonl")


def load_tenant_b() -> list[dict[str, Any]]:
    return _load("rag_corpus_v2_tenant_b.jsonl")


def load_questions_v2() -> list[dict[str, Any]]:
    return _load("rag_questions_v2.jsonl")


def _content_tokens(text: str) -> set[str]:
    return {
        token
        for token in CONTENT_RE.findall(str(text or "").lower())
        if token not in STOPWORDS and len(token) > 1
    }


def _coverage_feature(query: str, text: str) -> float:
    wanted = _content_tokens(query)
    if not wanted:
        return 0.0
    return len(wanted & _content_tokens(text)) / len(wanted)


def _auroc(positives: list[float], negatives: list[float]) -> float | None:
    if not positives or not negatives:
        return None
    wins = 0.0
    for positive in positives:
        for negative in negatives:
            if positive > negative:
                wins += 1.0
            elif positive == negative:
                wins += 0.5
    return round(wins / (len(positives) * len(negatives)), 4)


def _best_threshold(positives: list[float], negatives: list[float]) -> float | None:
    """Choose the threshold with the best balanced accuracy on the tuning split."""

    if not positives or not negatives:
        return None
    candidates = sorted(set(positives + negatives))
    best: tuple[float, float] | None = None
    for threshold in candidates:
        tpr = sum(1 for value in positives if value >= threshold) / len(positives)
        tnr = sum(1 for value in negatives if value < threshold) / len(negatives)
        score = tpr + tnr
        if best is None or score > best[0]:
            best = (score, threshold)
    return best[1] if best else None


def _apply_threshold(positives: list[float], negatives: list[float], threshold: float | None) -> dict[str, Any]:
    if threshold is None:
        return {"threshold": None, "tpr": None, "tnr": None}
    tpr = sum(1 for value in positives if value >= threshold) / len(positives) if positives else None
    tnr = sum(1 for value in negatives if value < threshold) / len(negatives) if negatives else None
    return {"threshold": round(threshold, 4), "tpr": round(tpr, 4) if tpr is not None else None,
            "tnr": round(tnr, 4) if tnr is not None else None}


def run_rag_eval_v2(
    *,
    modes: tuple[str, ...] = ("vector", "hybrid"),
    k: int = 5,
    chunk_tokens: int = 120,
    overlap: int = 20,
) -> dict[str, Any]:
    from apps.ai.rag.ingest import ingest_document
    from apps.ai.rag.retrieve import best_vector_score, search
    from apps.clinics.models import Clinic
    from apps.core.tenancy import with_clinic

    corpus = load_corpus_v2()
    tenant_b = load_tenant_b()
    questions = load_questions_v2()
    for label in modes:
        if label not in MODES:
            raise ValueError(f"Unknown mode '{label}'.")

    report: dict[str, Any] = {
        "version": 2,
        "generated_at": datetime.now(UTC).isoformat(),
        "embedding_provider": str(getattr(settings, "AI_EMBEDDING_PROVIDER", "mock")),
        "embedding_model": str(getattr(settings, "AI_EMBEDDING_MODEL", "")),
        "embedding_dim": int(getattr(settings, "AI_EMBEDDING_DIM", 384)),
        "chunking": {"max_tokens": chunk_tokens, "overlap": overlap},
        "corpus": {"documents": len(corpus), "tenant_b_documents": len(tenant_b)},
        "questions": {
            "total": len(questions),
            "by_split": dict(Counter(q["split"] for q in questions)),
            "by_category": dict(Counter(q["category"] for q in questions)),
        },
        "k": int(k),
        "modes": {},
    }

    with transaction.atomic():
        clinic_a = Clinic.objects.create(
            name="RAG v2 A", slug=f"ragv2-a-{uuid.uuid4().hex[:8]}", timezone="Asia/Kolkata", currency="INR"
        )
        clinic_b = Clinic.objects.create(
            name="RAG v2 B", slug=f"ragv2-b-{uuid.uuid4().hex[:8]}", timezone="Asia/Kolkata", currency="INR"
        )

        slug_by_doc: dict[str, str] = {}
        chunks_by_slug: dict[str, list[Any]] = {}
        for doc in corpus:
            result = ingest_document(
                clinic_a, title=doc["title"], text=doc["text"], kind=doc.get("kind") or "policy",
                max_tokens=chunk_tokens, overlap=overlap,
            )
            slug_by_doc[str(result.document.id)] = doc["slug"]
            with with_clinic(clinic_a):
                from apps.ai.models import KnowledgeChunk

                chunks_by_slug[doc["slug"]] = list(
                    KnowledgeChunk.all_objects.filter(document=result.document, status="active").order_by("ordinal")
                )

        tenant_b_ids: set[str] = set()
        for doc in tenant_b:
            result = ingest_document(
                clinic_b, title=doc["title"], text=doc["text"], kind=doc.get("kind") or "note",
                max_tokens=chunk_tokens, overlap=overlap,
            )
            tenant_b_ids.add(str(result.document.id))

        # Verified chunk relevance: a chunk is relevant when it contains the
        # labelled answer string. If no chunk contains it, the label is broken
        # and the evaluator reports it instead of silently scoring zero.
        relevant_chunks: dict[str, set[str]] = {}
        broken_labels: list[str] = []
        for question in questions:
            expected = str(question.get("expected_answer") or "")
            if not expected or not question["relevant"]:
                continue
            ids: set[str] = set()
            for slug in question["relevant"]:
                for chunk in chunks_by_slug.get(slug, []):
                    if expected.lower() in (chunk.text or "").lower():
                        ids.add(str(chunk.id))
            relevant_chunks[question["id"]] = ids
            if not ids:
                broken_labels.append(question["id"])
        report["labels"] = {
            "answerable_with_chunk_labels": len(relevant_chunks) - len(broken_labels),
            "broken_chunk_labels": broken_labels,
        }

        # Cache the cosine score and the top-1 coverage per question: these are
        # the candidate refusal features, independent of the retrieval mode.
        cosine_scores: dict[str, float | None] = {}
        coverage_feature: dict[str, float] = {}
        for question in questions:
            cosine_scores[question["id"]] = best_vector_score(clinic_a, question["question"])
            top = search(
                clinic_a, question["question"], top_k=1, mode="vector", log=False
            )
            coverage_feature[question["id"]] = (
                _coverage_feature(question["question"], top[0].text) if top else 0.0
            )

        for label in modes:
            mode, rerank = MODES[label]
            per_split: dict[str, list[tuple[list[str], set[str]]]] = {
                "tuning": [], "validation": [], "test": []
            }
            per_category: dict[str, list[tuple[list[str], set[str]]]] = {}
            evidence = {"answerable": 0, "covered": 0, "precision_sum": 0.0, "precision_n": 0}
            unanswerable_top: list[str] = []
            tenant_queries = 0
            foreign_hits = 0
            latencies: list[int] = []
            per_question: list[dict[str, Any]] = []

            for question in questions:
                started = time.perf_counter()
                hits = search(
                    clinic_a, question["question"], top_k=k, mode=mode, rerank=rerank, log=False
                )
                latency = int((time.perf_counter() - started) * 1000)
                latencies.append(latency)
                ranked_docs = []
                seen_docs: set[str] = set()
                for hit in hits:
                    doc_id = str(hit.document_id)
                    if doc_id in tenant_b_ids:
                        foreign_hits += 1
                    slug = slug_by_doc.get(doc_id)
                    if slug and slug not in seen_docs:
                        seen_docs.add(slug)
                        ranked_docs.append(slug)
                if question["category"] == "tenant":
                    tenant_queries += 1

                relevant = set(question["relevant"])
                if relevant:
                    per_split[question["split"]].append((ranked_docs, relevant))
                    per_category.setdefault(question["category"], []).append((ranked_docs, relevant))

                    expected = str(question.get("expected_answer") or "")
                    if expected:
                        evidence["answerable"] += 1
                        wanted = relevant_chunks.get(question["id"], set())
                        hit_ids = {str(hit.chunk_id) for hit in hits}
                        covered = bool(hit_ids & wanted)
                        if covered:
                            evidence["covered"] += 1
                        if hits:
                            evidence["precision_sum"] += len(hit_ids & wanted) / len(hit_ids)
                            evidence["precision_n"] += 1
                elif question["category"] == "unanswerable":
                    unanswerable_top.append(question["id"])

                per_question.append(
                    {
                        "id": question["id"],
                        "split": question["split"],
                        "category": question["category"],
                        "ranked": ranked_docs,
                        "top_chunk": hits[0].chunk_id if hits else None,
                        "latency_ms": latency,
                    }
                )

            split_metrics = {
                name: aggregate(per_split[name], k=k)
                for name in ("tuning", "validation", "test")
            }
            by_cat = {name: aggregate(rows, k=k) for name, rows in per_category.items()}
            latencies.sort()
            report["modes"][label] = {
                "mode": mode,
                "rerank": rerank,
                "splits": split_metrics,
                "by_category": by_cat,
                "evidence": {
                    "answerable_with_answer": evidence["answerable"],
                    "coverage": round(evidence["covered"] / evidence["answerable"], 4)
                    if evidence["answerable"] else 0.0,
                    "precision": round(evidence["precision_sum"] / evidence["precision_n"], 4)
                    if evidence["precision_n"] else 0.0,
                },
                "tenant": {
                    "queries": tenant_queries,
                    "foreign_document_hits": foreign_hits,
                    "passed": foreign_hits == 0,
                },
                "latency_ms": {
                    "p50": latencies[len(latencies) // 2] if latencies else 0,
                    "p95": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
                    if latencies else 0,
                },
                "per_question": per_question,
            }

        # Refusal-rule comparison. Positive = answerable, negative = unanswerable.
        answerable_ids = [q["id"] for q in questions if q["relevant"] and q.get("expected_answer")]
        unanswerable_ids = [q["id"] for q in questions if q["category"] == "unanswerable"]

        def values(feature: dict[str, float | None], ids: list[str]) -> list[float]:
            return [float(feature[i]) for i in ids if feature.get(i) is not None]

        calibration: dict[str, Any] = {}
        for name, feature in (("cosine", cosine_scores), ("coverage", coverage_feature)):
            positives = values(feature, answerable_ids)
            negatives = values(feature, unanswerable_ids)
            tuning_pos = [
                float(feature[q["id"]]) for q in questions
                if q["split"] == "tuning" and q["id"] in answerable_ids and feature.get(q["id"]) is not None
            ]
            tuning_neg = [
                float(feature[q["id"]]) for q in questions
                if q["split"] == "tuning" and q["id"] in unanswerable_ids and feature.get(q["id"]) is not None
            ]
            threshold = _best_threshold(tuning_pos, tuning_neg)
            tuning_validation_pos = [
                float(feature[q["id"]])
                for q in questions
                if q["split"] in {"tuning", "validation"}
                and q["id"] in answerable_ids
                and feature.get(q["id"]) is not None
            ]
            tuning_validation_neg = [
                float(feature[q["id"]])
                for q in questions
                if q["split"] in {"tuning", "validation"}
                and q["id"] in unanswerable_ids
                and feature.get(q["id"]) is not None
            ]
            test_pos = [
                float(feature[q["id"]])
                for q in questions
                if q["split"] == "test"
                and q["id"] in answerable_ids
                and feature.get(q["id"]) is not None
            ]
            test_neg = [
                float(feature[q["id"]])
                for q in questions
                if q["split"] == "test"
                and q["id"] in unanswerable_ids
                and feature.get(q["id"]) is not None
            ]
            calibration[name] = {
                "auroc": _auroc(positives, negatives),
                # Selection uses only tuning+validation; the test AUROC is
                # reported for information and never used to pick the feature.
                "auroc_tuning_validation": _auroc(tuning_validation_pos, tuning_validation_neg),
                "auroc_test": _auroc(test_pos, test_neg),
                "tuning": _apply_threshold(tuning_pos, tuning_neg, threshold),
                "validation": _apply_threshold(
                    [float(feature[q["id"]]) for q in questions if q["split"] == "validation" and q["id"] in answerable_ids and feature.get(q["id"]) is not None],
                    [float(feature[q["id"]]) for q in questions if q["split"] == "validation" and q["id"] in unanswerable_ids and feature.get(q["id"]) is not None],
                    threshold,
                ),
                "test": _apply_threshold(
                    [float(feature[q["id"]]) for q in questions if q["split"] == "test" and q["id"] in answerable_ids and feature.get(q["id"]) is not None],
                    [float(feature[q["id"]]) for q in questions if q["split"] == "test" and q["id"] in unanswerable_ids and feature.get(q["id"]) is not None],
                    threshold,
                ),
            }
        report["refusal"] = {
            "features": calibration,
            "chosen_feature": max(
                (
                    name
                    for name, data in calibration.items()
                    if data["auroc_tuning_validation"] is not None
                ),
                key=lambda name: calibration[name]["auroc_tuning_validation"] or 0.0,
            )
            if any(
                data["auroc_tuning_validation"] is not None
                for data in calibration.values()
            )
            else None,
            "selection_data": "tuning+validation only; test AUROC is reported, not used",
            "note": (
                "AUROC > 0.5 means the feature ranks answerable questions above "
                "unanswerable ones. A feature that cannot separate them should drive "
                "an answer-level grounding check, not a threshold."
            ),
        }

        transaction.set_rollback(True)

    return report


def render_markdown(report: dict[str, Any]) -> str:
    k = report["k"]
    lines = [
        "# RAG evaluation v2",
        "",
        f"- generated_at: `{report['generated_at']}`",
        f"- embeddings: `{report['embedding_provider']}` / `{report['embedding_model']}` ({report['embedding_dim']}d)",
        f"- chunking: {report['chunking']['max_tokens']} tokens / {report['chunking']['overlap']} overlap",
        f"- corpus: {report['corpus']['documents']} documents (+{report['corpus']['tenant_b_documents']} tenant-B documents)",
        f"- questions: {report['questions']['total']} {report['questions']['by_split']}",
        f"- broken chunk labels: {report['labels']['broken_chunk_labels'] or 'none'}",
        "",
        f"## Held-out test (k={k})",
        "",
        "| mode | recall@k | MRR@k | nDCG@k | evidence coverage | evidence precision | foreign hits | p50 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for label, data in report["modes"].items():
        test = data["splits"]["test"]
        lines.append(
            f"| {label} | {test[f'recall@{k}']:.4f} | {test[f'mrr@{k}']:.4f} | "
            f"{test[f'ndcg@{k}']:.4f} | {data['evidence']['coverage']:.4f} | "
            f"{data['evidence']['precision']:.4f} | {data['tenant']['foreign_document_hits']} | "
            f"{data['latency_ms']['p50']} ms |"
        )
    lines += ["", "## By category (test)", "", "| mode | category | recall@k | MRR@k |", "|---|---|---|---|"]
    for label, data in report["modes"].items():
        for category, metrics in data["by_category"].items():
            lines.append(
                f"| {label} | {category} | {metrics[f'recall@{k}']:.4f} | {metrics[f'mrr@{k}']:.4f} |"
            )
    lines += [
        "",
        "## Refusal rules",
        "",
        "| feature | AUROC (tuning+validation) | AUROC (test) | tuning threshold | test TPR | test TNR |",
        "|---|---|---|---|---|---|",
    ]
    for name, data in report["refusal"]["features"].items():
        test = data["test"]
        lines.append(
            f"| {name} | {data.get('auroc_tuning_validation')} | {data.get('auroc_test')} | "
            f"{data['tuning']['threshold']} | {test.get('tpr')} | {test.get('tnr')} |"
        )
    lines += [
        "",
        f"Chosen feature: **{report['refusal']['chosen_feature']}**",
        "",
        report["refusal"]["note"],
    ]
    return "\n".join(lines) + "\n"


__all__ = [
    "MODES",
    "load_corpus_v2",
    "load_questions_v2",
    "load_tenant_b",
    "render_markdown",
    "run_rag_eval_v2",
]
