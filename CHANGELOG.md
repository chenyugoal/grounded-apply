# Release notes

## 0.1.0a0 — Experimental alpha candidate

Grounded Apply provides a supervised, Codex-guided workflow for building a
reviewed career profile, preparing evidence-backed resumes and career answers,
and recording applications submitted by the user. Broader feature development
is paused. This is a source-checkout release candidate, not a package published
to PyPI. Publication and exact verification status belong in the
[live checkpoint](docs/SESSION_HANDOFF.md).

### Included

- Local text, selectable-text PDF and static LaTeX intake, complete line
  inventory, explicit publication grouping, guided questions and paged review.
- Approved-fact reuse, evidence comparisons, validated PDF resumes and career
  answers. Resume preparation selects and orders approved wording.
- Configured Greenhouse, Ashby, Lever and Workable discovery, bounded Netflix
  discovery, saved searches, resumable batches and externally triggered daily runs.
- Review exports, manual application history and next-action briefings.
- Optional encrypted database backup/restore, support export and confirmed
  whole-portable-home deletion.

### Limits

- Verification is local to macOS/Python 3.12.14 unless the checkpoint records
  additional results. The full hosted platform matrix is still unverified.
- There is no demonstrated reduction in active user effort and no completed
  independent human first-use acceptance trial. Setup and review can be substantial.
- No browser filling, autonomous submission, semantic rewriting, GUI, email
  integration, OCR or DOCX intake. Discovery is bounded and incomplete.
- Extracted text and evidence selection require human review. Unknown facts and
  required sensitive answers remain blockers; they are not guessed.
- The supported database limit is 256 MiB; encrypted archives are bounded to
  384 MiB. Near-capacity backup/restore used about 2.3 GiB of process memory.
  Automatic retention, document sharing and larger-history scaling are unfinished.
- Filesystem identity checks are sampled, not protection against a malicious
  process running as the same user. Local storage does not make Codex conversations
  local. See the [reference](docs/REFERENCE.md) and [security policy](SECURITY.md).

### Existing profiles

Schema remains version 7; this wrap-up adds no migration. Original import and
material histories retain their versioned behavior. New conversational intake
uses extractor 4 explicitly while the CLI default remains 2. Keep personal
profiles outside Git and use the documented backup and upgrade procedures.
