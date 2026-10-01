import re

ALLOWED_CATEGORIES = {
    "AI Use",
    "Student Privacy",
    "Parental Consent",
    "AI Literacy",
    "School Procurement",
    "Universal School Choice",
}
ALLOWED_POLICY_TYPES = {"Bill", "Law", "Regulation", "Guidance", "Other"}
ALLOWED_STATUS_GROUPS = {"Pending", "Enacted", "Active", "Inactive", "Unknown"}

SECTION_CATEGORY_MAP = {
    "Student Data Privacy and Protection Measures": "Student Privacy",
    "AI Usage Boundaries and Oversight Requirements": "AI Use",
    "AI Literacy and Graduation Requirements": "AI Literacy",
}

STATUS_GROUP_MAP = {
    "Enacted": "Enacted",
    "Introduced": "Pending",
    "Passed 1st Chamber": "Pending",
    "Passed 2nd Chamber": "Pending",
}

STATE_CODES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY",
}

STATUS_SUFFIX_RE = re.compile(
    r"\s+(Enacted|Introduced|Passed 1st Chamber|Passed 2nd Chamber)\s*$",
    re.I,
)


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def status_group_for(raw_status: str) -> str:
    raw_status = normalize_space(raw_status)
    for source, target in STATUS_GROUP_MAP.items():
        if raw_status.lower() == source.lower():
            return target
    raise ValueError(f"Unsupported legislative status: {raw_status}")


def policy_type_for(raw_status: str) -> str:
    return "Law" if status_group_for(raw_status) == "Enacted" else "Bill"


def category_for_heading(heading: str) -> str | None:
    return SECTION_CATEGORY_MAP.get(normalize_space(heading))


def state_code_for(name: str) -> str:
    try:
        return STATE_CODES[normalize_space(name)]
    except KeyError as exc:
        raise ValueError(f"Unknown state name: {name}") from exc


def extract_status_and_description(text_after_dash: str) -> tuple[str, str]:
    text_after_dash = normalize_space(text_after_dash)
    match = STATUS_SUFFIX_RE.search(text_after_dash)
    if not match:
        raise ValueError(f"Could not find supported status at end of bill text: {text_after_dash}")
    raw_status = normalize_space(match.group(1))
    source_description = normalize_space(text_after_dash[:match.start()])
    return raw_status, source_description


def extract_policy_identifier(anchor_text: str) -> str:
    anchor_text = normalize_space(anchor_text)
    parts = anchor_text.split()
    if parts and len(parts[0]) == 2 and parts[0].isalpha():
        parts = parts[1:]
    identifier = " ".join(parts)
    if not identifier:
        raise ValueError(f"Could not derive policy identifier from {anchor_text!r}")
    return identifier


def canonical_bill_title(state_name: str, policy_identifier: str) -> str:
    return f"{normalize_space(state_name)} {normalize_space(policy_identifier)}"
