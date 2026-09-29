# Preparing a public alpha release

This is a release acceptance guide, not a release announcement or a second status
log. [ROADMAP.md](ROADMAP.md) owns feature scope; [SESSION_HANDOFF.md](SESSION_HANDOFF.md)
owns current verification results and unresolved failures. A checked-in version
number or passing local tests alone does not establish release readiness.

The September 29 release publishes the working **experimental alpha**, with
broader development paused. That narrower source release may
document unfinished product-acceptance work; it must not claim the time-saving
or independently validated first-use outcome below. Privacy review, functioning
advertised features, reproducible verification and honest limitations remain
required. Publishing source does not mark these outstanding criteria complete.

## Define the release promise

The first public alpha should offer one clear outcome: a new user can create a
reviewed career profile through Codex, prepare a grounded PDF and answers for a
job, then resume their work. Configured public-board discovery and draft queues
extend that outcome. Final application submission remains a user action.

Describe each supported source and input format explicitly. Do not advertise
market-wide coverage, semantic rewriting, browser filling, a desktop GUI, or
native Windows support until their implementation and verification exist.
Keep future product design separate from available behavior.

## Verify the first-use experience

Use fictional data in a clean checkout and a new private runtime outside Git.
Have a reviewer follow only the [README](../README.md),
[Codex workflow](CODEX_WORKFLOW.md), and [quickstart](QUICKSTART.md).

| Release question | Evidence to record in the live checkpoint |
|---|---|
| Can a new user start without understanding CLI syntax? | A complete conversational run from setup to one reviewed application package, including dependency guidance and useful failure messages |
| Does onboarding account for the entire supplied career history? | An inventory covering every section, explicit unparsed/withheld content, and user-controlled retention; no unexplained starter subset |
| Can a user start without a CV? | A few optional questions at a time; only exact chosen answers retained with user-statement origin, pending review and separate approval; skip/stop preserves retained facts without storing a transcript |
| Are supported document formats honest? | A successful example for each advertised format and a clear recovery path when extraction cannot preserve the source |
| Can a user pause review and continue later? | Pending facts stay pending; a resumed session shows remaining decisions without losing work or approving it implicitly |
| Does job discovery explain its reach? | Per-source coverage, truncation and failures are visible; partial results remain usable; manual job capture works for uncovered sources |
| Can discovery start without company URLs or a profile? | Explicit role/location plan followed by actual authorized source finding, observed-link validation and feed preview; no profile setup detour, guessed board tokens or search snippets treated as snapshots; unavailable browsing and deferred leads remain visible |
| Are materials useful and faithful? | Review every PDF page, extracted text, questionnaire blockers, and links to approved evidence; no unsupported candidate facts |
| Does it save the user's time? | Measure active user minutes and required handoffs for the same jobs with and without the batch workflow; do not infer savings from elapsed agent time |
| Can the user recover their work? | Resume an interrupted run and restore an encrypted profile into a new home without duplicate jobs, approvals, or history |

The onboarding and active-time rows are product acceptance criteria. They are
not satisfied merely by adding help text or passing unrelated unit tests.
For statement-origin intake, record source and fresh installed CLI results,
unchanged resume compatibility, and a reviewed stop/resume scenario before
making the no-CV release claim. Current verification belongs in the live
checkpoint; a passed feature check alone does not establish release readiness.

## Verify the release artifact

Run the applicable [development gates](DEVELOPMENT.md#canonical-commands) on the
exact candidate commit and record commands, outcomes, platform, and dependency
versions in the live checkpoint. Required PDF and encryption gates must actually
exercise their dependencies; skips do not verify those features.

- Pass the full synthetic suite, real-PDF and encryption gates, and the complete
  lifecycle/search/daily pilots for the advertised scope.
- Build and test a fresh installed wheel using the documented isolated package
  gate. Verify the bundled migrations, entry point, optional extras, and help.
- Complete the advertised hosted OS/Python matrix. Local macOS verification
  does not establish Linux, Python 3.13, or Windows support.
- Review the complete diff and built artifact for personal data, credentials,
  generated private files, debug output, and undocumented dependencies.
- Recheck package version, license, repository URLs, included files, and every
  local documentation link. Use a confirmed distribution channel; do not assume
  a checkout package is already available on a public package index.

## Keep the public repository approachable

The README is the entry point. Detailed operational contracts belong in
[REFERENCE.md](REFERENCE.md); contributor commands belong in
[DEVELOPMENT.md](DEVELOPMENT.md); design decisions belong in [ADRs](adr/).
Do not grow the README into another implementation log.

Before publishing, confirm that:

- [CONTRIBUTING.md](../CONTRIBUTING.md) describes the actual development workflow
  and [SECURITY.md](../SECURITY.md) provides a usable private reporting route.
- The release notes distinguish supported behavior, known limitations, and
  migration or compatibility changes, with links to actual verification.
- The first-use demo uses conspicuously fictional people and organizations and
  shows the approval and final-submission boundaries.
- Troubleshooting covers interpreter selection, PDF dependencies, private-path
  permissions, unsupported inputs, partial discovery, and capacity limits.
- Storage documentation explains external source/export ownership, backup scope,
  manual retention, and the current in-memory backup/restore cost. See
  [ADR 0011](adr/0011-supported-profile-capacity.md) for measured capacity limits.

Full historical storage conversion, automatic retention, broader source coverage,
and large material-history performance remain separate roadmap work. An alpha
may have explicit limits; it must make the chosen scope usable and truthful.
