from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.models import PolicyRecord
from app.sources.ai_laws_education import Candidate, normalized_identifier_key


def _url_key(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlsplit(value.strip())
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            query,
            "",
        )
    )


def _existing_year(record: PolicyRecord) -> int | None:
    """Return explicit bill/session-year evidence from the official source URL only.

    The research source date is a snapshot/publication date, not the bill's year, so it
    must never be used to distinguish reused bill identifiers.
    """
    value = record.source_url or ""
    matches = re.findall(r"\b(20\d{2})\b", value)
    if matches:
        return int(matches[-1])
    return None


def _same_known_legislative_session(state_code: str, first: int | None, second: int | None) -> bool:
    if not first or not second:
        return False
    if first == second:
        return True
    low, high = sorted((first, second))
    # Selected source systems identify an odd/even two-year session by its first
    # year while the tracker may label an action with the second year. Keep the
    # allow-list explicit rather than treating every one-year difference as safe.
    return state_code in {"NY", "WA", "CA"} and low % 2 == 1 and high == low + 1


def _merge_recommendation(candidate: Candidate, existing: PolicyRecord) -> str:
    missing_existing = []
    for field in (
        "effective_date",
        "implementation_timeline",
        "last_verified_at",
        "status_as_of_date",
        "last_updated",
    ):
        if not getattr(existing, field, None) and getattr(candidate, field, None):
            missing_existing.append(field)
    if missing_existing:
        return "REVIEW_SAFE_ENRICHMENT:" + ",".join(missing_existing)
    if candidate.status == "Enacted" and existing.status in {"Pending", "Active"}:
        return "REVIEW_STATUS_UPDATE_TO_ENACTED"
    return "PRESERVE_EXISTING_REVIEW_DIFFERENCES"


def _diffs(candidate: Candidate, existing: PolicyRecord) -> list[dict]:
    pairs = {
        "title": (candidate.title, existing.title),
        "summary": (candidate.summary, existing.summary),
        "status": (candidate.status, existing.status),
        "status_detail": (candidate.status_detail, existing.status_detail),
        "category": (candidate.category, existing.category),
        "categories": (" | ".join(candidate.categories), " | ".join(existing.categories)),
        "source_url": (candidate.source_url, existing.source_url),
    }
    diffs = []
    for field, (incoming, current) in pairs.items():
        if field == "source_url":
            same = _url_key(incoming) == _url_key(current)
        else:
            same = incoming == current
        if not same:
            diffs.append(
                {
                    "state_code": candidate.state_code,
                    "policy_identifier": candidate.policy_identifier,
                    "source_year": candidate.raw.year,
                    "field": field,
                    "existing_value": current,
                    "ai_laws_value": incoming,
                    "existing_research_source_name": existing.research_source_name,
                    "existing_research_source_url": existing.research_source_url,
                    "ai_laws_research_source_url": candidate.research_source_url,
                    "resolution": (
                        "REVIEW_INCOMING_VALUE" if current else "SAFE_ENRICHMENT_CANDIDATE"
                    ),
                }
            )
    return diffs


def classify_against_existing(
    candidates: list[Candidate], existing_records: list[PolicyRecord]
) -> tuple[list[Candidate], list[dict], list[dict]]:
    index: dict[tuple[str, str], list[PolicyRecord]] = {}
    for record in existing_records:
        key = (record.state_code, normalized_identifier_key(record.policy_identifier))
        index.setdefault(key, []).append(record)

    duplicates: list[dict] = []
    conflicts: list[dict] = []
    seen_incoming: dict[tuple[str, str, int | None], Candidate] = {}

    for candidate in candidates:
        if candidate.issues:
            candidate.classification = "INVALID"
            continue
        if not candidate.state_code or not candidate.identifier_key:
            candidate.classification = "INVALID"
            candidate.issues.append("Missing normalized deduplication identity")
            continue

        incoming_key = (candidate.state_code, candidate.identifier_key, candidate.raw.year)
        previous = seen_incoming.get(incoming_key)
        if previous is not None:
            message = "Duplicate tracker row for the same state + normalized identifier + source year"
            candidate.classification = "INVALID"
            candidate.issues.append(message)
            previous.classification = "INVALID"
            if message not in previous.issues:
                previous.issues.append(message)
            continue
        seen_incoming[incoming_key] = candidate

        matches = index.get((candidate.state_code, candidate.identifier_key), [])
        if not matches:
            candidate.classification = (
                "POSSIBLE_MATCH_REVIEW" if candidate.has_blocking_review_flags() else "NEW"
            )
            continue

        if len(matches) > 1:
            candidate.classification = "POSSIBLE_MATCH_REVIEW"
            candidate.issues.append(
                f"Multiple existing policies share state + normalized identifier ({len(matches)} matches)"
            )
            continue

        existing = matches[0]
        candidate.duplicate_existing_title = existing.title
        candidate.duplicate_existing_source_url = existing.source_url

        incoming_url_key = _url_key(candidate.source_url)
        existing_url_key = _url_key(existing.source_url)
        same_official_source = bool(incoming_url_key and existing_url_key and incoming_url_key == existing_url_key)
        existing_year = _existing_year(existing)

        # state + normalized identifier is the primary identity. A conflicting year is
        # escalated only when the existing official URL itself provides year evidence and
        # the official URLs are not already the same record. This avoids mistaking a
        # research-source snapshot date for a bill/session year.
        if (
            candidate.raw.year
            and existing_year
            and candidate.raw.year != existing_year
            and not same_official_source
            and not _same_known_legislative_session(candidate.state_code, candidate.raw.year, existing_year)
        ):
            candidate.classification = "POSSIBLE_MATCH_REVIEW"
            candidate.issues.append(
                "Identifier matches an existing policy but explicit official-source year evidence differs"
            )
            duplicates.append(
                {
                    "classification": candidate.classification,
                    "state_code": candidate.state_code,
                    "policy_identifier": candidate.policy_identifier,
                    "normalized_identifier_key": candidate.identifier_key,
                    "ai_laws_year": candidate.raw.year,
                    "existing_year_evidence": existing_year,
                    "existing_title": existing.title,
                    "existing_source_url": existing.source_url,
                    "existing_research_source_name": existing.research_source_name,
                    "existing_research_source_url": existing.research_source_url,
                    "ai_laws_research_source_url": candidate.research_source_url,
                    "reason": "Identifier matches but explicit official-source year evidence differs; review for reused bill number/session",
                    "merge_recommendation": "DO_NOT_MERGE_YEAR_SESSION_CONFLICT",
                }
            )
            continue

        candidate.classification = "DUPLICATE_EXISTING"
        duplicates.append(
            {
                "classification": candidate.classification,
                "state_code": candidate.state_code,
                "policy_identifier": candidate.policy_identifier,
                "normalized_identifier_key": candidate.identifier_key,
                "ai_laws_year": candidate.raw.year,
                "existing_year_evidence": existing_year,
                "existing_title": existing.title,
                "existing_source_url": existing.source_url,
                "existing_research_source_name": existing.research_source_name,
                "existing_research_source_url": existing.research_source_url,
                "ai_laws_research_source_url": candidate.research_source_url,
                "reason": "Same state + normalized policy identifier; existing provenance is preserved",
                "merge_recommendation": _merge_recommendation(candidate, existing),
            }
        )
        conflicts.extend(_diffs(candidate, existing))

    return candidates, duplicates, conflicts
