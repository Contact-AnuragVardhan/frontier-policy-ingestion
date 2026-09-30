import csv
import json
from pathlib import Path

import requests

NULLABLE_FIELDS = {
    "effective_date",
    "implementation_timeline",
    "research_source_name",
    "research_source_url",
    "research_source_date",
    "last_verified_at",
}


def read_db_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    for row in rows:
        row["categories"] = json.loads(row["categories"])
        for field in NULLABLE_FIELDS:
            row[field] = row[field] or None
    return rows


def upsert_policies(*, supabase_url: str, secret_key: str, rows: list[dict], timeout: int = 45) -> list[dict]:
    if not supabase_url or not secret_key:
        raise ValueError("SUPABASE_URL and SUPABASE_SECRET_KEY are required")
    endpoint = (
        f"{supabase_url.rstrip('/')}/rest/v1/policies"
        "?on_conflict=state_code,policy_identifier,source_url"
    )
    response = requests.post(
        endpoint,
        timeout=timeout,
        headers={
            "apikey": secret_key,
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=representation",
        },
        json=rows,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase load failed ({response.status_code}): {response.text[:1000]}")
    return response.json()
