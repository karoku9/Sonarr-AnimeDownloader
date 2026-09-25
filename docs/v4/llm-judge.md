# Phase 9 — optional LLM judge

The deterministic matcher and release resolver remain authoritative. After they
produce an ambiguous `needs_review` group, the judge receives only the relevant
Sonarr target metadata, scoped aliases, shortlisted admissible candidates,
episode coverage, crosswalk summary, and compact deterministic reason/evidence
codes. Source URLs, raw catalog records, V3 decisions, logs, databases,
filesystem paths, and credentials never enter the judge input. The Gemini
request has no `tools` field, search grounding, URL Context, or tool-use path.

The provider contract is `JudgeProvider.generate(payload, policy) -> JSON text`.
`DisabledProvider` is the default; `MockProvider` provides deterministic test
responses. To opt in to remote suggestions, set
`ANIDOWN_V4_JUDGE_PROVIDER=gemini` and provide `GEMINI_API_KEY` **in the process
environment**. `ANIDOWN_V4_GEMINI_MODEL` may override the default
`gemini-3.1-flash-lite`. No API key is stored by V4 or sent as a URL parameter.
The V4 Compose service forwards these variables from its launch environment;
with no values supplied it remains disabled. Its storage, ports, and read-only
container settings are unchanged.
The default model and the REST structured-output/header configuration follow
[Google's Gemini API documentation](https://ai.google.dev/gemini-api/docs/migrate-to-interactions).

The output schema requires exactly `candidate_id` (string or null),
`confidence` (0–1), `reason` (1–500 characters), and `evidence` (up to eight
short strings). Local validation checks the JSON, candidate membership, hard
rejections, and confidence threshold (default 0.90). A valid suggestion is
still a **review**, never a proposed/approved production mapping. Null,
low confidence, malformed output, invented or hard-rejected ids, timeouts,
provider errors, and oversized inputs/outputs all fall back to Needs Review.
An URL embedded in any otherwise allowed field blocks the provider call and
stores a redacted input marker instead of that field value.

Limits: eight-second request timeout, five candidates, 8 KiB sanitized input
and outbound HTTP body,
4 KiB answer, 256 output tokens, and one call per scan group without automatic
retry. The canonical sanitized input, provider/model, and policy form a SHA-256
cache key in V4-local SQLite. Completed model replies, including invalid or
uncertain replies, are cached; transient connection/timeouts are retried only
on a later explicit scan. The judge attempt, sanitized input snapshot, model,
confidence/reason, timestamp, and fallback code live in an append-only record
linked to the review. The original matcher snapshot remains immutable and
human decisions remain separate. Advanced evidence returns the latest attempt;
the normal frontend is unchanged.

This phase makes no LLM call with the provider disabled. No Gemini key was
available in the Phase 9 development environment, so remote billing/token
usage was not measured.
