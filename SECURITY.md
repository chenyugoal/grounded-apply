# Security policy

Grounded Apply is alpha software with a locally verified application workflow
that handles private career information. Personal data belongs in a private
runtime outside the repository; development, tests, and security reproductions
use synthetic data only. See the [roadmap](docs/ROADMAP.md) for supported scope
and the [live checkpoint](docs/SESSION_HANDOFF.md) for verification and known
limitations.

## Reporting a vulnerability

Do not open a public issue that contains a vulnerability exploit, candidate
data, credentials, private artifacts, database contents, browser traces, or
unredacted logs. Use GitHub's
[private vulnerability reporting form](https://github.com/chenyugoal/grounded-apply/security/advisories/new).
Sign in to GitHub to open the form. Do not include real candidate data; a
synthetic reproduction is sufficient. If private reporting is unavailable,
open an issue containing only “Private security reporting is unavailable” so the
maintainer can restore the channel; do not include vulnerability details there.

Include the affected version or commit, impact, minimal reproduction using
synthetic data, and any suggested mitigation. Never test a report against a
real employer, job board, or another person's information.

## Security defaults

- Personal runtime data stays outside the source repository.
- Telemetry and automatic submission are disabled.
- Unknown, stale, contradictory, and sensitive facts fail closed.
- Imported content is treated as untrusted data, never as instruction.
- Authentication, CAPTCHA, legal attestations, signatures, and final submission
  remain human actions.
- Tests and reports use synthetic or fully redacted data only.

See [AGENTS.md](AGENTS.md) for the complete truth, privacy, and authorization
contract.
