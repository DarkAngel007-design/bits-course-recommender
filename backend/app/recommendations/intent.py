"""Natural-language query -> strict, editable intent (FR-09).

Two interpreters produce the same schema:
  * rules  - deterministic pattern parser, always available (degraded mode, FR-15)
  * llm    - Claude via the Anthropic SDK with a Pydantic output schema, used when
             credentials are configured. Its output is re-validated: unknown properties,
             operators and topic ids are rejected; it cannot add courses, rules or facts.
The dashboard shows the interpreted intent and lets the student edit it before rerunning.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ingestion.topics import TAXONOMY, resolve_interest  # shared taxonomy (no PDF dependencies)

log = logging.getLogger(__name__)

Category = Literal["ANY", "CDC", "DEL", "HUEL", "OPEL", "MINOR"]
HardProperty = Literal["midsem_present", "compre_present", "attendance_none", "has_component", "no_component",
                       "department", "level", "units", "instructor", "course_code"]
SoftProperty = Literal["project_based", "lenient_makeup", "open_book", "low_exam_weight", "topic"]
COMPONENTS = ["quiz", "project", "lab", "assignment", "presentation", "viva", "case_study"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


class HardFilter(BaseModel):
    property: HardProperty
    value: bool | int | str
    source_text: str | None = Field(None, description="The query words this filter came from")


class SoftPreference(BaseModel):
    property: SoftProperty
    value: str | None = None
    weight: float = Field(1.0, ge=0, le=3)
    source_text: str | None = None


class SchedulePrefs(BaseModel):
    no_early_classes: bool = False
    free_days: list[str] = Field(default_factory=list)
    compact: bool = False

    @field_validator("free_days")
    @classmethod
    def _days(cls, v: list[str]) -> list[str]:
        return [d for d in v if d in DAYS]


class Intent(BaseModel):
    category: Category = "ANY"
    topics: list[str] = Field(default_factory=list, description="Taxonomy topic ids")
    search_terms: list[str] = Field(default_factory=list, description="Extra free-text interest words")
    hard_filters: list[HardFilter] = Field(default_factory=list)
    soft_preferences: list[SoftPreference] = Field(default_factory=list)
    count: int = Field(5, ge=1, le=20)
    schedule: SchedulePrefs = Field(default_factory=SchedulePrefs)
    clarifications: list[str] = Field(default_factory=list)
    unsupported: list[str] = Field(default_factory=list, description="Requested properties the data cannot support")

    @field_validator("topics")
    @classmethod
    def _topics(cls, v: list[str]) -> list[str]:
        return [t for t in dict.fromkeys(v) if t in TAXONOMY]


# ---------------------------------------------------------------------------
# deterministic parser
# ---------------------------------------------------------------------------
NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "ten": 10}
DAY_WORDS = {"monday": "Mon", "tuesday": "Tue", "wednesday": "Wed", "thursday": "Thu", "friday": "Fri",
             "saturday": "Sat"}
PREFIXES = {"CS", "ECON", "EEE", "ECE", "MATH", "PHY", "CHEM", "BIO", "BITS", "HSS", "GS", "ME", "CE", "CHE", "FIN",
            "MGTS", "INSTR", "BIOT", "MF", "PHA", "IS", "ENVS", "MST", "SNS", "DE", "MEL", "MPBA", "AN", "AUE"}


def _neg(q: str, pat: str) -> bool:
    return bool(re.search(rf"\b(no|without|zero|avoid|skip|not? (?:have|having|with)|free of)\s+(?:an?\s+|any\s+|the\s+)?{pat}", q))


def parse_rules(query: str, profile_interests: list[str] | None = None) -> Intent:
    q = " " + query.lower().strip() + " "
    it = Intent()
    # category
    if re.search(r"\bdels?\b|discipline electives?", q):
        it.category = "DEL"
    elif re.search(r"\bhuels?\b|humanit(y|ies)|\bhss\b", q):
        it.category = "HUEL"
    elif re.search(r"\bopels?\b|open electives?", q):
        it.category = "OPEL"
    elif re.search(r"\bcdcs?\b|compulsory disciplin|discipline core|core courses?", q):
        it.category = "CDC"
    elif re.search(r"\bminor\b", q):
        it.category = "MINOR"
    # topics
    for tid, t in TAXONOMY.items():
        names = [t["label"].lower()] + [a.lower() for a in t["aliases"]]
        for n in names:
            if re.search(rf"(?<![a-z]){re.escape(n)}(?![a-z])", q):
                it.topics.append(tid)
                break
    if "artificial_intelligence" in it.topics and "machine_learning" not in it.topics and re.search(r"\bai\b|artificial", q):
        pass
    # exams
    if _neg(q, r"mid[\s-]?sem(?:ester)?s?(?: exams?| tests?)?|mid[\s-]?terms?"):
        it.hard_filters.append(HardFilter(property="midsem_present", value=False, source_text="no midsem"))
    elif re.search(r"\bwith (?:a )?mid[\s-]?sem", q):
        it.hard_filters.append(HardFilter(property="midsem_present", value=True, source_text="with midsem"))
    if _neg(q, r"compre(?:hensive)?s?(?: exams?)?|final exams?|end[\s-]?sem"):
        it.hard_filters.append(HardFilter(property="compre_present", value=False, source_text="no comprehensive exam"))
    # attendance
    if _neg(q, r"(?:mandatory |compulsory )?attendance(?: requirements?| polic(?:y|ies)| rules?)?") or \
            re.search(r"attendance (?:is )?(?:not|isn'?t) (?:mandatory|compulsory|required)", q):
        it.hard_filters.append(HardFilter(property="attendance_none", value=True, source_text="no attendance requirement"))
    # components
    comp_words = {"quiz": r"quiz(?:zes)?", "lab": r"labs?|laboratory", "project": r"projects?",
                  "presentation": r"presentations?|seminars?", "viva": r"vivas?", "assignment": r"assignments?"}
    for comp, pat in comp_words.items():
        if _neg(q, pat):
            it.hard_filters.append(HardFilter(property="no_component", value=comp, source_text=f"no {comp}"))
    prefer = re.search(r"prefer|preferably|ideally|would like|nice to have|if possible|rather", q)
    if re.search(r"project[\s-]?based|projects?[\s-]heavy|hands[\s-]on|project work|evaluat\w* (?:by|through) projects?", q):
        if prefer or not re.search(r"\bmust\b|\bonly\b|\brequire", q):
            it.soft_preferences.append(SoftPreference(property="project_based", weight=1.5,
                                                      source_text="project-based evaluation"))
        else:
            it.hard_filters.append(HardFilter(property="has_component", value="project", source_text="project"))
    if re.search(r"lenient make[\s-]?up|easy make[\s-]?up|flexible make[\s-]?up|make[\s-]?up (?:polic\w* )?(?:that is )?(?:lenient|flexible|easy)", q):
        it.soft_preferences.append(SoftPreference(property="lenient_makeup", weight=1.2, source_text="lenient make-up"))
        it.clarifications.append("\"Lenient\" is subjective. Results are ranked by a transparent reading of each "
                                 "handout's make-up clause (components covered, minus restrictions such as medical-only, "
                                 "prior permission or no make-up for quizzes). Tell me which condition matters most.")
    if re.search(r"open[\s-]?book|take[\s-]?home", q):
        it.soft_preferences.append(SoftPreference(property="open_book", source_text="open book"))
    if re.search(r"(?:fewer|less|light|low) exams?|exam[\s-]?light|less exam weight", q):
        it.soft_preferences.append(SoftPreference(property="low_exam_weight", source_text="fewer exams"))
    # schedule
    if re.search(r"no (?:8|eight)\s?(?:am|a\.m\.|o'?clock)|no early (?:morning )?classes|not? (?:before|at) (?:8|9)\s?am|"
                 r"no classes at (?:8|eight)|avoid (?:8|eight)\s?am", q):
        it.schedule.no_early_classes = True
    for w, d in DAY_WORDS.items():
        if re.search(rf"(?:free|off|no classes? on|keep)\s+(?:my\s+)?{w}s?|{w}s?\s+(?:free|off)", q):
            it.schedule.free_days.append(d)
    if re.search(r"compact|no (?:long )?gaps|avoid (?:long )?gaps|back[\s-]to[\s-]back", q):
        it.schedule.compact = True
    # misc hard filters
    m = re.search(r"\b(\d)\s*[- ]?units?\b", q)
    if m:
        it.hard_filters.append(HardFilter(property="units", value=int(m.group(1)), source_text=m.group(0).strip()))
    if re.search(r"higher[\s-]degree|\bg[\s-]level\b|graduate[\s-]level", q):
        it.hard_filters.append(HardFilter(property="level", value="G", source_text="higher degree"))
    for tok in re.findall(r"\b([A-Z]{2,5})\b", query):
        if tok in PREFIXES and tok not in ("AI", "ML", "HUEL", "OPEL", "DEL", "CDC"):
            it.hard_filters.append(HardFilter(property="department", value=tok, source_text=tok))
    mi = re.search(r"(?:taught by|instructor|prof\.?|professor|dr\.?)\s+([a-z][a-z .]{2,40}?)(?:[,.?!]|$| and | who )", q)
    if mi and mi.group(1).split()[0] in {"for", "of", "who", "the", "a", "an", "that", "is", "in", "with"}:
        mi = None
    if mi:
        it.hard_filters.append(HardFilter(property="instructor", value=mi.group(1).strip().title(),
                                          source_text=mi.group(0).strip()))
    # count
    mc = re.search(r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|ten)\s+(?:[a-z-]+\s+)?(?:courses|options|electives|dels|huels|opels|suggestions)", q)
    if mc:
        it.count = min(20, NUM_WORDS.get(mc.group(1), None) or int(mc.group(1)))
    # unsupported subjective claims
    for pat, msg in ((r"easy|easiest|chill|light workload|less work", "workload/difficulty"),
                     (r"good grad|lenient grad|high grades?|scoring|easy a\b", "grading leniency"),
                     (r"best (?:prof|instructor|teacher)|good (?:prof|instructor|teacher)", "instructor quality"),
                     (r"no (?:plagiarism|nc)\b", "grading policy")):
        if re.search(pat, q):
            it.unsupported.append(f"{msg}: the supplied documents contain no evidence for this, so it is not used "
                                  "to filter or rank.")
    # free-text terms (what remains after known phrases), used for BM25
    stripped = re.sub(r"\b(suggest|recommend|find|show|give|me|i|want|need|an?|the|some|courses?|electives?|dels?|"
                      r"huels?|opels?|cdcs?|with|without|no|and|or|related|to|on|in|for|of|that|which|is|are|have|has|"
                      r"prefer|preferably|based|evaluation|policy|requirement|attendance|midsem|mid-sem|compre|"
                      r"make-?up|lenient|project|classes|free|keep|my|am|8|please|any|like|about|also|would|should|"
                      r"semester|this|next|course|discipline|open|humanities|core|minor|exam|exams)\b", " ", q)
    if mi:
        stripped = stripped.replace(mi.group(0), " ")
    noise = set(DAY_WORDS) | {"taught", "easy", "easiest", "chill", "gaps", "gap", "early", "compact", "classes",
                              "morning", "grading", "grades", "workload", "ai", "ml", "suggestions", "options", "best", "good", "professor", "prof", "instructor", "teacher"} | set(NUM_WORDS)
    terms = [t.strip("-") for t in re.findall(r"[a-z][a-z+#-]{2,}", stripped)]
    terms = [t for t in terms if t not in noise and len(t) > 2]
    topic_words = {w for tid in it.topics for w in TAXONOMY[tid]["label"].lower().split()}
    it.search_terms = [t for t in dict.fromkeys(terms) if t not in topic_words][:8]
    if not it.topics and not it.search_terms and profile_interests:

        for i in profile_interests:
            it.topics += resolve_interest(i)
        it.topics = list(dict.fromkeys(it.topics))
    return Intent.model_validate(it.model_dump())


# ---------------------------------------------------------------------------
# LLM parser (optional)
# ---------------------------------------------------------------------------
SYSTEM = """You translate a BITS Pilani student's course-selection request into a strict JSON intent.
You never invent courses, rules or course properties; you only classify the request.

category: DEL (discipline elective), HUEL (humanities elective), OPEL (open elective), CDC (compulsory
discipline core), MINOR, or ANY if the student did not name one.
topics: choose only from these ids: {topics}
hard_filters (only when the student states a requirement, not a preference). Allowed properties:
  midsem_present (bool), compre_present (bool), attendance_none (true = "no attendance requirement"),
  has_component / no_component (one of: quiz, project, lab, assignment, presentation, viva, case_study),
  department (course prefix like CS, ECON), level ("G" = higher degree), units (int), instructor (name),
  course_code (like "CS F407").
soft_preferences (preferences, "prefer", "ideally", or subjective words): project_based, lenient_makeup,
  open_book, low_exam_weight.
schedule: no_early_classes (no 8 AM), free_days (Mon..Sat), compact.
clarifications: questions to ask when a word is subjective or ambiguous (e.g. "lenient").
unsupported: requested things the documents cannot support (difficulty, grading leniency, instructor ratings).
search_terms: a few extra interest keywords not covered by topics.
count: number of courses requested (default 5)."""


def parse_llm(query: str, profile_interests: list[str] | None = None) -> Intent | None:
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return None
    try:
        import anthropic
    except ImportError:
        return None
    try:
        client = anthropic.Anthropic(timeout=float(os.environ.get("LLM_TIMEOUT_S", "20")), max_retries=1)
        topics = ", ".join(f"{k} ({v['label']})" for k, v in TAXONOMY.items())
        user = f"Student interests from profile: {', '.join(profile_interests or []) or 'none'}\nRequest: {query}"
        resp = client.messages.parse(
            model=os.environ.get("LLM_MODEL", "claude-opus-5"),
            max_tokens=2000,
            system=SYSTEM.format(topics=topics),
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": user}],
            output_format=Intent,
        )
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            log.warning("LLM intent parse returned no usable output (stop_reason=%s)", resp.stop_reason)
            return None
        return Intent.model_validate(resp.parsed_output.model_dump())  # re-validate topics/operators
    except Exception as exc:  # network, auth, rate limit, schema: fall back to rules
        log.warning("LLM intent parsing failed, using deterministic parser: %s", type(exc).__name__)
        return None


def interpret(query: str, profile_interests: list[str] | None = None, use_llm: bool = True) -> tuple[Intent, str, str | None]:
    """Return (intent, parser_used, degraded_reason)."""
    if use_llm:
        it = parse_llm(query, profile_interests)
        if it is not None:
            # deterministic safety net: never lose an explicit hard constraint the rules parser saw
            rules = parse_rules(query, profile_interests)
            have = {(f.property, str(f.value)) for f in it.hard_filters}
            for f in rules.hard_filters:
                if (f.property, str(f.value)) not in have and f.property in ("midsem_present", "attendance_none",
                                                                             "compre_present"):
                    it.hard_filters.append(f)
            return it, "llm", None
        reason = "LLM not configured" if not (os.environ.get("ANTHROPIC_API_KEY") or
                                              os.environ.get("ANTHROPIC_AUTH_TOKEN")) else "LLM unavailable"
        return parse_rules(query, profile_interests), "rules", reason
    return parse_rules(query, profile_interests), "rules", None
