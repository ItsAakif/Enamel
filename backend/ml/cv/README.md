# CV pipeline (`ml/cv`)

Deterministic, dependency-light computer-vision pipeline used by the imaging
assistive-analysis feature (T129). It has **no PyTorch dependency** and no
clinical-validity claim.

## Pieces

| Module | Responsibility |
|---|---|
| `preprocess.py` | Load the YAML config and apply the fixed resize + normalisation transform. |
| `infer.py` | `predict(image_array, meta, config) -> list[Finding]`; the transparent intensity/threshold baseline. |
| `evaluate.py` | Fixture metrics (sensitivity, specificity, precision, recall, confusion matrix, calibration bins) and the Markdown report. |
| `fixtures.py` | Deterministic synthetic image + label generation. |
| `configs/fixture.yaml` | The preprocessing + inference contract recorded on every inference job. |

## Determinism

`preprocess` and `predict` are pure functions of `(image, config)`. The output
hash is `sha256(canonical_json({config, model, findings}))`, so two runs over the
same study with the same config produce the same findings JSON and the same
hash. The tests assert this.

## Findings are advisory

Every `Finding` carries `status="suggested"`. Findings are never written to the
chart automatically; a human must accept them (that workflow belongs to the
charting domain, not here).

## Evaluation

```powershell
cd backend
..\.venv\Scripts\python.exe scripts\cv_evaluate.py --config ml/cv/configs/fixture.yaml --out artifacts/cv
```

Writes `metrics.json` and `report.md` (with a limitations section) under the
output directory.

## Limitations

The default model is a threshold heuristic validated only on synthetic fixtures.
It is not a diagnostic device, it has not been validated on real dental data, and
its metrics must never be presented as clinical performance.
