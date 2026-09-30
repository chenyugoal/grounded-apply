# Grounded Apply

![Grounded Apply product vision: with Codex, turn your experience into approved career memory, discover and compare jobs, prepare evidence-linked applications, and track outcomes. You review and make the final submission; missing information returns to you for an approved memory update.](product-vision.png)

*[Product vision](docs/PRODUCT_VISION.md): the complete experience we envision.
The current alpha's capabilities are described below.*

**A local-first job-search assistant that keeps your applications grounded in facts.**

Build a career profile you can reuse, find roles on public company boards, and
prepare resumes and answers from facts you approve. Work through conversation
with Codex: you decide what is remembered, review each application, and make the
final submission.

**Status: experimental alpha · broader development paused.** The supervised local
workflow is tested on macOS with Python 3.12. Expect setup and review work;
independent first-use acceptance and time savings have not been measured.
The [roadmap](docs/ROADMAP.md) records unfinished work without a delivery schedule.

## What you can do

- **Build your career memory.** Bring your current resume/CV as a selectable-text
  PDF, paste text, or answer guided questions. Choose what to retain, then review
  and approve facts individually or in batches.
- **Discover opportunities.** Start with role and location preferences, find
  public company boards, and search supported Greenhouse, Ashby, Lever, Workable
  and Netflix sources with visible coverage gaps.
- **Compare requirements with evidence.** See which approved facts relate to a
  role and where information is missing.
- **Prepare application materials.** Select and order approved wording into
  traceable PDF resumes and career answers, for one job or a batch.
- **Pick up where you left off.** Resume saved searches and draft queues, review
  new materials, and record the progress of applications you submit.

Missing or unsupported facts become questions. The tool does not invent
achievements or predict hiring outcomes.

## Get started with Codex

Clone the repository and open its folder in Codex:

```bash
git clone https://github.com/chenyugoal/grounded-apply.git
cd grounded-apply
```

Then ask:

> Use $grounded-apply to help me set up my career profile. Show me the facts for
> review, then help me prepare an application for a role I choose.

Codex follows the [first-session workflow](docs/CODEX_WORKFLOW.md) and handles
identifiers, command syntax, and review steps. Keep your resume and personal data
outside this repository. You choose which facts may be stored and approve them
before they can support generated materials.

The base CLI needs **Python 3.12+** and a POSIX shell. The CLI itself needs no
model API key; using Codex requires your own Codex access. No package installation
is needed to inspect the CLI:

```bash
./scripts/gapply --help
./scripts/gapply doctor --materials --json
```

The prerequisite check reports presence only and needs no profile. PDF input
needs `pypdf`; PDF output also needs local TeX. Follow the
[setup guide](docs/QUICKSTART.md#start) for materials and optional encrypted-backup
dependencies.

The complete PDF workflow is verified locally on macOS/Python 3.12.14. Hosted
checks cover the base CLI, encryption and installed packages on macOS 15 and
Ubuntu 24.04 with Python 3.12/3.13; they do not verify the complete PDF workflow.
Native Windows support is unfinished. See the [release notes](CHANGELOG.md).

Already have a profile? Ask Codex, “What should I work on next in my job search?”
For recurring discovery, use the [daily setup guide](docs/DAILY_QUICKSTART.md).

## Current scope and roadmap

The illustration brings the full vision together. Here is what the alpha
supports today and what remains unfinished:

| Area | Supported today | Not yet available |
|---|---|---|
| Career memory | Text, selectable-text PDF and static LaTeX intake; reviewed facts; optional guided questions | OCR, DOCX, richer interpretation and saved interview progress |
| Discovery | Configured Greenhouse, Ashby, Lever and Workable boards; bounded Netflix discovery; Codex-assisted source finding | Broader coverage, more connectors and cross-source deduplication |
| Matching | Requirement-to-evidence comparisons and explicit gaps | Semantic ranking and richer employer research |
| Materials | Select and order approved wording; validated PDF resumes; grounded career answers; batches | Verified semantic rewriting, more templates and cover letters |
| Repeat use | Saved searches, resumable draft queues, daily execution when externally triggered, manual application history | Integrated setup, GUI and email/calendar updates |
| Applying | Review and export materials for manual use | Visible browser safe-fill; final submission stays with you |
| Data care | Private local storage; optional encrypted database backup/restore; whole-portable-home deletion | Document deduplication, automatic retention and larger-history performance |

See the [detailed roadmap](docs/ROADMAP.md) for engineering status and the
[release acceptance guide](docs/RELEASE_READINESS.md) for outstanding product validation.

## Know the boundaries

Discovery covers configured sources, with incomplete coverage and explicit gaps.
For unsupported sources, you can supply job text and its URL. Daily searches need
an authorized external wake-up mechanism; the CLI installs no background process.
Application status updates are manual. Login, sensitive answers, legal
attestations, signatures, and final submission remain your actions.

PDF/LaTeX extraction needs review, especially for wrapped publications, reading
order and unsupported sections. Backup and automated workflows support databases
up to 256 MiB; backup/restore near that limit used about 2.3 GiB of memory in
synthetic testing. Keep external source files and exported copies separately
backed up.

Your profile is stored locally, outside Git. Selected evidence and history are
retained in the private database; original source files and exported copies are
separate. Content shown to Codex is subject to your Codex configuration—local
storage does not mean the conversation stays on your device. Sensitive answers
are never inferred, and permission to use an answer is separate from permission
to retain it. Learning means reviewed memory and retrieval, not training a model
on your CV. See the [privacy and storage reference](docs/REFERENCE.md#trust-and-privacy-contract).

## Documentation

| I want to… | Start here |
|---|---|
| Use Grounded Apply through conversation | [Codex workflow](docs/CODEX_WORKFLOW.md) |
| Set up dependencies or inspect CLI examples | [Quickstart](docs/QUICKSTART.md) |
| Import a complete CV or build a profile by questions | [Profile setup](docs/PROFILE_SETUP.md) |
| Configure job sources and review their limits | [Job discovery](docs/JOB_DISCOVERY.md) |
| Discover jobs and prepare drafts together | [Saved searches](docs/SEARCH_RUNS.md) |
| Run and review a daily search | [Daily setup](docs/DAILY_QUICKSTART.md) |
| Understand backup, deletion, and import contracts | [Detailed reference](docs/REFERENCE.md) |
| Develop or verify a change | [Development guide](docs/DEVELOPMENT.md) · [Current checkpoint](docs/SESSION_HANDOFF.md) |
| Contribute or prepare a release | [Contributing](CONTRIBUTING.md) · [Release readiness](docs/RELEASE_READINESS.md) |

Read the [design](GROUNDED_APPLY_DESIGN.md) for the longer-term direction and
[security policy](SECURITY.md) for private vulnerability reporting.
Licensed under [Apache 2.0](LICENSE).
