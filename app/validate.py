from collections import Counter
from urllib.parse import urlparse

from app.models import PolicyRecord
from app.normalize import ALLOWED_CATEGORIES, ALLOWED_POLICY_TYPES, ALLOWED_STATUS_GROUPS


def _valid_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_records(records: list[PolicyRecord]) -> tuple[list[str], dict]:
    errors: list[str] = []
    seen: set[tuple[str, str, str]] = set()

    for index, record in enumerate(records, start=1):
        prefix = f"row {index} ({record.title})"
        if len(record.state_code) != 2 or not record.state_code.isupper():
            errors.append(f"{prefix}: invalid state_code {record.state_code!r}")
        if not record.state_name.strip():
            errors.append(f"{prefix}: state_name is required")
        if not record.policy_identifier.strip():
            errors.append(f"{prefix}: policy_identifier is required")
        if not record.title.strip():
            errors.append(f"{prefix}: title is required")
        if not record.summary.strip():
            errors.append(f"{prefix}: summary is required")
        if record.policy_type not in ALLOWED_POLICY_TYPES:
            errors.append(f"{prefix}: unsupported policy_type {record.policy_type!r}")
        if record.category not in ALLOWED_CATEGORIES:
            errors.append(f"{prefix}: unsupported primary category {record.category!r}")
        if not record.categories:
            errors.append(f"{prefix}: categories must not be empty")
        unknown_categories = set(record.categories) - ALLOWED_CATEGORIES
        if unknown_categories:
            errors.append(f"{prefix}: unsupported categories {sorted(unknown_categories)!r}")
        if record.category not in record.categories:
            errors.append(f"{prefix}: primary category must be included in categories")
        if record.status not in ALLOWED_STATUS_GROUPS:
            errors.append(f"{prefix}: unsupported status {record.status!r}")
        if not record.status_detail.strip():
            errors.append(f"{prefix}: status_detail is required")
        if not _valid_url(record.source_url):
            errors.append(f"{prefix}: invalid source_url {record.source_url!r}")
        if record.research_source_url and not _valid_url(record.research_source_url):
            errors.append(f"{prefix}: invalid research_source_url {record.research_source_url!r}")
        if not record.status_as_of_date:
            errors.append(f"{prefix}: status_as_of_date is required")
        if not record.last_updated:
            errors.append(f"{prefix}: last_updated is required")

        key = (record.state_code, record.policy_identifier, record.source_url)
        if key in seen:
            errors.append(f"{prefix}: duplicate unique key {key}")
        seen.add(key)

    category_counter = Counter()
    for record in records:
        category_counter.update(record.categories)

    summary = {
        "record_count": len(records),
        "state_count": len({record.state_code for record in records}),
        "states": dict(sorted(Counter(record.state_code for record in records).items())),
        "primary_categories": dict(sorted(Counter(record.category for record in records).items())),
        "all_categories": dict(sorted(category_counter.items())),
        "statuses": dict(sorted(Counter(record.status for record in records).items())),
        "status_details": dict(sorted(Counter(record.status_detail for record in records).items())),
        "policy_types": dict(sorted(Counter(record.policy_type for record in records).items())),
        "error_count": len(errors),
    }
    return errors, summary
