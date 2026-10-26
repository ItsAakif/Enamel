# AI engineering evidence

Every claim below maps to source files, a reproducible command and a recorded
outcome. Nothing is claimed that the repository does not measure.

## 1. LLM system and provider abstraction

| Claim | Source | Evidence |
|---|---|---|
| Provider abstraction with a deterministic mock and a real OpenRouter adapter | `backend/apps/ai/providers/{base,mock,openrouter,factory}.py` | `backend/apps/ai/tests/test_provider.py`, `test_openrouter_extensions.py` (mock transport, no live sockets) |
| Tool calling, JSON-schema output, actual model/provider capture | `providers/openrouter.py`, `services/structured.py` | committed tests; live probe returned `google/gemma-4-26b-a4b-it:free` / provider Google AI Studio |
| Paid-model guard and free-only default | `providers/openrouter.py`, `config/settings/base.py` (`AI_ALLOW_PAID`) | `test_openrouter_extensions.py` |
| 429/5xx retries with `Retry-After`; no silent model substitution | `providers/openrouter.py`, `evals/bench.py` | retry tests; live 429 evidence in [`docs/evidence/live-model-benchmarks.md`](evidence/live-model-benchmarks.md) |

**Live-model status: blocked.** The free pool returned upstream 429s
(`limit_source: upstream_provider_shared_pool`) for the pinned pair on
2026-10-24; several other free models returned empty content and one 404. The
pinned pair is marked `unverified-under-load`; no accuracy score is claimed.
Re-run: `python scripts/ai_model_bench.py run --recheck --out artifacts/evals`.

## 2. Agent trajectory and tool safety

| Claim | Source | Evidence |
|---|---|---|
| Confirmation nonces bound to a call, single-use | `apps/ai/tools/confirmation.py`, `registry.py` | `tests/test_tools.py` (binding, replay) |
| Identity, permission, tenant and argument gates | `registry.py`, `apps/core/permissions.py` | `test_tools.py` (identity, permission, extra args, cross-clinic) |
| Idempotent booking and history-preserving reschedule | `tools/booking.py`, `tools/reschedule.py`, `scheduling/services/appointments.py` | `test_agent_trajectory.py` (duplicate returns existing; reschedule cancels old + creates successor; one active row) |
| Conflicting booking writes nothing | `scheduling/services/appointments.py` (exclusion constraints) | `test_agent_trajectory.py` (`slot_conflict`, row count unchanged) |
| Unexpected tool failure rolls back and escalates, retry succeeds | `tools/registry.py` | `test_agent_trajectory.py` (`tool_error`, call escalated, no row; retry books) |
| Injected retrieved content cannot create an appointment | `rag/answer.py` (context is data) | `test_agent_trajectory.py` |

Model-dependent trajectory cases (multi-step, ambiguity, unavailable slots,
injection in context, recovery) are implemented in the benchmark battery
(`evals/bench.py`); live results are blocked with the model pool.

## 3. Retrieval

| Claim | Source | Evidence |
|---|---|---|
| Tenant filter before ranking; zero cross-tenant hits | `rag/retrieve.py`, `rag/lexical.py` | `test_retrieval.py`; v2 tenant attack set (7 queries, 0 foreign hits) |
| Real local embeddings (384d) independent of chat | `providers/embeddings.py` | `artifacts/evals/rag-v2-...-fastembed.json` |
| Hybrid RRF + optional cross-encoder | `rag/retrieve.py`, `rag/rerank.py` | v1/v2 artifacts |
| HNSW + GIN indexes | `ai/migrations/0004_rag_indexes.py` | migration applied; `pg_policies`/index checks in smoke |
| Labelled benchmark v1 (36 q) and v2 (106 q, splits) | `evals/datasets/rag_*`, `evals/rag_eval*.py` | artifacts below |

**v2 held-out test (78 questions, bge-small):**

| mode | recall@5 | MRR@5 | nDCG@5 | evidence coverage | foreign hits | p50 |
|---|---|---|---|---|---|---|
| vector | 0.9844 | 0.8984 | 0.9014 | 0.9882 | 0 | 156 ms |
| hybrid | 0.9844 | 0.8984 | 0.9014 | 0.9882 | 0 | 250 ms |
| hybrid+rerank | 0.9844 | 0.9375 | 0.9326 | 0.9765 | 0 | 5337 ms |

Findings: hybrid adds no quality lift; reranking improves ranking mostly on
superseded policies at 34x latency and stays off by default; refusal cosine
AUROC 0.914 (tuning+validation) / 0.925 (test) with test TPR 0.891 / TNR 0.778.

## 4. No-show ML

| Claim | Source | Evidence |
|---|---|---|
| Leakage-safe patient-disjoint split | `ml/no_show/train.py` | `split.patient_overlap = 0`, `assert_disjoint` |
| Booking-time-only features | `ml/no_show/features.py` | documented reasons; prior counters strictly earlier (tests) |
| Calibrated candidate vs baselines + activation gate | `ml/no_show/train.py`, `evaluate.py` | `artifacts/ml/noshow_metrics.json` |
| Reproducibility | `scripts/train_noshow.py --check` | metrics hash reproduced exactly |
| Uncertainty and temporal generalisation | `scripts/noshow_repeated.py` | `artifacts/ml/noshow_repeated_splits.md` |

Primary grouped holdout: candidate ROC-AUC 0.7110 / Brier 0.1268 vs logistic
0.5707 / 0.1360; gate activated. Repeated splits: 0.6866 ± 0.0507, gate 4/5.
Temporal holdout: AUC 0.6811, Brier 0.1611. Synthetic data only.

## 5. Computer vision

**Decision: no trained model.** The repository ships 12 synthetic 48x48 fixture
images (`fixtures/cv/README.md`, licence: project-internal synthetic, no patient
data). No licensed real radiograph dataset is available in the environment, and
training on 12 synthetic blobs would produce a meaningless, easily misread
result. The intensity/threshold baseline remains, with metrics explicitly
labelled fixture-only:

- `artifacts/cv/metrics.json`: 12 images, sensitivity/specificity 1.0 on the
  synthetic blobs, with a limitations string that states these do not estimate
  real-patient performance.
- Experiment protocol for a real attempt: licensed dataset with caries/
  radiolucency annotations, patient-level train/validation/test split,
  Dice/sensitivity/specificity at a fixed operating point, external validation,
  calibration, error analysis, provenance and regulatory review. Do not present
  any output as a diagnosis.

## 6. Platform and CI

| Claim | Evidence |
|---|---|
| Backend suite | `pytest -q` (see final report for the count) |
| Lint | `ruff check .` |
| RLS proofs, docs and claim guards | `scripts/check_rls.py`, `check_docs.py`, `check_claims.py` |
| CI without a live API key | `.github/workflows/ci.yml` (mock evals + retrieval benchmarks) |
| Live evaluation is separate and budgeted | `.github/workflows/live-eval.yml` (`workflow_dispatch`, request cap, secret) |

## 7. Résumé bullets supported by the evidence

- Built a tenant-scoped RAG service on PostgreSQL/pgvector with hybrid RRF
  retrieval and an optional cross-encoder; measured **0.984 recall@5** and
  **0.988 evidence coverage** on a 106-query labelled benchmark, with **zero
  cross-tenant leaks** across 7 attack queries.
- Designed an LLM tool layer with confirmation nonces, identity and permission
  gates, idempotent booking and rollback-to-escalation; verified **server-side
  outcomes** (one appointment, no partial writes) with 5 deterministic
  trajectory tests plus the existing registry suite.
- Built a model-evaluation harness that pins free OpenRouter models, records the
  actual provider/model, refuses paid models, and treats 429/empty completions
  as first-class outcomes; documented a real provider-quota blocker instead of
  inventing scores.
- Trained and calibrated a no-show model (HistGradientBoosting + isotonic) with
  patient-disjoint splits: **ROC-AUC 0.711 vs 0.571** baseline, repeated-split
  mean **0.687 ± 0.051**, temporal **0.681**, activation gate rejecting 1 of 5
  splits.
- Added CI covering backend, frontend, security/documentation guards, mock
  evaluations and both retrieval benchmarks, plus a separately triggered,
  budgeted live-model workflow.

## 8. Claims to avoid

- "Fine-tuned an LLM" - no fine-tuning was performed.
- "Trained a computer-vision model" - the pipeline is an intensity heuristic.
- "Measured real-model accuracy" - the live free pool was rate-limited; the pin
  is provisional.
- "Hybrid/reranking improves retrieval" - no measured lift on this corpus;
  reranking improves ranking at 34x latency.
- Any clinical, regulatory or compliance validation claim.
