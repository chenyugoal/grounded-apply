# Contributing

Grounded Apply is in an early truth-layer phase. Before contributing, read
[AGENTS.md](AGENTS.md), the live [session handoff](docs/SESSION_HANDOFF.md), and
the [development guide](docs/DEVELOPMENT.md).

Keep each change bounded to one verifiable outcome. Add synthetic tests for the
failure mode as well as the happy path, run `./scripts/check`, and update the
live handoff when your work changes the repository's actual status. Never add
real resumes, application answers, job-search records, credentials, raw browser
traces, or other personal data.

Contributions must preserve the evidence contract: factual application content
comes only from approved claims or named deterministic derivations; missing or
unsafe information becomes `NeedInfo` or `Contradiction`. Derivation metadata
alone is not trusted; only a registered evaluator may authorize a derived value.

By contributing, you certify the [Developer Certificate of Origin 1.1](https://developercertificate.org/).
Sign commits with `git commit -s` to add the required `Signed-off-by` line.
