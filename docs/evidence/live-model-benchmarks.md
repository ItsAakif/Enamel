# Live model benchmarks - what was actually measured

Date: **2026-10-24**.

The repository includes a real-model benchmark harness for the assistant
(`backend/scripts/ai_model_bench.py`, battery in
`backend/apps/ai/evals/bench.py`). This note records the first live run
honestly: the models were not available, so **no live quality score is
claimed**. The offline evaluation results are separate and are documented in
[`docs/AI-EVALUATION.md`](../AI-EVALUATION.md).

## What the harness does

- Discovers the model catalogue, filters to free models that advertise tool
  calling and structured output, and refuses paid models.
- Records the **actual** model and provider the API returned (not what was
  configured), plus usage, finish reason, latency and retry attempts.
- Treats 429 congestion and empty completions as first-class outcomes rather
  than failures that fake a score.
- Supports request budgets, pacing, a consecutive-429 circuit breaker, and a
  `--recheck` mode that re-runs only the models marked unavailable.

The API key is read from the local environment; it is never printed or stored
in an artifact.

## Command shape

The key was loaded from local environment configuration (never in the command
line, never printed):

```bash
cd backend
python scripts/ai_model_bench.py run \
  --models "google/gemma-4-26b-a4b-it:free,google/gemma-4-31b-it:free" \
  --cases 1 --case-limit 8 --pace 6 --max-consecutive-429 3 --attempts 2 \
  --max-requests 40
```

## What happened

Both pinned free models were consumed their attempt budget against upstream
congestion and were marked **unavailable at run time** after three consecutive
429s. Every completed request failed with a rate limit, not a wrong answer:

```text
requests=12/40
google/gemma-4-26b-a4b-it:free: overall=0.00 p50=0ms errors=3 429s=3 unavail=5 -> RUNTIME-UNAVAILABLE
google/gemma-4-31b-it:free:     overall=0.00 p50=0ms errors=3 429s=3 unavail=5 -> RUNTIME-UNAVAILABLE
```

The 429 response body named its source:

```json
{
  "limit_source": "upstream_provider_shared_pool",
  "raw": "google/gemma-4-26b-a4b-it:free is temporarily rate-limited upstream.
          Please retry shortly, or add your own key to accumulate your rate limits"
}
```

A bounded recheck later the same day produced the same result for both pinned
models, plus one additional free model that answered 200 with empty content.
No scores were produced on either attempt.

## Probe matrix (single calls, spaced apart)

All calls were to free models. Several returned **200 with empty content**,
which is why the harness records empty completions explicitly:

| Model | Result |
|---|---|
| `google/gemma-4-26b-a4b-it:free` | early probe 200 with content ("OK."); later 429s |
| `google/gemma-4-31b-it:free` | early probe 200 with content; later 429s |
| `nvidia/nemotron-3-super-120b-a12b:free` | 404 (id no longer served) |
| `xingchen-agi/xing4.0-29b-a4b:free` | 429; later 200 with empty content |
| `dots-studio/dots-3-note-preview:free` | 200, empty content |
| `recursive/flint-v1.0:free` | 200, empty content |
| `nvidia/nemotron-3.5-lightning:free` | 200, empty content |
| `liquid/lfm-2.5-2.6b:free` | 200, empty content |
| `openrouter/free` (dynamic router) | 200, empty content; excluded from the benchmark |
| `poolside/laguna-s-2.1:free` | 200 with content, but advertises no tools/structured output |

## Conclusion

- The pinned pair is marked **`unverified-under-load`** (`scores_valid: false`)
  in `backend/apps/ai/evals/configs/free-models.json`.
- The zero rates recorded here are **not model failures**: the models never
  received enough fair turns to measure anything.
- **No live quality score is claimed** for any model in this run. What is
  demonstrated is the measurement machinery: provider path, actual
  model/provider capture, paid-model refusal, and honest handling of
  congestion, empty content and retired ids.

## Reproduce

Re-run when the free pool recovers; it re-runs only models marked unavailable
and merges the new evidence:

```bash
cd backend
python scripts/ai_model_bench.py run --recheck --out artifacts/evals
```

The run writes artifacts under `backend/artifacts/evals/` when reproduced;
those generated files are not part of the clean release. The free-model pin is
`backend/apps/ai/evals/configs/free-models.json`.
The offline, deterministic evaluation suite - the numbers that are actually
claimed - is documented in [`docs/AI-EVALUATION.md`](../AI-EVALUATION.md).
