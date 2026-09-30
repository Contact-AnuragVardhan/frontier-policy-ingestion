import json
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Tag

from app.models import PolicyRecord
from app.normalize import (
    canonical_bill_title,
    category_for_heading,
    extract_policy_identifier,
    extract_status_and_description,
    normalize_space,
    policy_type_for,
    state_code_for,
    status_group_for,
)

DASH_RE = re.compile(r"\s+[–—-]\s+")
BILL_LINK_RE = re.compile(r"\b(?:HB|SB|AB|A|S)\s*\.?\s*\d+\b", re.I)

# Source-specific enrichment keeps the full policy meaning from Julia's article
# without copying the article's prose verbatim into the Frontier database.
# The primary category always matches the MultiState section; additional categories
# are added only when the source explicitly supports them.
ENRICHMENTS = {
    ("ID", "SB 1227"): {
        "summary": "Creates a statewide K-12 AI framework requiring local AI-use policies, AI literacy standards, educator training and data-privacy safeguards, while prohibiting AI from replacing human teachers.",
        "categories": ("Student Privacy", "AI Use", "AI Literacy"),
        "implementation_timeline": None,
    },
    ("VT", "HB 650"): {
        "summary": "Requires education-technology providers to register and certify privacy compliance annually, with state review, a published product list, and statewide certification criteria covering AI, privacy, geolocation and targeted advertising.",
        "categories": ("Student Privacy",),
        "implementation_timeline": None,
    },
    ("CA", "AB 1159"): {
        "summary": "Expands student-data privacy protections to school-used online services and college students, bars student data from AI training, extends requirements to third-party operators, removes certain exemptions and creates a private right of action.",
        "categories": ("Student Privacy",),
        "implementation_timeline": None,
    },
    ("IL", "SB 3735"): {
        "summary": "Gives families an opt-out from school technology and AI grading decisions and restricts companies' use and retention of student data for AI training without explicit consent.",
        "categories": ("Student Privacy", "Parental Consent", "AI Use"),
        "implementation_timeline": None,
    },
    ("OK", "SB 1734"): {
        "summary": "Allows school AI use only under educator supervision and human review, bans AI for high-stakes student decisions, requires state guidance and district policies, and mandates annual parent disclosure.",
        "categories": ("AI Use",),
        "implementation_timeline": None,
    },
    ("MD", "SB 720"): {
        "summary": "Requires state AI guidance, local school AI policies and AI literacy, integrates AI into workforce standards and educator training, establishes a statewide K-12 AI collaborative, designates AI coordinators and supports certification of compliant AI tools.",
        "categories": ("AI Use", "AI Literacy"),
        "implementation_timeline": None,
    },
    ("MD", "HB 1057"): {
        "summary": "Companion measure requiring state AI guidance, local school AI policies and AI literacy, workforce and educator-training standards, statewide K-12 AI coordination, designated AI coordinators and certification support for compliant AI tools.",
        "categories": ("AI Use", "AI Literacy"),
        "implementation_timeline": None,
    },
    ("NY", "A 9190"): {
        "summary": "Restricts classroom AI use to ninth grade and above except for diagnostics or special-education interventions, while allowing staff to use AI for administrative and planning tasks.",
        "categories": ("AI Use",),
        "implementation_timeline": None,
    },
    ("AZ", "HB 4040"): {
        "summary": "Requires K-12 public schools and public universities to adopt policies governing authorized and unauthorized student AI use in coursework, including prevention measures, guidelines and consequences for violations.",
        "categories": ("AI Use",),
        "implementation_timeline": None,
    },
    ("MS", "SB 2294"): {
        "summary": "Requires a computer-science or career-and-technical-education graduation credit that includes emerging technologies such as artificial intelligence.",
        "categories": ("AI Literacy",),
        "implementation_timeline": "Starting with the 2029-2030 ninth-grade class.",
    },
    ("GA", "SB 179"): {
        "summary": "Makes computer science, including artificial intelligence, a high-school graduation requirement and phases in statewide access to computer-science instruction.",
        "categories": ("AI Literacy",),
        "implementation_timeline": "Beginning in the 2031-2032 school year.",
    },
    ("NJ", "A 4352"): {
        "summary": "Requires school districts to teach AI concepts, skills and ethical use in K-12 and requires public higher-education institutions to offer AI certificate and degree programs.",
        "categories": ("AI Literacy",),
        "implementation_timeline": None,
    },
    ("NJ", "S 2862"): {
        "summary": "Senate companion requiring K-12 instruction on AI concepts, skills and ethical use and requiring public higher-education institutions to offer AI certificate and degree programs.",
        "categories": ("AI Literacy",),
        "implementation_timeline": None,
    },
}
EXPECTED_KEYS = frozenset(ENRICHMENTS)


def fetch_html(url: str, timeout: int = 30) -> str:
    response = requests.get(
        url,
        timeout=timeout,
        headers={
            "User-Agent": "FrontierEducationProject-PolicyIngestion/2.0 (+https://frontiereducationproject.com)"
        },
    )
    response.raise_for_status()
    return response.text


def _iter_bill_items(soup: BeautifulSoup):
    current_category = None
    for node in soup.find_all(["h3", "li"]):
        if node.name == "h3":
            current_category = category_for_heading(node.get_text(" ", strip=True))
            continue
        if node.name != "li" or not current_category:
            continue
        links = node.find_all("a", href=True)
        if not links:
            continue
        text = normalize_space(node.get_text(" ", strip=True))
        if not re.search(r"\b(Enacted|Introduced|Passed 1st Chamber|Passed 2nd Chamber)\s*$", text, re.I):
            continue
        yield current_category, node, links


def _state_name_from_item(item: Tag) -> str:
    text = normalize_space(item.get_text(" ", strip=True))
    state = text.split("(", 1)[0].strip()
    state_code_for(state)
    return state


def _description_and_status(item: Tag) -> tuple[str, str]:
    text = normalize_space(item.get_text(" ", strip=True))
    parts = DASH_RE.split(text, maxsplit=1)
    if len(parts) != 2:
        raise ValueError(f"Could not separate bill label and description: {text}")
    raw_status, source_description = extract_status_and_description(parts[1])
    return source_description, raw_status


def _record_for_bill(
    *,
    state_name: str,
    anchor_text: str,
    source_url: str,
    primary_category: str,
    raw_status: str,
    article_url: str,
    article_date: str,
) -> PolicyRecord:
    state_code = state_code_for(state_name)
    policy_identifier = extract_policy_identifier(anchor_text)
    key = (state_code, policy_identifier)
    try:
        enrichment = ENRICHMENTS[key]
    except KeyError as exc:
        raise ValueError(
            f"Unrecognized MultiState bill {key}. The dated source changed and this adapter needs review before loading."
        ) from exc

    categories = tuple(enrichment["categories"])
    if not categories or categories[0] != primary_category:
        raise ValueError(
            f"Category mismatch for {key}: source section={primary_category!r}, enrichment={categories!r}"
        )

    return PolicyRecord(
        state_code=state_code,
        state_name=state_name,
        policy_identifier=policy_identifier,
        title=canonical_bill_title(state_name, policy_identifier),
        summary=enrichment["summary"],
        policy_type=policy_type_for(raw_status),
        category=primary_category,
        categories=categories,
        status=status_group_for(raw_status),
        status_detail=raw_status,
        effective_date=None,
        implementation_timeline=enrichment["implementation_timeline"],
        source_url=source_url,
        research_source_name="MultiState",
        research_source_url=article_url,
        research_source_date=article_date,
        status_as_of_date=article_date,
        last_updated=article_date,
        last_verified_at=None,
    )


def parse_html(html: str, article_url: str, article_date: str) -> list[PolicyRecord]:
    soup = BeautifulSoup(html, "html.parser")
    records: list[PolicyRecord] = []

    for primary_category, item, links in _iter_bill_items(soup):
        state_name = _state_name_from_item(item)
        _source_description, raw_status = _description_and_status(item)

        for link in links:
            anchor_text = normalize_space(link.get_text(" ", strip=True))
            if not BILL_LINK_RE.search(anchor_text):
                continue
            href = link.get("href", "").strip()
            if not href.startswith("http"):
                continue
            records.append(
                _record_for_bill(
                    state_name=state_name,
                    anchor_text=anchor_text,
                    source_url=href,
                    primary_category=primary_category,
                    raw_status=raw_status,
                    article_url=article_url,
                    article_date=article_date,
                )
            )

    if not records:
        raise ValueError("No policy records were extracted. The source page structure may have changed.")

    actual_keys = {(r.state_code, r.policy_identifier) for r in records}
    missing = sorted(EXPECTED_KEYS - actual_keys)
    unexpected = sorted(actual_keys - EXPECTED_KEYS)
    if missing or unexpected or len(records) != len(EXPECTED_KEYS):
        raise ValueError(
            "MultiState source coverage changed; refusing partial/silent import. "
            f"missing={missing}, unexpected={unexpected}, extracted={len(records)}, expected={len(EXPECTED_KEYS)}"
        )

    return records


def load_fixture(path: Path) -> list[PolicyRecord]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = []
    for item in payload:
        item["categories"] = tuple(item["categories"])
        records.append(PolicyRecord(**item))
    return records


def extract_source_metadata(html: str, article_url: str, article_date: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title = normalize_space(soup.find("h1").get_text(" ", strip=True)) if soup.find("h1") else None
    page_text = normalize_space(soup.get_text(" ", strip=True))

    count_match = re.search(r"tracking\s+(\d+)\s+bills\s+in\s+(\d+)\s+states", page_text, re.I)
    sections = [
        normalize_space(h.get_text(" ", strip=True))
        for h in soup.find_all("h3")
        if category_for_heading(h.get_text(" ", strip=True))
    ]

    return {
        "research_source_name": "MultiState",
        "title": title,
        "url": article_url,
        "source_date": article_date,
        "tracked_bill_count_reported": int(count_match.group(1)) if count_match else 134,
        "tracked_state_count_reported": int(count_match.group(2)) if count_match else 31,
        "named_policy_rows_ingested": len(EXPECTED_KEYS),
        "named_states_ingested": len({state for state, _ in EXPECTED_KEYS}),
        "sections": sections or list(category_for_heading.__globals__["SECTION_CATEGORY_MAP"].keys()),
        "note": "The article reports broader tracking totals but explicitly names only the highlighted Key Bills ingested by this adapter.",
    }
