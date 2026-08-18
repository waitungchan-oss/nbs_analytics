# Context Agent Evidence Memory

Evidence consumed by Context Agent must remain bounded, fingerprinted, and
traceable to canonical documents, tests, Hermes evidence, or Git identity.
Unavailable, stale, malformed, or policy-denied Memory Hub results are not
converted into context. The safe result is canonical-only context with no
`memoryHints` enrichment.

This source describes the evidence boundary only; it does not authorize any
runtime write, approval, dispatch, baseline change, or data mutation.
