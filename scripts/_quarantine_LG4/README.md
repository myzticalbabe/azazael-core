# Quarantine — LG4 (2026-10-28)

`audit_loop_metadata.py` moved here because it was an orphan schema: zero
importers, zero data files, zero consumers of `filter_for_recovery()`. It was
untracked, so this move (not a delete) preserves it for revival.

Revive iff: an audit-loop entry source exists (loop registry with heartbeats)
and a consumer calls `filter_for_recovery()` / `enrich_audit_loops()`. Until
then it stays out of `scripts/` so no lane mistakes it for live ladder code.
The metadata-schemas index entry for the audit-loop schema stands as spec.
