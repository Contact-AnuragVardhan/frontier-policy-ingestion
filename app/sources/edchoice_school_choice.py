from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from app.models import PolicyRecord
from app.normalize import STATE_CODES, normalize_space, state_code_for

SOURCE_NAME = "EdChoice School Choice Dashboard"
SOURCE_URL = "https://www.edchoice.org/school-choice/dashboard/"
UNIVERSAL_SOURCE_URL = "https://www.edchoice.org/universal-school-choice"
CATEGORY = "Universal School Choice"
QUALIFYING_UNIVERSAL_VALUES = ("Full", "Eligibility")
FULL_UNIVERSAL_VALUE = "Full"
ELIGIBILITY_UNIVERSAL_VALUE = "Eligibility"

EXPECTED_HEADERS = {
    "state",
    "program type",
    "program name",
    "enacted",
    "launched",
    "universal",
}

DATE_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%m/%d/%Y")


@dataclass(frozen=True)
class DashboardRow:
    state_name: str
    program_type: str
    program_name: str
    enacted_year: int | None
    launched_year: int | None
    participation: str | None = None
    participation_rate: str | None = None
    eligibility: str | None = None
    eligibility_rate: str | None = None
    average_funding: str | None = None
    public_funding: str | None = None
    schools: str | None = None
    universal: str = ""
    program_url: str | None = None
    classification_source: str = "dashboard"
    classification_source_url: str | None = SOURCE_URL

    def raw_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DetailInfo:
    title: str | None = None
    enacted_year: int | None = None
    launched_year: int | None = None
    funding_mechanism: str | None = None
    universal_eligibility: bool | None = None
    universal_usage: bool | None = None
    universal_funding: bool | None = None
    truly_universal: bool | None = None
    governing_statute_text: str | None = None
    governing_statute_url: str | None = None
    apply_url: str | None = None
    detail_last_updated: str | None = None
    official_source_url: str | None = None
    official_source_kind: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Candidate:
    raw: DashboardRow
    detail: DetailInfo = field(default_factory=DetailInfo)
    state_code: str | None = None
    policy_identifier: str | None = None
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
    research_source_url: str = SOURCE_URL
    research_source_date: str | None = None
    status_as_of_date: str | None = None
    last_updated: str | None = None
    last_verified_at: str | None = None
    classification: str = "NEW"
    validation_result: str = "PENDING"
    exclusion_reason: str | None = None
    issues: list[str] = field(default_factory=list)
    review_flags: list[str] = field(default_factory=list)
    duplicate_existing_title: str | None = None
    duplicate_existing_source_url: str | None = None

    def has_blocking_review_flags(self) -> bool:
        return any(not flag.startswith("WARNING:") for flag in self.review_flags)

    def is_valid_for_db(self) -> bool:
        return (
            self.classification == "NEW"
            and not self.issues
            and not self.has_blocking_review_flags()
            and bool(self.source_url)
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
            "status_as_of_date": self.status_as_of_date,
            "last_updated": self.last_updated,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"EdChoice candidate is not DB-ready; missing={missing}, issues={self.issues}")
        return PolicyRecord(
            state_code=self.state_code or "",
            state_name=self.raw.state_name,
            policy_identifier=self.policy_identifier or "",
            title=self.title or "",
            summary=self.summary or "",
            policy_type=self.policy_type or "Other",
            category=self.category or CATEGORY,
            categories=self.categories,
            status=self.status or "Unknown",
            status_detail=self.status_detail or "Unknown",
            effective_date=self.effective_date,
            implementation_timeline=self.implementation_timeline,
            source_url=self.source_url or "",
            research_source_name=self.research_source_name,
            research_source_url=self.research_source_url,
            research_source_date=self.research_source_date,
            status_as_of_date=self.status_as_of_date or "",
            last_updated=self.last_updated or "",
            last_verified_at=self.last_verified_at,
        )

    def review_dict(self) -> dict:
        return {
            "classification": self.classification,
            "validation_result": self.validation_result,
            "issues": " | ".join(self.issues),
            "review_flags": " | ".join(self.review_flags),
            "exclusion_reason": self.exclusion_reason or "",
            "source_state": self.raw.state_name,
            "state_code": self.state_code or "",
            "program_type": self.raw.program_type,
            "program_name": self.raw.program_name,
            "dashboard_universal": self.raw.universal,
            "classification_source": self.raw.classification_source,
            "classification_source_url": self.raw.classification_source_url or "",
            "dashboard_enacted": self.raw.enacted_year or "",
            "dashboard_launched": self.raw.launched_year or "",
            "program_url": self.raw.program_url or "",
            "detail_title": self.detail.title or "",
            "detail_truly_universal": _bool_text(self.detail.truly_universal),
            "detail_universal_eligibility": _bool_text(self.detail.universal_eligibility),
            "detail_universal_usage": _bool_text(self.detail.universal_usage),
            "detail_universal_funding": _bool_text(self.detail.universal_funding),
            "funding_mechanism": self.detail.funding_mechanism or "",
            "governing_statute_text": self.detail.governing_statute_text or "",
            "governing_statute_url": self.detail.governing_statute_url or "",
            "policy_identifier": self.policy_identifier or "",
            "title": self.title or "",
            "summary": self.summary or "",
            "policy_type": self.policy_type or "",
            "category": self.category or "",
            "categories": " | ".join(self.categories),
            "status": self.status or "",
            "status_detail": self.status_detail or "",
            "effective_date": self.effective_date or "",
            "implementation_timeline": self.implementation_timeline or "",
            "source_url": self.source_url or "",
            "research_source_name": self.research_source_name,
            "research_source_url": self.research_source_url,
            "research_source_date": self.research_source_date or "",
            "status_as_of_date": self.status_as_of_date or "",
            "last_updated": self.last_updated or "",
            "last_verified_at": self.last_verified_at or "",
            "duplicate_existing_title": self.duplicate_existing_title or "",
            "duplicate_existing_source_url": self.duplicate_existing_source_url or "",
        }


def _bool_text(value: bool | None) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return ""


def fetch_html(url: str, timeout: int = 30, session: requests.Session | None = None) -> str:
    client = session or requests.Session()
    response = client.get(
        url,
        timeout=timeout,
        headers={"User-Agent": "AIChoice-PolicyIngestion/1.0 (+https://www.aichoice.org)"},
    )
    response.raise_for_status()
    return response.text


def _norm_header(value: str) -> str:
    return normalize_space(value).lower().replace("\xa0", " ")


def _find_dashboard_table(soup: BeautifulSoup) -> tuple[Tag, list[str]]:
    for table in soup.find_all("table"):
        headers = [_norm_header(cell.get_text(" ", strip=True)) for cell in table.find_all("th")]
        normalized = {header for header in headers if header}
        if EXPECTED_HEADERS.issubset(normalized):
            return table, headers
    raise ValueError(
        "Could not find the EdChoice dashboard table with State, Program Type, Program Name, "
        "Enacted, Launched, and Universal columns. The page may render data client-side."
    )


def _parse_year(value: str | None) -> int | None:
    value = normalize_space(value or "")
    match = re.search(r"\b(19|20)\d{2}\b", value)
    return int(match.group(0)) if match else None


def _lookup(by_header: dict[str, str], *names: str) -> str | None:
    for name in names:
        if name in by_header:
            value = normalize_space(by_header[name])
            return value or None
    return None


def parse_dashboard_html(html: str, page_url: str = SOURCE_URL) -> list[DashboardRow]:
    soup = BeautifulSoup(html, "html.parser")
    table, headers = _find_dashboard_table(soup)
    rows: list[DashboardRow] = []

    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if not cells:
            continue
        values = [normalize_space(cell.get_text(" ", strip=True)) for cell in cells]
        if len(values) < len(headers):
            # Some data-table plugins omit the leading checkbox header in body rows.
            nonempty_headers = [h for h in headers if h]
            if len(values) == len(nonempty_headers):
                effective_headers = nonempty_headers
            else:
                continue
        else:
            effective_headers = headers

        by_header = {
            effective_headers[i]: values[i]
            for i in range(min(len(effective_headers), len(values)))
            if effective_headers[i]
        }
        state = _lookup(by_header, "state")
        program_type = _lookup(by_header, "program type")
        program_name = _lookup(by_header, "program name")
        universal = _lookup(by_header, "universal")
        if not state or not program_type or not program_name or universal is None:
            continue

        program_url = None
        try:
            program_idx = effective_headers.index("program name")
            anchor = cells[program_idx].find("a", href=True)
            if anchor:
                program_url = urljoin(page_url, anchor["href"].strip())
        except (ValueError, IndexError):
            pass

        rows.append(
            DashboardRow(
                state_name=state,
                program_type=program_type,
                program_name=program_name,
                enacted_year=_parse_year(_lookup(by_header, "enacted")),
                launched_year=_parse_year(_lookup(by_header, "launched")),
                participation=_lookup(by_header, "participation"),
                participation_rate=_lookup(by_header, "participation rate"),
                eligibility=_lookup(by_header, "eligibility"),
                eligibility_rate=_lookup(by_header, "eligibility rate"),
                average_funding=_lookup(by_header, "average funding"),
                public_funding=_lookup(by_header, "public funding"),
                schools=_lookup(by_header, "schools"),
                universal=universal,
                program_url=program_url,
            )
        )

    if not rows:
        raise ValueError("No EdChoice dashboard rows were extracted from the table.")
    return rows


def parse_dashboard_csv(text: str) -> list[DashboardRow]:
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if not reader.fieldnames:
        raise ValueError("EdChoice CSV has no header row")
    headers = {_norm_header(name) for name in reader.fieldnames if name}
    missing = EXPECTED_HEADERS - headers
    if missing:
        raise ValueError(f"EdChoice CSV is missing required columns: {sorted(missing)}")

    rows: list[DashboardRow] = []
    for item in reader:
        normalized = {_norm_header(key): normalize_space(value or "") for key, value in item.items() if key}
        rows.append(
            DashboardRow(
                state_name=normalized.get("state", ""),
                program_type=normalized.get("program type", ""),
                program_name=normalized.get("program name", ""),
                enacted_year=_parse_year(normalized.get("enacted")),
                launched_year=_parse_year(normalized.get("launched")),
                participation=normalized.get("participation") or None,
                participation_rate=normalized.get("participation rate") or None,
                eligibility=normalized.get("eligibility") or None,
                eligibility_rate=normalized.get("eligibility rate") or None,
                average_funding=normalized.get("average funding") or None,
                public_funding=normalized.get("public funding") or None,
                schools=normalized.get("schools") or None,
                universal=normalized.get("universal", ""),
                program_url=normalized.get("program url") or normalized.get("program_url") or None,
            )
        )
    return rows


def parse_dashboard_source_date(html: str) -> str | None:
    text = normalize_space(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    match = re.search(r"last\s+modified\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", text, re.I)
    if not match:
        return None
    return _date_to_iso(match.group(1))


def _heading_by_text(soup: BeautifulSoup, tag: str, text: str) -> Tag | None:
    wanted = normalize_space(text).lower()
    for heading in soup.find_all(tag):
        if normalize_space(heading.get_text(" ", strip=True)).lower() == wanted:
            return heading
    return None


def _nodes_until_next_h2(heading: Tag) -> list[Tag]:
    """Return tags after *heading* in document order until the next h2.

    EdChoice's current WordPress markup nests section content in wrapper divs,
    so the content following an h2 is not necessarily a direct sibling of the
    h2 itself.  Walking only ``find_next_sibling`` therefore misses the live
    Truly Universal state headings and program cards.  ``find_all_next``
    follows document order across those wrappers while the next h2 remains a
    reliable semantic section boundary.
    """
    nodes: list[Tag] = []
    for node in heading.find_all_next(True):
        if node is heading:
            continue
        if node.name == "h2":
            break
        nodes.append(node)
    return nodes


def _state_for_program_anchor(anchor: Tag, section_heading: Tag) -> str | None:
    """Find the state label associated with a program link without relying on CSS classes.

    EdChoice's Universal School Choice page is server-rendered, but the exact wrapper
    classes are presentation details.  Walk from the program link through increasingly
    larger ancestors and use an exact state-name text node when present.  Stop before
    the section wrapper becomes broad enough to include multiple programs.
    """
    node: Tag | None = anchor
    for _ in range(8):
        parent = node.parent if isinstance(node, Tag) else None
        if not isinstance(parent, Tag):
            break
        if parent is section_heading.parent:
            break
        strings = [normalize_space(value) for value in parent.stripped_strings]
        exact_states = [value for value in strings if value in STATE_CODES]
        if len(exact_states) == 1:
            return exact_states[0]
        if len(exact_states) > 1:
            break
        node = parent

    # Fallback for flatter markup: scan backwards from the link until the section
    # heading and take the nearest exact state label.
    for previous in anchor.find_all_previous():
        if previous is section_heading:
            break
        if previous.name not in {"li", "p", "span", "div", "h3", "h4", "h5", "strong"}:
            continue
        value = normalize_space(previous.get_text(" ", strip=True))
        if value in STATE_CODES:
            return value
    return None


def _program_type_for_anchor(anchor: Tag) -> str:
    known_types = (
        "Education Savings Account",
        "Tax-Credit Scholarship",
        "Tax-Credit ESA",
        "Refundable Tax Credits",
        "Refundable Tax Credit",
        "Voucher",
        "Other",
    )
    node: Tag | None = anchor
    for _ in range(6):
        parent = node.parent if isinstance(node, Tag) else None
        if not isinstance(parent, Tag):
            break
        strings = [normalize_space(value) for value in parent.stripped_strings]
        for value in strings:
            for known in known_types:
                if value.lower() == known.lower():
                    return known
        node = parent
    return "Other"


def _programs_in_universal_section(soup: BeautifulSoup, section_name: str, page_url: str) -> dict[str, dict[str, dict]]:
    heading = _heading_by_text(soup, "h2", section_name)
    if heading is None:
        raise ValueError(f"Could not find EdChoice Universal School Choice section: {section_name}")

    result: dict[str, dict[str, dict]] = {}
    seen_anchors: set[int] = set()
    for node in _nodes_until_next_h2(heading):
        anchors: list[Tag] = []
        if node.name == "a" and node.get("href"):
            anchors.append(node)
        anchors.extend(node.find_all("a", href=True))
        for anchor in anchors:
            if id(anchor) in seen_anchors:
                continue
            seen_anchors.add(id(anchor))
            href = normalize_space(anchor.get("href", ""))
            absolute_url = urljoin(page_url, href)
            if "/school-choice/programs/" not in urlparse(absolute_url).path:
                continue
            title = normalize_space(anchor.get_text(" ", strip=True))
            if not title or title.lower() == "learn more":
                continue
            state = _state_for_program_anchor(anchor, heading)
            if not state:
                continue
            result.setdefault(state, {})[absolute_url.rstrip("/") + "/"] = {
                "state": state,
                "program_name": title,
                "program_type": _program_type_for_anchor(anchor),
                "program_url": absolute_url.rstrip("/") + "/",
            }
    return result


def _truly_universal_states(soup: BeautifulSoup) -> list[str]:
    heading = _heading_by_text(soup, "h2", "Truly Universal")
    if heading is None:
        raise ValueError("Could not find the EdChoice Truly Universal section")
    states: list[str] = []
    for node in _nodes_until_next_h2(heading):
        for value in node.stripped_strings:
            state = normalize_space(value)
            if state in STATE_CODES and state not in states:
                states.append(state)
    if not states:
        raise ValueError("EdChoice Truly Universal section did not expose any state names")
    return states


def _is_qualifying_universal_value(value: str | None) -> bool:
    normalized = normalize_space(value or "").lower()
    return normalized in {item.lower() for item in QUALIFYING_UNIVERSAL_VALUES}


def parse_universal_school_choice_html(html: str, page_url: str = UNIVERSAL_SOURCE_URL) -> list[DashboardRow]:
    """Extract EdChoice programs with universal eligibility.

    This is the automatic fallback when the dashboard DataTable is populated only
    client-side. EdChoice's Universal Eligibility section is the authoritative
    fallback for programs available to all K-12 students, including programs that
    automatically become universal after a phase-in period. Programs that also meet
    EdChoice's Truly Universal standard (eligibility + options + funding) are marked
    ``Full``; the remaining universal-eligibility programs are marked ``Eligibility``.
    """
    soup = BeautifulSoup(html, "html.parser")
    true_states = set(_truly_universal_states(soup))
    eligibility = _programs_in_universal_section(soup, "Universal Eligibility", page_url)
    options = _programs_in_universal_section(soup, "Universal Options", page_url)
    funding = _programs_in_universal_section(soup, "Universal Funding", page_url)

    full_program_urls_by_state = {
        state: set(eligibility.get(state, {})) & set(options.get(state, {})) & set(funding.get(state, {}))
        for state in true_states
    }

    rows: list[DashboardRow] = []
    for state, programs in eligibility.items():
        for program_url, item in programs.items():
            universal_value = (
                FULL_UNIVERSAL_VALUE
                if state in true_states and program_url in full_program_urls_by_state.get(state, set())
                else ELIGIBILITY_UNIVERSAL_VALUE
            )
            rows.append(
                DashboardRow(
                    state_name=state,
                    program_type=item["program_type"],
                    program_name=item["program_name"],
                    enacted_year=None,
                    launched_year=None,
                    universal=universal_value,
                    program_url=program_url,
                    classification_source="universal_school_choice_page",
                    classification_source_url=page_url,
                )
            )

    if not rows:
        raise ValueError("No Universal Eligibility EdChoice programs were extracted from the Universal School Choice page")
    rows.sort(key=lambda row: (row.state_name, row.program_name))
    return rows


def _date_to_iso(value: str | None) -> str | None:
    value = normalize_space(value or "")
    if not value:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_bool_marker(value: str | None) -> bool | None:
    value = normalize_space(value or "").lower()
    if not value:
        return None
    if "✅" in value or value in {"yes", "true", "full"}:
        return True
    if "❌" in value or value in {"no", "false", "n/a"}:
        return False
    return None


def _lines(soup: BeautifulSoup) -> list[str]:
    return [normalize_space(line) for line in soup.get_text("\n", strip=True).splitlines() if normalize_space(line)]


def _next_line_after(lines: list[str], label: str) -> str | None:
    target = label.lower()
    for index, line in enumerate(lines[:-1]):
        if line.lower() == target:
            return lines[index + 1]
    return None


def _value_after_prefix(lines: list[str], prefix: str) -> str | None:
    target = prefix.lower()
    for line in lines:
        if line.lower().startswith(target):
            return normalize_space(line[len(prefix):])
    return None


def _is_official_government_url(url: str | None) -> bool:
    if not url:
        return False
    host = urlparse(url).netloc.lower().split(":", 1)[0]
    if not host:
        return False
    if host.endswith(".gov"):
        return True
    # Some official state legislature sites use legacy state.xx.us domains.
    if re.search(r"(^|\.)(?:leg|legislature)\.state\.[a-z]{2}\.us$", host):
        return True
    return False


def _section_anchors(heading: Tag) -> list[Tag]:
    """Collect links in an h2 section across nested WordPress wrappers."""
    anchors: list[Tag] = []
    seen: set[int] = set()
    for node in _nodes_until_next_h2(heading):
        candidates: list[Tag] = []
        if node.name == "a" and node.get("href"):
            candidates.append(node)
        candidates.extend(node.find_all("a", href=True))
        for anchor in candidates:
            if id(anchor) in seen:
                continue
            seen.add(id(anchor))
            anchors.append(anchor)
    return anchors


def parse_detail_html(html: str, detail_url: str) -> DetailInfo:
    soup = BeautifulSoup(html, "html.parser")
    lines = _lines(soup)
    heading = soup.find("h1")
    title = normalize_space(heading.get_text(" ", strip=True)) if heading else None

    enacted_year = _parse_year(_next_line_after(lines, "Enacted:"))
    launched_year = _parse_year(_next_line_after(lines, "Launched:"))
    funding_mechanism = _value_after_prefix(lines, "Funding Mechanism:")
    universal_eligibility = _parse_bool_marker(_value_after_prefix(lines, "Universal Eligibility:"))
    universal_usage = _parse_bool_marker(_value_after_prefix(lines, "Universal Usage:"))
    universal_funding = _parse_bool_marker(_value_after_prefix(lines, "Universal Funding:"))
    truly_universal = _parse_bool_marker(_value_after_prefix(lines, "Truly Universal:"))

    apply_url = None
    for anchor in soup.find_all("a", href=True):
        if normalize_space(anchor.get_text(" ", strip=True)).lower() == "apply now":
            apply_url = urljoin(detail_url, anchor["href"].strip())
            break

    statute_text = None
    statute_url = None
    official_source_url = None
    official_source_kind = None
    for h2 in soup.find_all("h2"):
        if normalize_space(h2.get_text(" ", strip=True)).lower() != "governing statutes":
            continue
        anchors = _section_anchors(h2)
        if anchors:
            statute_text = "; ".join(
                dict.fromkeys(normalize_space(a.get_text(" ", strip=True)) for a in anchors if normalize_space(a.get_text(" ", strip=True)))
            ) or None
            statute_url = urljoin(detail_url, anchors[0]["href"].strip())
            for anchor in anchors:
                href = urljoin(detail_url, anchor["href"].strip())
                if _is_official_government_url(href):
                    official_source_url = href
                    official_source_kind = "governing_statute"
                    break
        break

    if not official_source_url and _is_official_government_url(apply_url):
        official_source_url = apply_url
        official_source_kind = "official_program_page"

    detail_last_updated = None
    # Prefer the update date immediately following the universality block when exposed.
    for index, line in enumerate(lines):
        if line.lower().startswith("truly universal:"):
            for next_line in lines[index + 1:index + 5]:
                match = re.search(r"last updated\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", next_line, re.I)
                if match:
                    detail_last_updated = _date_to_iso(match.group(1))
                    break
            break

    return DetailInfo(
        title=title,
        enacted_year=enacted_year,
        launched_year=launched_year,
        funding_mechanism=funding_mechanism,
        universal_eligibility=universal_eligibility,
        universal_usage=universal_usage,
        universal_funding=universal_funding,
        truly_universal=truly_universal,
        governing_statute_text=statute_text,
        governing_statute_url=statute_url,
        apply_url=apply_url,
        detail_last_updated=detail_last_updated,
        official_source_url=official_source_url,
        official_source_kind=official_source_kind,
    )


def load_official_source_overrides(path: Path | None) -> dict[tuple[str, str], dict]:
    if path is None or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    result: dict[tuple[str, str], dict] = {}
    for item in payload:
        state = normalize_space(item.get("state", ""))
        program = normalize_space(item.get("program_name", ""))
        url = normalize_space(item.get("official_source_url", ""))
        if not state or not program or not url:
            continue
        state_key = state.lower()
        program_key = program.lower()
        result[(state_key, program_key)] = item
        # EdChoice sometimes prefixes the state name on the program detail/
        # universality page while the dashboard omits it (for example Arizona).
        # Treat those source-title variants as aliases for the same reviewed
        # official-source mapping.
        prefix = state_key + " "
        if program_key.startswith(prefix):
            result[(state_key, program_key[len(prefix):])] = item
        else:
            result[(state_key, prefix + program_key)] = item
    return result


def _summary_for(row: DashboardRow, detail: DetailInfo) -> str:
    parts = [f"{row.program_name} is an EdChoice-listed {row.program_type} program."]
    universal_value = normalize_space(row.universal)
    if row.classification_source == "universal_school_choice_page":
        if universal_value.lower() == FULL_UNIVERSAL_VALUE.lower():
            parts.append(
                "EdChoice's Universal School Choice page lists this program under Universal Eligibility, Universal Options, and Universal Funding, meeting its Truly Universal standard."
            )
        else:
            parts.append(
                "EdChoice's Universal School Choice page lists this program under Universal Eligibility, meaning all K-12 students qualify or the program automatically becomes universal after a phase-in period."
            )
    else:
        parts.append(
            f"EdChoice's School Choice in America Dashboard classifies it as {universal_value} in the Universal column."
        )
    if detail.truly_universal is True:
        parts.append("The EdChoice program page also marks it as Truly Universal.")
    elif detail.universal_eligibility is True and universal_value.lower() != FULL_UNIVERSAL_VALUE.lower():
        parts.append("The EdChoice program page marks Universal Eligibility as yes.")
    if row.eligibility_rate:
        parts.append(f"The dashboard reports an eligibility rate of {row.eligibility_rate}.")
    return " ".join(parts)


def candidate_from_source(
    row: DashboardRow,
    detail: DetailInfo,
    *,
    source_date: str | None,
    source_url: str = SOURCE_URL,
    official_source_overrides: dict[tuple[str, str], dict] | None = None,
) -> Candidate:
    candidate = Candidate(raw=row, detail=detail, research_source_url=source_url)
    candidate.research_source_date = source_date
    candidate.status_as_of_date = source_date
    candidate.last_updated = source_date

    try:
        candidate.state_code = state_code_for(row.state_name)
    except ValueError as exc:
        candidate.issues.append(str(exc))

    if not _is_qualifying_universal_value(row.universal):
        candidate.classification = "EXCLUDED"
        candidate.validation_result = "EXCLUDED"
        candidate.exclusion_reason = (
            f"Dashboard Universal value is {row.universal!r}; only {', '.join(QUALIFYING_UNIVERSAL_VALUES)} "
            f"qualify for {CATEGORY}."
        )
        return candidate

    # The dashboard program name is the stable source identity. The detail-page H1,
    # when present, is preferred as the display title because EdChoice sometimes
    # prefixes the state name there (for example, Arizona).
    # Prefer the EdChoice program-page H1 as the stable source identity.  This
    # makes dashboard-table and Universal-page fallback runs converge on the
    # same name even when the dashboard abbreviates the title.
    candidate.policy_identifier = normalize_space(detail.title or row.program_name)
    candidate.title = normalize_space(detail.title or row.program_name)
    candidate.summary = _summary_for(row, detail)
    candidate.policy_type = "Other"
    candidate.category = CATEGORY
    candidate.categories = (CATEGORY,)

    enacted_year = detail.enacted_year or row.enacted_year
    launched_year = detail.launched_year or row.launched_year
    if enacted_year:
        candidate.status = "Enacted"
    else:
        candidate.status = "Unknown"
    detail_parts = []
    if row.enacted_year:
        detail_parts.append(f"EdChoice dashboard enacted year: {row.enacted_year}")
    if detail.enacted_year and detail.enacted_year != row.enacted_year:
        detail_parts.append(f"EdChoice program page enacted year: {detail.enacted_year}")
    if row.launched_year:
        detail_parts.append(f"EdChoice dashboard launched year: {row.launched_year}")
    if detail.launched_year and detail.launched_year != row.launched_year:
        detail_parts.append(f"EdChoice program page launched year: {detail.launched_year}")
    if row.classification_source == "universal_school_choice_page":
        if normalize_space(row.universal).lower() == FULL_UNIVERSAL_VALUE.lower():
            detail_parts.append("EdChoice Universal School Choice page: Universal Eligibility + Universal Options + Universal Funding (Truly Universal)")
        else:
            detail_parts.append("EdChoice Universal School Choice page: Universal Eligibility")
    else:
        detail_parts.append(f"EdChoice dashboard Universal: {normalize_space(row.universal)}")
    if detail.truly_universal is True:
        detail_parts.append("EdChoice program page: Truly Universal")
    candidate.status_detail = "; ".join(detail_parts)
    candidate.effective_date = None  # Never infer from enacted/launched year.
    if row.launched_year and detail.launched_year and row.launched_year != detail.launched_year:
        candidate.implementation_timeline = (
            f"EdChoice dashboard launch year: {row.launched_year}; "
            f"EdChoice program-page launch year: {detail.launched_year}. "
            "The dashboard notes that year may be school-year ending or calendar year."
        )
    elif launched_year:
        candidate.implementation_timeline = f"EdChoice reports the program launched in {launched_year}."
    else:
        candidate.implementation_timeline = None

    candidate.source_url = detail.official_source_url
    overrides = official_source_overrides or {}
    state_key = normalize_space(row.state_name).lower()
    override = overrides.get((state_key, normalize_space(candidate.policy_identifier).lower()))
    if override is None:
        override = overrides.get((state_key, normalize_space(row.program_name).lower()))
    if override:
        override_url = normalize_space(override.get("official_source_url", ""))
        if override_url:
            candidate.source_url = override_url
        verified_at = normalize_space(override.get("verified_at", ""))
        if verified_at:
            candidate.last_verified_at = verified_at

    if not source_date:
        candidate.review_flags.append(
            "EdChoice source date was not available; status_as_of_date cannot be grounded."
        )
    if detail.title:
        dashboard_key = re.sub(r"[^a-z0-9]+", "", normalize_space(row.program_name).lower())
        detail_key = re.sub(r"[^a-z0-9]+", "", normalize_space(detail.title).lower())
        # EdChoice detail pages sometimes add the state name to the same program title.
        identity_matches = dashboard_key == detail_key or dashboard_key in detail_key or detail_key in dashboard_key
        if not identity_matches:
            candidate.review_flags.append(
                f"Program identity mismatch: dashboard={row.program_name!r}, detail={detail.title!r}."
            )
    if row.enacted_year and detail.enacted_year and row.enacted_year != detail.enacted_year:
        candidate.review_flags.append(
            f"WARNING: Dashboard enacted year {row.enacted_year} differs from program page {detail.enacted_year}; both are preserved for review."
        )
    if row.launched_year and detail.launched_year and row.launched_year != detail.launched_year:
        candidate.review_flags.append(
            f"WARNING: Dashboard launched year {row.launched_year} differs from program page {detail.launched_year}; both are preserved because the dashboard notes its year may be school-year ending or calendar year."
        )
    universal_value = normalize_space(row.universal).lower()
    if universal_value == FULL_UNIVERSAL_VALUE.lower() and detail.truly_universal is False:
        candidate.issues.append(
            "Source conflict: EdChoice classification says Universal=Full but program detail page says Truly Universal=false."
        )
    elif universal_value == ELIGIBILITY_UNIVERSAL_VALUE.lower() and detail.universal_eligibility is False:
        candidate.issues.append(
            "Source conflict: EdChoice classification says Universal=Eligibility but program detail page says Universal Eligibility=false."
        )
    elif detail.truly_universal is None and detail.universal_eligibility is None:
        candidate.review_flags.append(
            "WARNING: Program detail page did not expose parseable universality flags; EdChoice source classification retained."
        )
    if not candidate.source_url:
        candidate.review_flags.append(
            "Missing verified official government source URL; keep review-only until an official source is verified."
        )
    elif not _is_official_government_url(candidate.source_url):
        candidate.review_flags.append(
            f"Official source URL is not recognized as a government domain: {candidate.source_url}"
        )

    if not candidate.policy_identifier:
        candidate.issues.append("Missing source-supported program name/policy identifier")
    if not candidate.summary:
        candidate.issues.append("Missing source-grounded summary")

    candidate.validation_result = "PASS" if not candidate.issues and not candidate.has_blocking_review_flags() else "REVIEW"
    return candidate


def fetch_details_for_rows(
    rows: list[DashboardRow],
    *,
    timeout: int = 30,
    session: requests.Session | None = None,
    continue_on_error: bool = True,
) -> tuple[dict[tuple[str, str], DetailInfo], list[dict]]:
    client = session or requests.Session()
    details: dict[tuple[str, str], DetailInfo] = {}
    fetch_log: list[dict] = []
    for row in rows:
        if not _is_qualifying_universal_value(row.universal):
            continue
        key = (row.state_name, row.program_name)
        if not row.program_url:
            details[key] = DetailInfo()
            fetch_log.append({"state": row.state_name, "program_name": row.program_name, "detail_url": None, "error": "missing program URL"})
            continue
        try:
            html = fetch_html(row.program_url, timeout=timeout, session=client)
            info = parse_detail_html(html, row.program_url)
            details[key] = info
            fetch_log.append({"state": row.state_name, "program_name": row.program_name, "detail_url": row.program_url, "error": None, **info.as_dict()})
        except Exception as exc:  # noqa: BLE001 - recorded for review; caller decides whether blocking.
            if not continue_on_error:
                raise
            details[key] = DetailInfo()
            fetch_log.append({"state": row.state_name, "program_name": row.program_name, "detail_url": row.program_url, "error": str(exc)})
    return details, fetch_log
