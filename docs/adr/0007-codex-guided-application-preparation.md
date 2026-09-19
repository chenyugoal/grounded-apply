# ADR 0007: Codex-guided application preparation

- **Status:** Accepted; implemented and locally verified (see SESSION_HANDOFF.md)
- **Date:** 2026-09-18

The next milestone makes the existing local pilot usable through conversation
for an industry research or engineering job search. Codex operates the typed
CLI; the user reviews career facts and finished materials, chooses where to
apply, and submits. The milestone measures a complete reusable workflow, not
offers, response rates, autonomous application volume, or a release on every OS.

## Bounded outcome

1. A repository skill guides onboarding, job comparison, application preparation,
   and resumption using the implemented commands. No user needs to manage IDs,
   review tokens, JSON manifests, or command syntax by hand.
2. A read-only `brief` command summarizes saved jobs, application stages,
   material readiness, evidence gaps, and explicit next actions. Workflow stage
   determines action ordering; keyword overlap never becomes a fit score.
3. An on-demand response-check suggestion uses a disclosed, configurable elapsed
   time since the recorded application event. It neither schedules reminders nor
   sends messages, and it does not invent an employer deadline or response.
4. The launcher selects an explicitly configured Python or repository virtualenv
   and reports unsupported interpreters clearly. Setup instructions are verified.
5. A fictional graduating researcher can reuse one approved profile across
   research and engineering jobs, prepare a real PDF and grounded answers, pause
   for approval/submission, and resume with a useful briefing. Required unknowns,
   stale evidence, injection text, and sensitive answers retain the existing gates.

## Product boundary

The first usable version selects and orders approved wording. Codex can explain
relevance and suggest improvements, but new wording is an unapproved proposal
until explicitly reviewed and imported through the existing evidence contract.
It cannot enter a verified artifact merely because Codex wrote it. Preserve
expected graduation dates, publication status, and the difference between
academic research, coursework, prototypes, and production work.

The user supplies job text and URL. Codex may use available authorized browsing
to help the user research public openings, but live fetching, discovery feeds,
semantic fit scoring, PDF/DOCX ingestion, browser filling, outbound messages,
and autonomous submission are outside this milestone. No private profile or
search preferences enter repository docs or fixtures. Facts still require
explicit review; approving a workflow is not blanket approval of its contents.

## Upgrade path and acceptance

The skill composes existing services. The briefing reads validated projections
inside a consistent SQLite snapshot; no second database or mutable dashboard
state is introduced. Later discovery, MCP, interfaces, and language-model
adapters can call the same services and preserve these contracts.

Completion requires focused policy/privacy tests, the full repository gate,
required PDF and encryption gates, an extended synthetic CLI pilot, an installed
pilot, skill validation and a realistic skill forward-test. A successful tool
test is not evidence of guaranteed conversational quality or hiring outcomes.
Record exact results and any environment limits in SESSION_HANDOFF.md.
