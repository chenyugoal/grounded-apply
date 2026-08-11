# Security policy

Grounded Apply is pre-release software that handles highly sensitive personal
information. The current Phase 0 scaffold is not ready for real candidate data.

## Reporting a vulnerability

Do not open a public issue that contains a vulnerability exploit, candidate
data, credentials, private artifacts, database contents, browser traces, or
unredacted logs. Use the repository host's private security-advisory channel
when one is configured. Until then, share only a redacted description with the
maintainer through a private channel and wait for a secure artifact-transfer
method.

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
