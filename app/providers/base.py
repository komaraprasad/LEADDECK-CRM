"""Provider interface for contact enrichment. Providers never touch the database."""
from __future__ import annotations
from dataclasses import dataclass, field


class ProviderError(Exception):
    """Configuration or transport problem. Messages must never contain secrets."""


@dataclass
class ContactValue:
    value: str
    verification: str = "unverified"   # unverified | valid | invalid | unknown
    grade: str | None = None


@dataclass
class ProviderResult:
    status: str                         # matched | not_found | error
    emails: list[ContactValue] = field(default_factory=list)
    phones: list[ContactValue] = field(default_factory=list)
    company_phones: list[ContactValue] = field(default_factory=list)  # employer switchboard/office numbers, not direct lines
    credits_used: int = 0
    error: str | None = None
    matched_name: str | None = None      # who the provider says this profile is (for the wrong-person check)
    matched_employer: str | None = None
    shape: str | None = None        # field names/types of the raw response, never values


class Provider:
    name = "base"
    credits_per_lookup = 1

    def configured(self) -> bool:
        return True

    def estimate(self, n_people: int) -> int:
        return n_people * self.credits_per_lookup

    def lookup(self, person: dict) -> ProviderResult:  # person: linkedin_url, full_name, company_name
        raise NotImplementedError
