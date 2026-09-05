# ADR 0005: Retire approved claims without rewriting their provenance

- **Status:** Accepted for implementation
- **Date:** 2026-09-04

Approved imported claims and their original approve/reject audit remain immutable
provenance. A separate append-only retirement record makes an approved claim
unusable for subsequent resolution. Public effective-profile and resolution
services project it as withdrawn (or superseded when a replacement is supplied).
The original import and approval retries continue to verify their original data.
The retirement audit is revalidated before every effective projection.

Users correct pending content by rejecting it and importing a corrected proposal.
They correct approved content by importing and approving the replacement, then
retiring the original with that replacement ID. A replacement must be a distinct,
approved, active claim of the same type, subject, and scope. An optional replacement
never approves new content. Retirement without a replacement is withdrawal.

Preview reads and validates the claim, approval audit, optional replacement, and
current retirement state. Its digest binds those records, actor, and hashed
idempotency key. Confirmation revalidates the preview and atomically records one
retirement plus a content-free workflow. Exact retries verify both immutable
records. Stale or changed requests fail closed. A new preview cannot retire an
already retired claim. Retirement cannot be reversed by editing the database.

The initial contradiction workflow is explicit human selection of which approved
facts to retire, not automatic semantic adjudication. Resolution continues to
return structured contradictions for incompatible active values. No model decides
truth or invents a replacement. Later semantic assistance may propose candidates
for the same human-controlled contract.
