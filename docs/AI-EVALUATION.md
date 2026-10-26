# AI evaluation

This document records what is measured, how, and what the numbers mean. It is
written for a reviewer who wants to check the claims rather than trust them.

## 1. Retrieval (knowledge base)

**Dataset** - `backend/apps/ai/evals/datasets/rag_corpus.jsonl` (16 synthetic
clinic documents) and `rag_questions.jsonl` (36 questions: 8 lexical, 14
paraphrase, 6 multi-hop, 8 unanswerable). Each question lists the documents
that contain its answer; unanswerable questions list none.

**Metrics** (document-level, binary relevance, k=5):

- `recall@k` - at least one relevant document in the top k.
- `MRR@k` - reciprocal rank of the first relevant document.
- `nDCG@k` - rank-discounted gain against the ideal ordering.

**Run** (the command writes the artifact under `backend/artifacts/evals/`):

| mode | recall@5 | MRR@5 | nDCG@5 | p50 | p95 |
|---|---|---|---|---|---|
| vector (bge-small) | 1.0000 | 0.9536 | 0.9454 | 175 ms | 266 ms |
| lexical (Postgres FTS) | 0.0714 | 0.0714 | 0.0714 | 74 ms | 110 ms |
| hybrid (RRF) | 1.0000 | 0.9536 | 0.9454 | 293 ms | 486 ms |
| hybrid + cross-encoder | 0.9643 | 0.9464 | 0.9373 | 4320 ms | 5581 ms |

**What this says** - on a small, clean corpus the dense baseline is already
saturated, so hybrid adds no recall and the cross-encoder costs latency for a
small loss. The honest decision is to keep vector/hybrid on by default and
leave reranking off until it is re-measured on the target corpus. Lexical
alone is poor for paraphrases, which is why the hybrid branch exists for exact
terms and identifiers.

**Refusal calibration (v1)** - the best-cosine ranges overlap (answerable
0.638-0.850, unanswerable 0.496-0.718), so no cosine cutoff can separate them.
The evaluator reports `calibration.separated: false`; answer-level grounding
checks are needed rather than a similarity threshold.

### v2: 106 labelled queries, splits and tenant attacks

The v1 set stays as a versioned baseline. v2 adds 39 documents (long guides,
current-vs-superseded policies, a separate tenant-B corpus) and 106 queries
with tuning 9 / validation 19 / test 78. Thresholds and feature choices use
tuning+validation only; test AUROC is reported, not used.

Output: the command writes an artifact under `backend/artifacts/evals/`.

| mode | recall@5 | MRR@5 | nDCG@5 | evidence coverage | foreign hits | p50 |
|---|---|---|---|---|---|---|
| vector | 0.9844 | 0.8984 | 0.9014 | 0.9882 | 0 | 156 ms |
| hybrid | 0.9844 | 0.8984 | 0.9014 | 0.9882 | 0 | 250 ms |
| hybrid + rerank | 0.9844 | **0.9375** | **0.9326** | 0.9765 | 0 | 5337 ms |

- Reranking improves ranking (MRR +0.039, nDCG +0.031), mostly on
  conflicting/superseded policies, but at 34x latency; it stays off by default.
- Cross-tenant: 7 attack queries, zero foreign hits in every mode.
- Refusal: cosine AUROC 0.914 (tuning+validation) / 0.925 (test); tuning
  threshold 0.6468 gives test TPR 0.891 / TNR 0.778. The 0.78 TNR is the reason
  answer-level grounding remains necessary.

## 2. Agent and tool behaviour

`backend/apps/ai/evals/bench.py` runs a deterministic battery against a real
chat model when one is available:

- JSON schema adherence, tool selection and required arguments, a two-step
  booking flow with a confirmation nonce, refusal without evidence, six
  prompt-injection attacks (zero tool calls, zero secret leakage), grounded
  answers with citations, and latency.

The harness records the **actual** model and provider returned by the API,
refuses paid models, distinguishes 429 congestion from a wrong answer, and has
a `--recheck` mode. The committed first run was blocked by the free pool's
shared rate limit; [`docs/evidence/live-model-benchmarks.md`](evidence/live-model-benchmarks.md)
records every
429, empty completion and 404 instead of inventing scores.

## 3. No-show prediction

`backend/ml/no_show/` trains a logistic baseline and a calibrated
HistGradientBoosting candidate on a synthetic dataset with a patient-disjoint
split. Holdout (303 rows, 49 positives):

| model | ROC-AUC | Brier |
|---|---|---|
| prior | 0.500 | 0.1360 |
| logistic regression | 0.5707 | 0.1360 |
| HistGradientBoosting + isotonic | **0.7110** | **0.1268** |

An activation gate rejects a candidate that is worse than the baseline on
either metric; `--check` reproduces the metrics hash. Calibration bins and
feature importance are persisted under `backend/artifacts/ml/` when the command runs.

**Repeated splits and temporal generalisation.** Five split/estimator seeds on
the same dataset give candidate ROC-AUC **0.6866 ± 0.0507** (range
0.6052-0.7385), PR-AUC 0.3543 ± 0.0548, Brier 0.1421 ± 0.0142; the logistic
baseline is 0.6192 ± 0.0368. The activation gate passes 4 of 5 splits. A
chronological holdout (newest 25%) gives ROC-AUC 0.6811, PR-AUC 0.3234, Brier
0.1611 and a much higher predicted positive rate, which is the honest estimate
for temporal drift. Output: the command writes `noshow_repeated_splits.md` under `backend/artifacts/ml/`.
Patient overlap in the grouped split is zero and asserted; the temporal check
allows overlap by construction and discloses it.

## 4. Imaging

The CV pipeline is an explicit intensity/threshold baseline over synthetic
fixtures, not a trained network. It reports sensitivity, specificity,
precision, recall and calibration bins, and states the limitation in the
output directory (`backend/artifacts/cv/`, generated by the command). It is deliberately not presented as a
clinical model.

## Reproduction

```bash
cd backend

# Retrieval, mock embeddings (no download, CI-safe)
python scripts/rag_eval.py --embedding-provider mock --reranker lexical

# Retrieval, real local embeddings (optional: pip install fastembed)
python scripts/rag_eval.py --embedding-provider fastembed \
    --modes vector,lexical,hybrid,hybrid+rerank --label fastembed

# Agent/tool suites (mock; deterministic)
python scripts/ai_evals.py run --suite all --out artifacts/evals/latest.json

# Real-model benchmark (needs OPENROUTER_API_KEY in the environment)
python scripts/ai_model_bench.py run --cases 1 --pace 6

# No-show experiment + reproducibility check
python scripts/train_noshow.py --dataset synthetic --seed 42 --out artifacts/ml --check

pytest -q apps/ai apps/ml
```

## Limits

- Synthetic data only: no real patient records, no clinical claim.
- The retrieval corpus is small and short-document; chunking quality is not
  exercised.
- Real-model results depend on free-tier availability and are re-runnable, not
  guaranteed.
