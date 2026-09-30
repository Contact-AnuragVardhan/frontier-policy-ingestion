from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PolicyRecord:
    state_code: str
    state_name: str
    policy_identifier: str
    title: str
    summary: str
    policy_type: str
    category: str
    categories: tuple[str, ...]
    status: str
    status_detail: str
    effective_date: str | None
    implementation_timeline: str | None
    source_url: str
    research_source_name: str | None
    research_source_url: str | None
    research_source_date: str | None
    status_as_of_date: str
    last_updated: str
    last_verified_at: str | None

    def db_dict(self) -> dict:
        data = asdict(self)
        data["categories"] = list(self.categories)
        return data

    def review_dict(self) -> dict:
        data = self.db_dict()
        data["categories"] = " | ".join(self.categories)
        return data
