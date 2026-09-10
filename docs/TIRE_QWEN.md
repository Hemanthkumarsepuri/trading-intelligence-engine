# TIRE — Qwen 2.5 (explanation only)

Qwen never decides research state, direction, BUY/SELL, size, confidence,
probability, or targets.

## Runtime

- Adapter: `app/llm/qwen_narrative.py`
- Default: LM Studio `http://127.0.0.1:1234/v1`, model `qwen2.5-coder-7b-instruct`
- Health: `GET /api/qwen/health` (fail-open)
- Explain: `POST /api/research/explain` with `{facts, source_evidence_ids}`

The application works fully when Qwen is disabled, timed out, or rejected.

## Allowed JSON keys

`summary`, `developing_observation`, `supporting_evidence`,
`conflicting_evidence`, `missing_evidence`, `confirmation_condition`,
`invalidation_condition`, `data_quality_note`

Legacy keys (`key_observations`, `supporting_facts`, …) are still accepted
and mapped. They are not shown as a chatbot transcript.

## Rejected

Top-level keys including `recommendation`, `buy`, `sell`, `probability`,
`confidence`, `expected_return`, `target_price`, `stop_loss`, `opportunity_score`,
`prediction`, `smart_money`, `direction`, `position_size`.

Text claims such as "buy now", "target price", "% confidence", or
"institutional buyers are accumulating" are `unsupported claim` and discarded.

## UI

Optional **Explain simply** on the symbol research page. Qwen is never
auto-called. Fallback copy: "AI explanation unavailable — showing TIRE's
evidence summary." Always: "AI explanation based on TIRE evidence."

## Timeouts

- Health: `GET {base}/models` with `QWEN_HEALTH_TIMEOUT_SECONDS` (default 2s).
  Never a generation request. Statuses: `QWEN_READY`, `QWEN_UNAVAILABLE`,
  `QWEN_TIMEOUT`, `QWEN_NOT_CONFIGURED`.
- Explain: `QWEN_TIMEOUT_SECONDS` (default 8s). On timeout, deterministic
  TIRE text is shown immediately.

Local Qwen 2.5 via LM Studio is optional. If the model is too slow for
interactive use, TIRE remains fully usable without it.

