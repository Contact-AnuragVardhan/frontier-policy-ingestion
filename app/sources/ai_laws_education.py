from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from app.models import PolicyRecord
from app.normalize import (
    ALLOWED_CATEGORIES,
    ALLOWED_STATUS_GROUPS,
    canonical_bill_title,
    normalize_space,
    state_code_for,
)

SOURCE_NAME = "AI Laws by State — Education AI Tracker"
SOURCE_URL = "https://www.ailawsbystate.com/tools/education-ai-tracker"

SUPPORTED_SOURCE_CATEGORIES = {
    "Student Data Privacy & AI",
    "AI Literacy & Curriculum",
    "Generative AI in Classrooms",
    "Teacher Use of AI / PD",
}

PENDING_STATUSES = {
    "introduced",
    "in committee",
    "passed one chamber",
    "passed both chambers",
    # Defensive compatibility with source wording seen in older snapshots.
    "passed 1st chamber",
    "passed 2nd chamber",
}
INACTIVE_STATUSES = {"dead", "dead/failed", "failed", "vetoed"}

DATE_FORMATS = (
    "%b %d, %Y",
    "%B %d, %Y",
    "%Y-%m-%d",
    "%m/%d/%Y",
)

EXPECTED_HEADERS = (
    "state",
    "bill / statute",
    "year",
    "category",
    "status",
    "key requirement",
)


@dataclass(frozen=True)
class TrackerRow:
    state_name: str
    identifier_raw: str
    year: int | None
    source_category: str
    source_status: str
    key_requirement: str
    research_record_url: str | None

    def raw_dict(self) -> dict:
        return asdict(self)




@dataclass(frozen=True)
class TrackerPageMetadata:
    declared_state_count: int | None = None
    declared_bill_count: int | None = None
    showing_count: int | None = None
    total_count: int | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DetailInfo:
    title: str | None = None
    official_source_url: str | None = None
    source_status: str | None = None
    last_action_date: str | None = None
    last_verified_date: str | None = None
    data_updated_date: str | None = None
    effective_date: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Candidate:
    raw: TrackerRow
    detail: DetailInfo = field(default_factory=DetailInfo)
    state_code: str | None = None
    policy_identifier: str | None = None
    identifier_key: str | None = None
    title: str | None = None
    summary: str | None = None
    policy_type: str | None = None
    category: str | None = None
    categories: tuple[str, ...] = ()
    status: str | None = None
    status_detail: str | None = None
    effective_date: str | None = None
    implementation_timeline: str | None = None
    source_url: str | None = None
    research_source_name: str = SOURCE_NAME
    research_source_url: str | None = None
    research_source_date: str | None = None
    status_as_of_date: str | None = None
    last_updated: str | None = None
    last_verified_at: str | None = None
    classification: str = "NEW"
    issues: list[str] = field(default_factory=list)
    review_flags: list[str] = field(default_factory=list)
    duplicate_existing_title: str | None = None
    duplicate_existing_source_url: str | None = None
    review_disposition: str = "UNREVIEWED"
    review_resolution_note: str | None = None

    def is_reviewed_exclusion(self) -> bool:
        return self.review_disposition.startswith("EXCLUDE_")

    def has_blocking_review_flags(self) -> bool:
        return any(not flag.startswith("WARNING:") for flag in self.review_flags)

    def is_valid_for_db(self) -> bool:
        # Only NEW records are eligible for generated SQL. Existing matches and
        # possible matches are review-only so existing provenance is never overwritten.
        # Non-blocking source-alignment warnings remain visible in review output but do
        # not by themselves make an otherwise valid record un-loadable.
        return (
            not self.is_reviewed_exclusion()
            and not self.issues
            and not self.has_blocking_review_flags()
            and self.classification == "NEW"
        )

    def to_policy_record(self) -> PolicyRecord:
        required = {
            "state_code": self.state_code,
            "policy_identifier": self.policy_identifier,
            "title": self.title,
            "summary": self.summary,
            "policy_type": self.policy_type,
            "category": self.category,
            "status": self.status,
            "status_detail": self.status_detail,
            "source_url": self.source_url,
            "research_source_url": self.research_source_url,
            "research_source_date": self.research_source_date,
            "status_as_of_date": self.status_as_of_date,
            "last_updated": self.last_updated,
        }
        missing = [name for name, value in required.items() if not value]
        if missing or not self.state_code:
            raise ValueError(f"Candidate is not DB-ready; missing={missing}, issues={self.issues}")
        return PolicyRecord(
            state_code=self.state_code,
            state_name=self.raw.state_name,
            policy_identifier=self.policy_identifier,
            title=self.title,
            summary=self.summary,
            policy_type=self.policy_type,
            category=self.category,
            categories=self.categories,
            status=self.status,
            status_detail=self.status_detail,
            effective_date=self.effective_date,
            implementation_timeline=self.implementation_timeline,
            source_url=self.source_url,
            research_source_name=self.research_source_name,
            research_source_url=self.research_source_url,
            research_source_date=self.research_source_date,
            status_as_of_date=self.status_as_of_date,
            last_updated=self.last_updated,
            last_verified_at=self.last_verified_at,
        )

    def review_dict(self) -> dict:
        return {
            "classification": self.classification,
            "issues": " | ".join(self.issues),
            "review_flags": " | ".join(self.review_flags),
            "review_disposition": self.review_disposition,
            "review_resolution_note": self.review_resolution_note or "",
            "source_state": self.raw.state_name,
            "state_code": self.state_code or "",
            "source_identifier": self.raw.identifier_raw,
            "policy_identifier": self.policy_identifier or "",
            "normalized_identifier_key": self.identifier_key or "",
            "source_year": self.raw.year or "",
            "source_category": self.raw.source_category,
            "category": self.category or "",
            "categories": " | ".join(self.categories),
            "source_status": self.raw.source_status,
            "status": self.status or "",
            "status_detail": self.status_detail or "",
            "key_requirement": self.raw.key_requirement,
            "summary": self.summary or "",
            "title": self.title or "",
            "policy_type": self.policy_type or "",
            "effective_date": self.effective_date or "",
            "implementation_timeline": self.implementation_timeline or "",
            "source_url": self.source_url or "",
            "research_source_name": self.research_source_name,
            "research_source_url": self.research_source_url or "",
            "research_source_date": self.research_source_date or "",
            "status_as_of_date": self.status_as_of_date or "",
            "last_updated": self.last_updated or "",
            "last_verified_at": self.last_verified_at or "",
            "detail_last_action_date": self.detail.last_action_date or "",
            "duplicate_existing_title": self.duplicate_existing_title or "",
            "duplicate_existing_source_url": self.duplicate_existing_source_url or "",
        }


def fetch_html(url: str, timeout: int = 30, session: requests.Session | None = None) -> str:
    client = session or requests.Session()
    response = client.get(
        url,
        timeout=timeout,
        headers={
            "User-Agent": "AIChoice-PolicyIngestion/1.0 (+https://www.aichoice.org)"
        },
    )
    response.raise_for_status()
    return response.text


def _table_headers(table: Tag) -> list[str]:
    header_cells = table.find_all("th")
    return [normalize_space(cell.get_text(" ", strip=True)).lower() for cell in header_cells]


def _find_tracker_table(soup: BeautifulSoup) -> Tag:
    for table in soup.find_all("table"):
        headers = _table_headers(table)
        if not headers:
            continue
        if all(any(expected == actual or expected in actual for actual in headers) for expected in EXPECTED_HEADERS):
            return table
    raise ValueError(
        "Could not find the Education AI Tracker table with the expected columns. "
        "The source page structure may have changed or public access may be unavailable."
    )


def parse_tracker_html(html: str, page_url: str = SOURCE_URL) -> list[TrackerRow]:
    soup = BeautifulSoup(html, "html.parser")
    table = _find_tracker_table(soup)
    headers = _table_headers(table)
    if len(headers) < 6:
        raise ValueError(f"Tracker table has too few columns: {headers!r}")

    rows: list[TrackerRow] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if not cells:
            continue
        values = [normalize_space(cell.get_text(" ", strip=True)) for cell in cells]
        if len(values) < 6:
            continue

        by_header = {headers[i]: values[i] for i in range(min(len(headers), len(values)))}
        state = by_header.get("state", values[0])
        identifier = by_header.get("bill / statute", values[1])
        year_text = by_header.get("year", values[2])
        category = by_header.get("category", values[3])
        status = by_header.get("status", values[4])
        key_requirement = by_header.get("key requirement", values[5])

        identifier_cell_index = headers.index("bill / statute") if "bill / statute" in headers else 1
        identifier_cell = cells[identifier_cell_index]
        detail_link = identifier_cell.find("a", href=True)
        detail_url = urljoin(page_url, detail_link["href"].strip()) if detail_link else None

        year = int(year_text) if re.fullmatch(r"\d{4}", year_text) else None
        rows.append(
            TrackerRow(
                state_name=state,
                identifier_raw=identifier,
                year=year,
                source_category=category,
                source_status=status,
                key_requirement=key_requirement,
                research_record_url=detail_url,
            )
        )

    if not rows:
        raise ValueError("No Education AI Tracker rows were extracted from the public tracker table.")
    return rows




def parse_tracker_page_metadata(html: str) -> TrackerPageMetadata:
    """Extract source-declared live counts when the page exposes them.

    These values are validation aids only; they are never hardcoded or used as the
    authoritative dataset.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = normalize_space(soup.get_text(" ", strip=True))

    declared_state_count = None
    declared_bill_count = None
    showing_count = None
    total_count = None

    match = re.search(r"\b(\d+)\s+states?\s*,\s*(\d+)\s+bills?\b", text, re.I)
    if match:
        declared_state_count = int(match.group(1))
        declared_bill_count = int(match.group(2))

    match = re.search(r"\bShowing\s+(\d+)\s+of\s+(\d+)\s+bills?\b", text, re.I)
    if match:
        showing_count = int(match.group(1))
        total_count = int(match.group(2))

    return TrackerPageMetadata(
        declared_state_count=declared_state_count,
        declared_bill_count=declared_bill_count,
        showing_count=showing_count,
        total_count=total_count,
    )


def _date_to_iso(value: str | None) -> str | None:
    value = normalize_space(value or "")
    if not value:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _extract_label_value(soup: BeautifulSoup, label: str) -> str | None:
    target = label.strip().lower()
    # The detail page renders record labels as ordinary text followed by a value.
    for text_node in soup.find_all(string=True):
        if normalize_space(str(text_node)).lower() != target:
            continue
        parent = text_node.parent
        if parent is None:
            continue
        # Prefer the next sibling/element text after the label container.
        for sibling in parent.next_siblings:
            if isinstance(sibling, Tag):
                value = normalize_space(sibling.get_text(" ", strip=True))
            else:
                value = normalize_space(str(sibling))
            if value:
                return value
        next_tag = parent.find_next()
        while next_tag and next_tag is not parent:
            value = normalize_space(next_tag.get_text(" ", strip=True))
            if value and value.lower() != target:
                return value
            next_tag = next_tag.find_next()
    return None


def _extract_record_fields_by_text(soup: BeautifulSoup) -> dict[str, str]:
    text = soup.get_text("\n", strip=True)
    lines = [normalize_space(line) for line in text.splitlines() if normalize_space(line)]
    result: dict[str, str] = {}
    wanted = {
        "Status": "status",
        "Last action": "last_action",
        "Last verified": "last_verified",
        "Data updated": "data_updated",
        "Effective date": "effective_date",
    }
    for index, line in enumerate(lines[:-1]):
        key = wanted.get(line)
        if key and key not in result:
            result[key] = lines[index + 1]
    return result


def parse_detail_html(html: str, detail_url: str) -> DetailInfo:
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")
    title = normalize_space(heading.get_text(" ", strip=True)) if heading else None

    official_url = None
    for anchor in soup.find_all("a", href=True):
        text = normalize_space(anchor.get_text(" ", strip=True)).lower()
        href = urljoin(detail_url, anchor["href"].strip())
        host = urlparse(href).netloc.lower()
        if "official bill text" in text or "official legislature" in text:
            if host and "ailawsbystate.com" not in host:
                official_url = href
                break

    fields = _extract_record_fields_by_text(soup)
    # Fallback to semantic label search for layouts that do not flatten cleanly.
    source_status = fields.get("status") or _extract_label_value(soup, "Status")
    last_action = fields.get("last_action") or _extract_label_value(soup, "Last action")
    last_verified = fields.get("last_verified") or _extract_label_value(soup, "Last verified")
    data_updated = fields.get("data_updated") or _extract_label_value(soup, "Data updated")
    effective_date = fields.get("effective_date") or _extract_label_value(soup, "Effective date")

    return DetailInfo(
        title=title,
        official_source_url=official_url,
        source_status=normalize_space(source_status or "") or None,
        last_action_date=_date_to_iso(last_action),
        last_verified_date=_date_to_iso(last_verified),
        data_updated_date=_date_to_iso(data_updated),
        effective_date=_date_to_iso(effective_date),
    )


def normalize_identifier_display(value: str) -> str:
    value = normalize_space(value).upper().replace("–", "-").replace("—", "-")
    value = re.sub(r"\s*-\s*", "-", value)
    value = value.replace(".", "")
    compact = re.sub(r"\s+", "", value)
    match = re.fullmatch(r"([A-Z]+)0*(\d+)", compact)
    if match:
        prefix, number = match.groups()
        return f"{prefix} {int(number)}"
    return value


def normalized_identifier_key(value: str) -> str:
    value = normalize_space(value).upper().replace(".", "")
    parts = re.findall(r"[A-Z]+|\d+", value)
    normalized: list[str] = []
    for part in parts:
        if part.isdigit():
            normalized.append(str(int(part)) if set(part) != {"0"} else "0")
        else:
            normalized.append(part)
    return ":".join(normalized)


def status_for_source(raw_status: str) -> str | None:
    normalized = normalize_space(raw_status).lower()
    if normalized == "enacted":
        return "Enacted"
    if normalized in PENDING_STATUSES:
        return "Pending"
    if normalized in INACTIVE_STATUSES:
        return "Inactive"
    if normalized == "unknown":
        return "Unknown"
    return None


def _contains_any(text: str, patterns: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in patterns)


def _has_ai_signal(text: str) -> bool:
    text = normalize_space(text)
    return bool(
        re.search(r"\bAI\b", text, re.I)
        or _contains_any(
            text,
            (
                "artificial intelligence",
                "artificial intell",  # source headings can be visibly truncated
                "artificial intel",   # truncation before the final "ligence"
                "artifical intelligence",  # common source typo
                "generative ai",
                "chatbot",
                "chat bot",
                "deepfake",
                "facial recognition",
                "biometric",
                "machine learning",
                "automated decision",
                "algorithmic",
            ),
        )
    )


def _has_education_signal(text: str) -> bool:
    return _contains_any(
        text,
        (
            "school",
            "student",
            "pupil",
            "teacher",
            "educator",
            "education",
            "eduaction",  # source typo seen in public bill titles
            "educ-",
            "curriculum",
            "classroom",
            "college",
            "instruction",
            "academic",
            "sch cd",
            "com col",
            "isbe",
        ),
    )


def _has_k12_signal(text: str) -> bool:
    normalized = normalize_space(text).lower()
    if _contains_any(
        normalized,
        (
            "k-12",
            "k12",
            "school",
            "school district",
            "pupil",
            "elementary",
            "high school",
            "middle school",
            "teacher",
            "educator",
            "classroom",
            "school code",
            "sch cd",
            "isbe",
        ),
    ):
        return True
    # Word-boundary check avoids treating 'postsecondary' as a K-12 signal.
    return bool(re.search(r"\bsecondary\b", normalized))


def _has_postsecondary_signal(text: str) -> bool:
    return _contains_any(
        text,
        (
            "postsecondary",
            "post-secondary",
            "higher education",
            "higher educational",
            "community college",
            "community colleges",
            "com col",
            "university",
            "universities",
        ),
    )


def _has_privacy_signal(text: str) -> bool:
    return _contains_any(
        text,
        (
            "privacy",
            "student data",
            "personal data",
            "biometric",
            "facial recognition",
            "surveillance",
            "online safety",
            "cyber",
        ),
    )


def categories_for_source(
    source_category: str,
    key_requirement: str,
    detail_title: str | None = None,
) -> tuple[str | None, tuple[str, ...], list[str]]:
    source_category = normalize_space(source_category)
    requirement = normalize_space(key_requirement)
    title = normalize_space(detail_title or "")
    issues: list[str] = []
    categories: list[str] = []
    primary: str | None = None

    if source_category == "AI Literacy & Curriculum":
        primary = "AI Literacy"
        categories.append("AI Literacy")
    elif source_category == "Generative AI in Classrooms":
        primary = "AI Use"
        categories.append("AI Use")
    elif source_category == "Student Data Privacy & AI":
        primary = "Student Privacy"
        categories.append("Student Privacy")
        if _contains_any(
            requirement,
            (
                "regulates ai use",
                "generative ai use",
                "ai collection",
                "ai processing",
                "ai tool",
                "artificial intelligence use",
            ),
        ):
            categories.append("AI Use")
    elif source_category == "Teacher Use of AI / PD":
        supports_use = _contains_any(
            requirement,
            ("teacher use of ai", "educator use of ai", "staff use of ai", "governs teacher use of ai"),
        )
        supports_learning = _contains_any(
            requirement,
            ("professional development", "teacher training", "educator training", "training requirement"),
        )

        # The tracker currently uses a generic either/or Key Requirement for many rows.
        # Use the source-supported detail title as a second signal rather than blindly
        # assigning both categories. This keeps the decision deterministic and reviewable.
        generic_either_or = supports_use and supports_learning and " or " in requirement.lower()
        if generic_either_or:
            categories.clear()
            title_ai = _has_ai_signal(title)
            title_education = _has_education_signal(title)
            title_pd = _contains_any(
                title,
                (
                    "professional development",
                    "teacher training",
                    "educator training",
                    "training",
                ),
            )
            title_use = _contains_any(
                title,
                (
                    "teacher evaluation",
                    "teacher use",
                    "educator use",
                    "staff use",
                ),
            )

            # Stay within the source category's own semantics. A title that points to a
            # different topic (for example school biometrics/privacy) is evidence of a
            # possible source mismatch, not permission to silently recategorize the row.
            if title_ai and title_use and title_pd:
                categories.extend(("AI Use", "AI Literacy"))
            elif title_ai and title_use:
                categories.append("AI Use")
            elif title_ai and title_pd:
                categories.append("AI Literacy")
            else:
                issues.append(
                    "Teacher Use of AI / PD is ambiguous after checking the detail title; manual category review required"
                )
        else:
            if supports_use:
                categories.append("AI Use")
            if supports_learning:
                categories.append("AI Literacy")
            if not categories:
                issues.append("Teacher Use of AI / PD could not be mapped conservatively from Key Requirement")
        primary = categories[0] if categories else None
    else:
        issues.append(f"Unsupported source category: {source_category}")

    if _contains_any(requirement, ("parental consent", "parent consent", "guardian consent", "parent opt-out", "parent opt out")):
        if "Parental Consent" not in categories:
            categories.append("Parental Consent")
    if _contains_any(requirement, ("school procurement", "procurement", "vendor approval", "approved vendor", "purchase of ai")):
        if "School Procurement" not in categories:
            categories.append("School Procurement")

    if primary and primary not in categories:
        categories.insert(0, primary)
    if any(category not in ALLOWED_CATEGORIES for category in categories):
        issues.append(f"Mapped unsupported AI Choice categories: {categories!r}")
    return primary, tuple(categories), issues



IDENTITY_CORE_DOMAINS = {"AI", "EDUCATION", "PRIVACY"}
IDENTITY_OFF_DOMAIN_PATTERNS: dict[str, tuple[str, ...]] = {
    "HEALTH": (
        "health", "medical", "medicine", "physician", "therapy", "psychotherapy",
        "mental health", "hospital", "patient",
    ),
    "ELECTIONS": ("election", "elections", "political", "campaign", "ballot", "candidate"),
    "CRIMINAL_JUSTICE": (
        "criminal", "law enforcement", "police", "offense", "offenses", "arrest", "court", "courts",
    ),
    "VEHICLES_TRANSPORT": ("vehicle", "vehicles", "transportation", "autonomous vehicle"),
    "EMPLOYMENT_LABOR": ("employment", "labor", "workforce", "wage", "worker", "workers"),
    "TAX_FINANCE": (
        "tax", "taxes", "taxation", "financial aid", "finance system", "gross receipts", "motor fuel",
    ),
    "FIREARMS": ("firearm", "firearms", "gun", "guns"),
    "SEXUAL_OFFENSES": ("sexual offense", "sexual offenses", "sex offense", "sex offenses"),
    "VETERANS_MILITARY": ("veteran", "veterans", "military", "jrotc"),
    "RETIREMENT": ("retirement", "pension"),
    "LEGAL_PROFESSIONAL": ("state bar", "bar examination", "bar exam"),
}

IDENTITY_TOKEN_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "under", "over", "this", "that",
    "act", "bill", "statute", "relating", "related", "relative", "regarding", "concerning",
    "amend", "amends", "amending", "code", "title", "state", "states", "public", "private",
    "use", "uses", "using", "program", "programs", "provision", "provisions", "requires",
    "require", "requiring", "prohibit", "prohibits", "prohibiting", "establish", "establishes",
    "establishing", "introduced", "enacted", "department", "commission", "board",
}


def _detail_slug_text(detail_url: str | None) -> str:
    """Return the human-readable title portion encoded in an AI Laws detail URL."""
    if not detail_url:
        return ""
    path = urlparse(detail_url).path.rstrip("/")
    slug = path.split("/")[-1] if path else ""
    text = normalize_space(slug.replace("-", " "))
    # The first slug component normally repeats the bill identifier. Remove only that
    # prefix; keep all remaining wording because it is useful identity evidence.
    text = re.sub(
        r"^(?:HB|SB|AB|HR|SR|SCR|HCR|LD|H|S|A|B)\s*0*\d+\s+",
        "",
        text,
        flags=re.I,
    )
    return normalize_space(text)


def _official_title_text(title: str | None) -> str:
    text = normalize_space(title or "")
    # AI Laws detail headings often prefix the official title with STATE + BILL ID.
    return normalize_space(
        re.sub(
            r"^[A-Z]{2}\s+(?:HB|SB|AB|HR|SR|SCR|HCR|LD|H|S|A|B)\s*0*\d+\s*:\s*",
            "",
            text,
            flags=re.I,
        )
    )


def _identity_tokens(text: str) -> set[str]:
    values = re.findall(r"[a-z0-9]+", normalize_space(text).lower())
    return {
        value
        for value in values
        if len(value) >= 3
        and not value.isdigit()
        and value not in IDENTITY_TOKEN_STOPWORDS
        and not re.fullmatch(r"(?:hb|sb|ab|hr|sr|scr|hcr|ld|h|s|a|b)\d*", value)
    }


def _identity_domains(text: str) -> set[str]:
    result: set[str] = set()
    if _has_ai_signal(text):
        result.add("AI")
    if _has_education_signal(text):
        result.add("EDUCATION")
    if _has_privacy_signal(text):
        result.add("PRIVACY")
    for domain, patterns in IDENTITY_OFF_DOMAIN_PATTERNS.items():
        if _contains_any(text, patterns):
            result.add(domain)
    return result


def bill_identity_review_flags(row: TrackerRow, detail: DetailInfo) -> list[str]:
    """Cross-check AI Laws detail identity against the enriched official title.

    The tracker can contain a correct bill number paired with stale/wrong detail metadata
    from a different legislative session. We therefore compare the title encoded in the
    AI Laws detail URL with the official-government title extracted from the detail page.
    This is a conservative review gate: it does not decide legal substance and it does
    not automatically exclude the row.
    """
    title = _official_title_text(detail.title)
    slug_title = _detail_slug_text(row.research_record_url)
    if not title or not slug_title:
        return []

    title_tokens = _identity_tokens(title)
    slug_tokens = _identity_tokens(slug_title)
    if len(title_tokens) < 2 or len(slug_tokens) < 2:
        return []

    shared_tokens = title_tokens & slug_tokens
    title_domains = _identity_domains(title)
    slug_domains = _identity_domains(slug_title)
    title_off_domains = title_domains - IDENTITY_CORE_DOMAINS
    slug_off_domains = slug_domains - IDENTITY_CORE_DOMAINS

    flags: list[str] = []

    # High-confidence collision: the two titles have no meaningful vocabulary in common
    # and one/both point to different non-education subject matter. This catches reused
    # bill numbers/session collisions such as "school choice" vs "political advertising"
    # without penalizing a legitimate "AI" title paired with an "education" slug.
    if not shared_tokens and (title_off_domains or slug_off_domains):
        if not (title_off_domains & slug_off_domains):
            flags.append(
                "Bill identity mismatch: AI Laws detail URL title and official government title "
                "have no meaningful topic overlap and point to different subject matter"
            )

    # Student-privacy records can be especially vulnerable to a reused bill number. If
    # one side is plainly education-only while the other is privacy/biometric-only and
    # there is no lexical overlap, require review. AI<->education wording by itself is
    # intentionally allowed because that is common for genuine Education AI bills.
    if not shared_tokens and not flags:
        education_only_side = (
            "EDUCATION" in title_domains
            and "PRIVACY" not in title_domains
            and "PRIVACY" in slug_domains
            and "EDUCATION" not in slug_domains
        ) or (
            "EDUCATION" in slug_domains
            and "PRIVACY" not in slug_domains
            and "PRIVACY" in title_domains
            and "EDUCATION" not in title_domains
        )
        if education_only_side:
            flags.append(
                "Bill identity mismatch: education-focused title and privacy/biometric detail identity "
                "do not align for the same bill number"
            )

    # Scope guard: even when the AI Laws slug and official title agree with each other,
    # a strong non-education subject in the official title should not silently enter the
    # Education AI map merely because it contains AI/privacy wording.
    generic_education_requirement = normalize_space(row.key_requirement).lower() in {
        "requires ai literacy education or curriculum standards",
        "regulates generative ai use in educational settings",
        "regulates ai use in k-12 education",
        "protects student data from ai collection or processing",
        "governs teacher use of ai or professional development requirements",
    }
    if (
        generic_education_requirement
        and title_off_domains
        and "EDUCATION" not in title_domains
        and not any(flag.startswith("Bill identity mismatch:") for flag in flags)
    ):
        flags.append(
            "Education-scope mismatch: official government title is centered on a non-education domain "
            f"({', '.join(sorted(title_off_domains))})"
        )

    # AI Choice's EdTech Map is K-12. A tracker row that is plainly limited to
    # postsecondary/higher education should not enter the map merely because the
    # external tracker labels it as education. Mixed K-12 + higher-ed bills remain
    # eligible when the title contains an explicit K-12/school signal.
    if (
        generic_education_requirement
        and _has_postsecondary_signal(title)
        and not _has_k12_signal(title)
        and not any(flag.startswith("Bill identity mismatch:") for flag in flags)
        and not any(flag.startswith("Education-scope mismatch:") for flag in flags)
    ):
        flags.append(
            "Education-scope mismatch: official government title is postsecondary-only and does not show K-12 coverage"
        )

    return flags


def _years_share_known_session(state_name: str, first: int, second: int) -> bool:
    if first == second:
        return True
    low, high = sorted((first, second))
    # These source URLs commonly identify the first year of an odd/even two-year
    # legislative session while tracker rows can be labelled with the second year.
    # Keep the allow-list explicit instead of assuming every state is biennial.
    return (
        normalize_space(state_name) in {"New York", "Washington", "California"}
        and low % 2 == 1
        and high == low + 1
    )


def source_consistency_review_flags(row: TrackerRow, detail: DetailInfo) -> list[str]:
    """Return non-fatal source-alignment concerns that must be reviewed before SQL.

    These are deliberately conservative review flags, not claims that a source is wrong.
    They protect production from tracker/detail joins that are structurally valid but may
    describe an unrelated bill.
    """
    flags: list[str] = []
    title = normalize_space(detail.title or "")
    official_url = normalize_space(detail.official_source_url or "")

    if row.year and official_url:
        url_years = {int(value) for value in re.findall(r"20\d{2}", official_url)}
        if url_years and row.year not in url_years and not any(
            _years_share_known_session(row.state_name, row.year, value) for value in url_years
        ):
            flags.append(
                "WARNING: "
                f"Tracker year {row.year} does not align with explicit year evidence in the official source URL: {sorted(url_years)}"
            )

    if not title:
        return flags

    requirement = normalize_space(row.key_requirement).lower()
    # Title relevance is a guardrail, not a semantic legal classifier. Many valid
    # omnibus bills have broad titles, so block only when the detail title has no
    # visible signal at all for the tracker's education/AI/privacy subject matter.
    relevant_signal = _has_ai_signal(title) or _has_education_signal(title) or _has_privacy_signal(title)
    if requirement in {
        "requires ai literacy education or curriculum standards",
        "regulates generative ai use in educational settings",
        "regulates ai use in k-12 education",
        "protects student data from ai collection or processing",
    } and not relevant_signal:
        flags.append(
            "Detail title has no obvious AI, education/student, or privacy signal for this Education AI Tracker row"
        )

    flags.extend(bill_identity_review_flags(row, detail))
    return flags


def candidate_from_source(row: TrackerRow, detail: DetailInfo, tracker_url: str = SOURCE_URL) -> Candidate:
    candidate = Candidate(raw=row, detail=detail)
    candidate.summary = normalize_space(row.key_requirement)
    candidate.research_source_url = row.research_record_url or tracker_url

    if row.state_name == "United States (Federal)":
        candidate.issues.append("Federal record is outside the state/DC EdTech Map jurisdiction model")
    else:
        try:
            # Keep the existing shared MultiState normalization untouched.
            # DC support belongs to this new source adapter only.
            candidate.state_code = (
                "DC"
                if normalize_space(row.state_name) == "District of Columbia"
                else state_code_for(row.state_name)
            )
        except ValueError as exc:
            candidate.issues.append(str(exc))

    candidate.policy_identifier = normalize_identifier_display(row.identifier_raw)
    candidate.identifier_key = normalized_identifier_key(row.identifier_raw)
    if not candidate.policy_identifier or not candidate.identifier_key:
        candidate.issues.append("Policy identifier could not be normalized")

    primary, categories, category_issues = categories_for_source(
        row.source_category, row.key_requirement, detail.title
    )
    candidate.category = primary
    candidate.categories = categories
    candidate.issues.extend(category_issues)
    candidate.review_flags.extend(source_consistency_review_flags(row, detail))

    status_detail = detail.source_status or row.source_status
    candidate.status_detail = normalize_space(status_detail)
    candidate.status = status_for_source(candidate.status_detail)
    if candidate.status is None:
        candidate.issues.append(f"Unsupported source status: {candidate.status_detail}")
    elif candidate.status not in ALLOWED_STATUS_GROUPS:
        candidate.issues.append(f"Mapped status is not supported by production schema: {candidate.status}")

    # The tracker rows are legislative records. Do not relabel an enacted bill as a law
    # unless the source itself explicitly identifies it as a statute/law.
    identifier_label = normalize_space(row.identifier_raw).lower()
    candidate.policy_type = "Law" if "statute" in identifier_label else "Bill"

    candidate.title = normalize_space(detail.title or "") or (
        canonical_bill_title(row.state_name, candidate.policy_identifier)
        if candidate.policy_identifier
        else None
    )

    candidate.source_url = detail.official_source_url
    if not candidate.source_url:
        candidate.issues.append("Official government/legislative source URL is missing")
    elif "ailawsbystate.com" in urlparse(candidate.source_url).netloc.lower():
        candidate.issues.append("source_url points to the research aggregator instead of an official source")

    candidate.research_source_date = detail.data_updated_date or detail.last_verified_date
    # Use the most recent source-supported freshness signal. On the live tracker,
    # Data Updated can legitimately be later than Last Verified when a bill status has
    # moved since the last separate verification timestamp.
    freshness_dates = [d for d in (detail.data_updated_date, detail.last_verified_date) if d]
    candidate.status_as_of_date = max(freshness_dates) if freshness_dates else None
    candidate.last_updated = detail.data_updated_date or detail.last_verified_date
    candidate.last_verified_at = detail.last_verified_date
    if not candidate.status_as_of_date:
        candidate.issues.append("No source-supported Last Verified/Data Updated date is available for status_as_of_date")
    if not candidate.last_updated:
        candidate.issues.append("No source-supported Data Updated/Last Verified date is available for last_updated")

    # Never infer effective dates from year, last action, publication, or tracker update dates.
    # Populate only when the public detail record explicitly exposes an Effective date.
    candidate.effective_date = detail.effective_date
    candidate.implementation_timeline = None

    if not candidate.summary:
        candidate.issues.append("Key Requirement/summary is empty")
    if not candidate.research_source_url:
        candidate.issues.append("Research source URL is missing")
    return candidate


def fetch_details_for_rows(
    rows: list[TrackerRow],
    *,
    timeout: int = 30,
    continue_on_error: bool = True,
) -> tuple[list[Candidate], list[dict]]:
    session = requests.Session()
    candidates: list[Candidate] = []
    fetch_log: list[dict] = []

    for index, row in enumerate(rows, start=1):
        detail = DetailInfo()
        error = None
        if row.research_record_url:
            try:
                html = fetch_html(row.research_record_url, timeout=timeout, session=session)
                detail = parse_detail_html(html, row.research_record_url)
            except Exception as exc:  # review-only pipeline should preserve row and flag it
                error = f"{type(exc).__name__}: {exc}"
                if not continue_on_error:
                    raise
        else:
            error = "No public detail-page URL was present in the tracker row"

        candidate = candidate_from_source(row, detail)
        if error:
            candidate.issues.append(f"Detail fetch failed: {error}")
        candidates.append(candidate)
        fetch_log.append(
            {
                "row_number": index,
                "state": row.state_name,
                "identifier": row.identifier_raw,
                "detail_url": row.research_record_url,
                "detail_fetch_error": error,
                "official_source_url": detail.official_source_url,
                "last_verified_date": detail.last_verified_date,
                "data_updated_date": detail.data_updated_date,
                "effective_date": detail.effective_date,
                "detail_title": detail.title,
                "detail_status": detail.source_status,
                "last_action_date": detail.last_action_date,
            }
        )
    return candidates, fetch_log
