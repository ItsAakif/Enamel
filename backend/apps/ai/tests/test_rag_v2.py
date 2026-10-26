"""v2 dataset integrity, split discipline and calibration helpers."""

from __future__ import annotations

from apps.ai.evals.rag_eval_v2 import (
    _auroc,
    _best_threshold,
    _coverage_feature,
    load_corpus_v2,
    load_questions_v2,
    load_tenant_b,
)


def test_v2_dataset_is_large_enough_and_labelled():
    corpus = load_corpus_v2()
    tenant_b = load_tenant_b()
    questions = load_questions_v2()
    slugs = {doc["slug"] for doc in corpus}

    assert len(corpus) >= 35
    assert len(tenant_b) >= 2
    assert len(questions) >= 100
    assert len({q["id"] for q in questions}) == len(questions)

    for question in questions:
        assert set(question["relevant"]) <= slugs
        if question["relevant"] and question.get("expected_answer"):
            assert any(
                question["expected_answer"].lower() in doc["text"].lower()
                for doc in corpus
                if doc["slug"] in question["relevant"]
            ), question["id"]
        if question["category"] in {"tenant", "unanswerable"}:
            assert question["relevant"] == []


def test_v2_splits_are_separate_and_populated():
    questions = load_questions_v2()
    splits = {q["split"] for q in questions}
    assert splits == {"tuning", "validation", "test"}
    for split in splits:
        assert sum(1 for q in questions if q["split"] == split) > 0
    # A question belongs to exactly one split; the test split is the largest.
    assert sum(1 for q in questions if q["split"] == "test") > sum(
        1 for q in questions if q["split"] == "tuning"
    )


def test_v2_covers_every_required_category():
    categories = {q["category"] for q in load_questions_v2()}
    assert {
        "direct",
        "paraphrase",
        "multi",
        "conflict",
        "longdoc",
        "unanswerable",
        "tenant",
    } <= categories


def test_auroc_ranks_separable_features_high():
    assert _auroc([0.9, 0.8], [0.2, 0.1]) == 1.0
    assert _auroc([0.1, 0.2], [0.8, 0.9]) == 0.0
    assert _auroc([0.5], [0.5]) == 0.5


def test_best_threshold_maximises_balanced_accuracy():
    threshold = _best_threshold([0.8, 0.7], [0.2, 0.3])
    assert threshold is not None
    assert 0.3 <= threshold <= 0.7


def test_coverage_feature_is_bounded_and_ignores_stopwords():
    assert _coverage_feature("where is the parking", "parking is behind the building") > 0
    assert 0.0 <= _coverage_feature("the and of", "anything") <= 1.0
