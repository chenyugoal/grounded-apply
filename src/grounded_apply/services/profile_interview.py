"""Read-only questions for optional, progressive career-profile conversations.

The catalogue never reads a profile or accepts answers. Depth selects one exact
round; finishing it is not a profile-completeness assessment. Cursors identify
questions only and provide no durable record of which answers a user supplied.
"""

from __future__ import annotations

from dataclasses import dataclass


INTERVIEW_TOPICS: tuple[str, ...] = (
    "contact", "education", "experience", "research", "projects", "skills",
    "publications", "achievements", "preferences",
)
MAX_INTERVIEW_QUESTIONS = 10


@dataclass(frozen=True, slots=True)
class _Question:
    id: str
    topic: str
    depth: int
    prompt: str
    why: str

    def projection(self) -> dict[str, object]:
        return {"id": self.id, "topic": self.topic, "depth": self.depth,
                "prompt": self.prompt, "why": self.why}


# IDs are stable catalogue identifiers, not candidate or answer identifiers.
_CATALOGUE: tuple[_Question, ...] = (
    _Question("contact.d1.name", "contact", 1,
              "What name should appear on your application materials?",
              "Uses the name you choose rather than inferring it from a file."),
    _Question("contact.d1.email", "contact", 1,
              "Which email address, if any, should applications use?",
              "Establishes an explicit application contact."),
    _Question("contact.d1.location", "contact", 1,
              "What city or region, if any, would you like to display?",
              "Lets you choose a useful level of location detail."),
    _Question("contact.d2.phone", "contact", 2,
              "Would you like to include a phone number, and if so which one?",
              "Keeps optional contact information under your control."),
    _Question("contact.d2.links", "contact", 2,
              "Which public portfolio, website, or professional profile links should appear?",
              "Collects the exact links you want to share."),
    _Question("contact.d3.current", "contact", 3,
              "Which contact details or public links need updating?",
              "Identifies corrections without assuming earlier details are current."),
    _Question("contact.d3.omit", "contact", 3,
              "Which contact details should stay off your application materials?",
              "Separates having a detail from permission to publish it."),
    _Question("education.d1.entries", "education", 1,
              "What education or training would you like to include?",
              "Makes room for the full education history you choose to share."),
    _Question("education.d1.credentials", "education", 1,
              "What are the exact institution and qualification names for each entry?",
              "Preserves the credential wording instead of upgrading it."),
    _Question("education.d2.dates", "education", 2,
              "What are the dates and current completion status of each entry?",
              "Distinguishes completed, ongoing, and expected qualifications."),
    _Question("education.d2.focus", "education", 2,
              "Which specializations, coursework, or thesis titles are relevant?",
              "Adds specific academic context without treating it as employment."),
    _Question("education.d3.evidence", "education", 3,
              "What source could verify each qualification's exact status and dates?",
              "Finds evidence before making a stronger credential claim."),
    _Question("education.d3.distinctions", "education", 3,
              "Are there academic distinctions or results you want to document exactly?",
              "Preserves official wording and any qualifications on the result."),
    _Question("experience.d1.roles", "experience", 1,
              "What roles have you held, including internships, teaching, or service?",
              "Builds an inventory without silently selecting a starter subset."),
    _Question("experience.d1.details", "experience", 1,
              "What were the exact organization, title, and dates for each role?",
              "Keeps the identity and timeline of each role distinct."),
    _Question("experience.d2.responsibilities", "experience", 2,
              "What were your main responsibilities in each role?",
              "Adds the work behind a job title."),
    _Question("experience.d2.ownership", "experience", 2,
              "Which parts did you own, contribute to, or support personally?",
              "Separates your contribution from the team's work."),
    _Question("experience.d3.outcomes", "experience", 3,
              "What outcomes can you support, including qualitative results?",
              "Captures real impact without requiring invented metrics."),
    _Question("experience.d3.measurement", "experience", 3,
              "For any numerical result, how was it measured and over what period?",
              "Records the basis and limits of a metric before reuse."),
    _Question("research.d1.inventory", "research", 1,
              "Which research questions or studies have you worked on?",
              "Includes research that may not fit a short employment summary."),
    _Question("research.d1.context", "research", 1,
              "Where and when did you do each research project?",
              "Preserves the setting and dates of the work."),
    _Question("research.d2.contribution", "research", 2,
              "What was your own contribution to each study?",
              "Avoids attributing an entire study or team result to one person."),
    _Question("research.d2.methods", "research", 2,
              "Which methods did you implement, use, or evaluate yourself?",
              "Distinguishes hands-on methods from methods merely discussed."),
    _Question("research.d3.findings", "research", 3,
              "What findings can you support, and what evidence supports them?",
              "Connects research claims to results instead of plausible summaries."),
    _Question("research.d3.limits", "research", 3,
              "What limitations or unfinished work should qualify those findings?",
              "Preserves uncertainty and the difference between a prototype and a finished result."),
    _Question("projects.d1.inventory", "projects", 1,
              "Which personal, academic, open-source, or work projects should we include?",
              "Creates a broader project inventory without assuming production use."),
    _Question("projects.d1.purpose", "projects", 1,
              "What problem did each project address, and when did you work on it?",
              "Gives each project a purpose and timeline."),
    _Question("projects.d2.implementation", "projects", 2,
              "What did you personally design, build, test, or maintain?",
              "Captures concrete contribution and responsibility."),
    _Question("projects.d2.setting", "projects", 2,
              "Was each project coursework, a prototype, a personal tool, or used by others?",
              "Keeps the actual usage setting explicit."),
    _Question("projects.d3.results", "projects", 3,
              "What results or usage can you verify for each project?",
              "Supports outcomes without assuming adoption or scale."),
    _Question("projects.d3.artifacts", "projects", 3,
              "Which shareable artifacts or links show your contribution?",
              "Finds evidence the user is willing to share."),
    _Question("skills.d1.technical", "skills", 1,
              "Which tools, technologies, or methods have you personally used?",
              "Starts with actual use rather than a target job's keywords."),
    _Question("skills.d1.languages", "skills", 1,
              "Which spoken languages would you like to include, at what proficiency?",
              "Uses your stated proficiency instead of guessing it."),
    _Question("skills.d2.examples", "skills", 2,
              "What task have you completed with each skill you want to emphasize?",
              "Connects a skill label to concrete work."),
    _Question("skills.d2.recency", "skills", 2,
              "When did you last use each skill, and in what setting?",
              "Makes recency and academic or professional context visible."),
    _Question("skills.d3.evidence", "skills", 3,
              "Which project or work example best demonstrates each key skill?",
              "Identifies evidence for later application-specific selection."),
    _Question("skills.d3.limits", "skills", 3,
              "Which skills should be described as introductory or limited experience?",
              "Prevents familiarity from becoming an unsupported expertise claim."),
    _Question("publications.d1.inventory", "publications", 1,
              "Which papers, posters, talks, or other publications should we include?",
              "Accounts for individual outputs instead of one generic research fact."),
    _Question("publications.d1.status", "publications", 1,
              "What is each item's current status, such as submitted, preprint, or published?",
              "Keeps publication status exact."),
    _Question("publications.d2.citation", "publications", 2,
              "What is the exact citation or presentation title for each item?",
              "Preserves titles, authorship, venues, and dates from the source."),
    _Question("publications.d2.contribution", "publications", 2,
              "What did you contribute to each publication or presentation?",
              "Distinguishes authorship from the specific work performed."),
    _Question("publications.d3.evidence", "publications", 3,
              "Which DOI, public link, or other source confirms each item's status?",
              "Provides an evidence path without assuming a link is proof until reviewed."),
    _Question("publications.d3.updates", "publications", 3,
              "Have any titles, venues, dates, or publication statuses changed?",
              "Finds stale entries before they appear in a new application."),
    _Question("achievements.d1.inventory", "achievements", 1,
              "Which awards, certifications, or accomplishments would you like to include?",
              "Includes achievements that may be absent from a short resume."),
    _Question("achievements.d1.details", "achievements", 1,
              "What is the exact name, issuer, and date of each award or certification?",
              "Preserves formal credential and recognition details."),
    _Question("achievements.d2.contribution", "achievements", 2,
              "What did you personally do to earn each achievement?",
              "Separates individual work from group recognition."),
    _Question("achievements.d2.criteria", "achievements", 2,
              "What selection criteria or scope can you verify for each recognition?",
              "Avoids inventing selectivity, rank, or prestige."),
    _Question("achievements.d3.evidence", "achievements", 3,
              "What source supports each achievement or numerical result?",
              "Connects stronger impact statements to evidence."),
    _Question("achievements.d3.validity", "achievements", 3,
              "Do any certifications have expiry dates or conditions we should preserve?",
              "Prevents a past credential from being presented as currently valid."),
    _Question("preferences.d1.roles", "preferences", 1,
              "Which role families and levels would you like to explore?",
              "Guides the search without treating an aspiration as a qualification."),
    _Question("preferences.d1.locations", "preferences", 1,
              "Which locations or remote-work arrangements interest you?",
              "Collects preferences without inferring geographic eligibility."),
    _Question("preferences.d2.environment", "preferences", 2,
              "Which industries, research areas, or team settings interest you?",
              "Makes source selection more relevant to your goals."),
    _Question("preferences.d2.constraints", "preferences", 2,
              "Which job features are must-haves, and which are nice-to-haves?",
              "Keeps hard filters separate from flexible preferences."),
    _Question("preferences.d3.priorities", "preferences", 3,
              "When attractive roles differ, what tradeoffs matter most to you?",
              "Supports transparent choices instead of an unexplained ranking."),
    _Question("preferences.d3.review", "preferences", 3,
              "Which preferences are temporary or worth revisiting later?",
              "Avoids treating today's search choices as permanent career facts."),
)


def build_interview(topic: str = "all", depth: int = 1,
                    after: str | None = None, limit: int = 3) -> dict[str, object]:
    """Return a bounded page of generic questions with no external interaction.

    ``depth`` is the exact round, not a cumulative maximum. ``after`` must be
    the ID of a question in that topic/round. It advances past a question whether
    answered or skipped; the caller owns conversational progress. A completed
    page has no next cursor. An explicit last-question cursor yields an empty,
    finished page. No answer, profile, current time, or environment is consulted.
    """
    if type(topic) is not str or topic not in ("all", *INTERVIEW_TOPICS):
        raise ValueError("Interview topic must be all or a supported topic")
    if type(depth) is not int or depth not in (1, 2, 3):
        raise ValueError("Interview depth must be an integer from 1 to 3")
    if type(limit) is not int or not 1 <= limit <= MAX_INTERVIEW_QUESTIONS:
        raise ValueError("Interview limit must be an integer from 1 to 10")
    selected = tuple(question for question in _CATALOGUE
                     if question.depth == depth and (topic == "all" or question.topic == topic))
    start = 0
    if after is not None:
        if type(after) is not str:
            raise ValueError("Interview cursor must identify a question in this topic and depth")
        indexes = {question.id: index for index, question in enumerate(selected)}
        if after not in indexes:
            raise ValueError("Interview cursor must identify a question in this topic and depth")
        start = indexes[after] + 1
    page = selected[start:start + limit]
    remaining = len(selected) - start - len(page)
    return {
        "schema_version": 1,
        "catalog_version": 1,
        "topic": topic,
        "depth": depth,
        "questions": [question.projection() for question in page],
        "next_cursor": page[-1].id if remaining else None,
        "finished": remaining == 0,
        "total_questions": len(selected),
        "remaining_questions": remaining,
        "answers_stored": False,
        "profile_read": False,
        "guidance": [
            "Answer, skip, change topic, or stop whenever you want; unknown is a valid answer.",
            "These are generic questions. Finishing a round does not establish a complete profile.",
            "The cursor continues this question list; it does not save answers or track completion.",
            "This command accepts no answers and stores nothing. Your Codex conversation is separate.",
            "Retaining career facts requires a separate exact-source import and your explicit approval; "
            "unsupported answers remain questions, and search preferences remain separate choices.",
        ],
    }
