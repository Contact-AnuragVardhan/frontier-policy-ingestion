from __future__ import annotations

from dataclasses import dataclass

from app.sources.ai_laws_education import Candidate


@dataclass(frozen=True)
class IncludeResolution:
    categories: tuple[str, ...]
    note: str


# These are deliberate post-review decisions made against the official source linked
# by each tracker detail record. Keep them narrow and identifier-specific: this is not
# a replacement for the generic category normalizer.
VERIFIED_INCLUDES: dict[tuple[str, str], IncludeResolution] = {
    ("IL", "SB:1677"): IncludeResolution(
        ("AI Use",),
        "Official-source review: teacher evaluation restrictions concern AI use.",
    ),
    ("IN", "HB:1296"): IncludeResolution(
        ("AI Use",),
        "Official-source review: AI inventory/policy requirements support AI Use.",
    ),
    ("LA", "HR:320"): IncludeResolution(
        ("AI Literacy",),
        "Official-source review: resolution supports AI education/professional development.",
    ),
    ("MS", "SB:2429"): IncludeResolution(
        ("AI Use", "AI Literacy"),
        "Official-source review: AI-in-education task-force scope supports use and literacy/training.",
    ),
    ("MS", "SB:2062"): IncludeResolution(
        ("AI Use", "AI Literacy"),
        "Official-source review: AI-in-education task-force scope supports use and literacy/training.",
    ),
    ("NY", "A:6972"): IncludeResolution(
        ("AI Use", "AI Literacy", "Student Privacy", "School Procurement"),
        "Official-source review: model-policy scope covers AI use, professional development, privacy, and software procurement.",
    ),
    ("NY", "A:6720"): IncludeResolution(
        ("Student Privacy",),
        "Official-source review: school biometric-identification restrictions are a student-privacy policy.",
    ),
    ("NY", "S:3827"): IncludeResolution(
        ("Student Privacy",),
        "Official-source review: school biometric-identification restrictions are a student-privacy policy.",
    ),
}


# These tracker rows were individually reviewed and do not support the tracker's
# Teacher Use of AI / PD classification. They stay in review artifacts but are never
# eligible for SQL.
REVIEWED_EXCLUDES: dict[tuple[str, str], str] = {
    ("CT", "SB:325"): "Official-source review: record concerns public-record/address disclosure rather than classroom/teacher AI.",
    ("HI", "HR:202"): "Official-source review: detail/official record is unrelated to the tracker AI/PD characterization.",
    ("IL", "HB:5321"): "Official-source review: record concerns teacher salary/continuing education rather than an AI requirement.",
    ("MD", "HB:807"): "Official-source review: record concerns English-language-learner teacher competency rather than AI.",
    ("NY", "A:7838"): "Official-source review: record concerns teacher-retirement membership rather than AI.",
    ("NY", "A:10217"): "Official-source review: record concerns teacher-turnover provisions rather than AI.",
    ("OK", "HB:3544"): "Official-source review: official record does not support the tracker Teacher Use of AI / PD characterization.",
}

TEACHER_REVIEW_ISSUE_PREFIXES = (
    "Teacher Use of AI / PD is ambiguous",
    "Teacher Use of AI / PD could not be mapped conservatively",
)



# Rows reviewed after the bill-identity/scope safety pass.  These are kept
# identifier-specific on purpose: the generic safety gate remains conservative
# for future tracker changes, while this list records the decisions from the
# reviewed 2026-09-28 live snapshot.
REVIEWED_IDENTITY_SCOPE_EXCLUDES: set[tuple[str, str]] = {
    ("CA", "SB:903"),
    ("CA", "AB:1979"),
    ("CO", "HB:1210"),
    ("CO", "HB:1195"),
    ("DC", "B:25:832"),
    ("FL", "H:827"),
    ("HI", "SB:278"),
    ("IL", "HB:2575"),
    ("IN", "HB:1047"),
    ("KY", "SB:131"),
    ("MD", "HB:1046"),
    ("MD", "HB:223"),
    ("MD", "SB:655"),
    ("MD", "HB:198"),
    ("MI", "HB:5044"),
    ("MS", "SB:2050"),
    ("NH", "HB:312"),
    ("NJ", "A:168"),
    ("NJ", "S:116"),
    ("NJ", "S:3499"),
    ("NJ", "A:5974"),
    ("NJ", "A:2807"),
    ("NY", "S:2500"),
    ("NY", "S:6428"),
    ("NY", "S:6776"),
    ("NY", "A:9106"),
    ("NY", "S:79"),
    ("NY", "S:8484"),
    ("TN", "SB:1580"),
    ("TX", "HB:2491"),
    ("TX", "HB:2298"),
    ("VT", "H:929"),
    ("VA", "SB:269"),
    ("VA", "HB:668"),
    # K-12 scope exclusions verified after reviewing the final SQL candidate set.
    ("CA", "AB:1651"),
    ("CA", "AB:2392"),
    ("CA", "AB:2504"),
    ("IL", "HB:1859"),
    ("VA", "HJR:32"),
    # Additional reused-bill-number/session collisions verified after the final SQL
    # review.  In each case the Education AI Tracker row and AI Laws detail page /
    # official source resolve to different legislative sessions or subjects.
    ("TN", "HB:1455"),
    ("TX", "HB:2400"),
    ("WA", "SB:6254"),
}

# Reviewed cases where the bill number was reused across legislative sessions and
# the tracker/detail source resolved to the wrong session/subject. These remain
# explicit because the tracker "Year" field is not reliable session evidence.
VERIFIED_REUSED_SESSION_COLLISIONS: set[tuple[str, str]] = {
    ("TN", "HB:1455"),
    ("TX", "HB:2400"),
    ("WA", "SB:6254"),
}

# Reviewed records that are genuinely education-related but outside AI Choice's
# K-12 EdTech Map scope (for example, postsecondary-only or professional licensing).
VERIFIED_K12_SCOPE_EXCLUDES: set[tuple[str, str]] = {
    ("CA", "AB:1651"),
    ("CA", "AB:2392"),
    ("CA", "AB:2504"),
    ("IL", "HB:1859"),
    ("VA", "HJR:32"),
}


SOURCE_MISMATCH_FLAG_FRAGMENT = (
    "Detail title has no obvious AI, education/student, or privacy signal for this Education AI Tracker row"
)


def _identity(candidate: Candidate) -> tuple[str, str] | None:
    if not candidate.state_code or not candidate.identifier_key:
        return None
    return candidate.state_code, candidate.identifier_key


def _clear_teacher_mapping_issues(candidate: Candidate) -> None:
    candidate.issues = [
        issue
        for issue in candidate.issues
        if not issue.startswith(TEACHER_REVIEW_ISSUE_PREFIXES)
    ]


def apply_review_resolutions(candidates: list[Candidate]) -> list[Candidate]:
    """Apply explicit post-review dispositions without hiding the original evidence.

    The generic parser remains conservative. This layer only applies documented,
    identifier-specific decisions and safe exclusion rules. Excluded rows retain their
    original classification/evidence in the review artifacts but are not treated as
    unresolved blockers for generating SQL for independently clean NEW records.
    """
    for candidate in candidates:
        identity = _identity(candidate)

        if identity in VERIFIED_INCLUDES:
            resolution = VERIFIED_INCLUDES[identity]
            candidate.category = resolution.categories[0]
            candidate.categories = resolution.categories
            _clear_teacher_mapping_issues(candidate)
            candidate.review_disposition = "INCLUDE_VERIFIED"
            candidate.review_resolution_note = resolution.note
            continue

        if identity in REVIEWED_EXCLUDES:
            candidate.review_disposition = "EXCLUDE_REVIEWED"
            candidate.review_resolution_note = REVIEWED_EXCLUDES[identity]
            continue

        if identity in REVIEWED_IDENTITY_SCOPE_EXCLUDES:
            if identity in VERIFIED_REUSED_SESSION_COLLISIONS:
                candidate.review_disposition = "EXCLUDE_BILL_IDENTITY_MISMATCH"
                candidate.review_resolution_note = (
                    "Reviewed live record: bill number was reused across legislative sessions and the tracker/detail source resolved to the wrong session or subject; excluded from SQL pending corrected source data."
                )
                continue
            if identity in VERIFIED_K12_SCOPE_EXCLUDES:
                candidate.review_disposition = "EXCLUDE_NON_EDUCATION_SCOPE"
                candidate.review_resolution_note = (
                    "Reviewed live record: education-related policy is outside the AI Choice K-12 EdTech Map scope; excluded from SQL."
                )
                continue
            has_identity_mismatch = any(
                flag.startswith("Bill identity mismatch:") for flag in candidate.review_flags
            )
            has_scope_mismatch = any(
                flag.startswith("Education-scope mismatch:") for flag in candidate.review_flags
            )
            if has_identity_mismatch:
                candidate.review_disposition = "EXCLUDE_BILL_IDENTITY_MISMATCH"
                candidate.review_resolution_note = (
                    "Reviewed 2026-09-28 live record: AI Laws detail identity and official government bill identity do not align; excluded from SQL pending corrected source data."
                )
            elif has_scope_mismatch:
                candidate.review_disposition = "EXCLUDE_NON_EDUCATION_SCOPE"
                candidate.review_resolution_note = (
                    "Reviewed 2026-09-28 live record: official government bill title is outside the Education AI Tracker scope; excluded from SQL."
                )
            else:
                candidate.review_disposition = "EXCLUDE_REVIEWED"
                candidate.review_resolution_note = (
                    "Reviewed 2026-09-28 live record: excluded from SQL after bill-identity/scope review."
                )
            continue

        if candidate.raw.state_name == "United States (Federal)":
            candidate.review_disposition = "EXCLUDE_OUT_OF_SCOPE"
            candidate.review_resolution_note = (
                "Federal record is outside the current state/DC EdTech Map jurisdiction model."
            )
            continue

        if any(SOURCE_MISMATCH_FLAG_FRAGMENT in flag for flag in candidate.review_flags):
            candidate.review_disposition = "EXCLUDE_SOURCE_MISMATCH"
            candidate.review_resolution_note = (
                "Tracker/detail title alignment failed the source-consistency guard; excluded from SQL pending corrected source data."
            )

    return candidates
