"""Settings from environment variables. Secrets are never defaulted to real values."""
from __future__ import annotations
import os
from dataclasses import dataclass, field

DEFAULT_TOOL_URLS = {
    "commentminer": "http://15.252.131.112",
    "talentpulse": "http://ec2-15-252-131-112.ap-south-1.compute.amazonaws.com:8000",
    "hiringbadge": "https://hiring-badge-finder-lvu3.onrender.com",
    "jobharvest": "https://jobharvest-xnac.onrender.com",
}
TOOL_LABELS = {
    "commentminer": "CommentMiner", "talentpulse": "TalentPulse",
    "hiringbadge": "Hiring Badge Finder", "jobharvest": "JobHarvest",
}
TOOLS = tuple(DEFAULT_TOOL_URLS)


@dataclass
class Settings:
    database_url: str = "sqlite:///leaddeck.db"
    secret_key: str = ""
    admin_email: str = ""
    admin_password: str = ""
    sync_token: str = ""
    secure_cookies: bool = False
    testing: bool = False
    tool_urls: dict = field(default_factory=lambda: dict(DEFAULT_TOOL_URLS))
    rocketreach_api_key: str = ""
    enrich_credit_cap: int = 25
    enrich_lead_cap: int = 10
    crm_event_token: str = ""


def load_dotenv(path=None) -> None:
    """Tiny .env reader (no extra package). Real environment variables always win."""
    from pathlib import Path
    f = Path(path) if path else Path(__file__).resolve().parent.parent / ".env"
    if not f.is_file():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _int(v, default):
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return default


def get_settings(env=None) -> Settings:
    if env is None:
        load_dotenv()
    env = os.environ if env is None else env
    urls = {k: env.get(f"TOOL_URL_{k.upper()}", v).rstrip("/") for k, v in DEFAULT_TOOL_URLS.items()}
    return Settings(
        database_url=env.get("DATABASE_URL", "sqlite:///leaddeck.db"),
        secret_key=env.get("SECRET_KEY", ""),
        admin_email=env.get("ADMIN_EMAIL", ""),
        admin_password=env.get("ADMIN_PASSWORD", ""),
        sync_token=env.get("SYNC_TOKEN", ""),
        secure_cookies=bool(env.get("RENDER") or env.get("SECURE_COOKIES")),
        tool_urls=urls,
        rocketreach_api_key=env.get("ROCKETREACH_API_KEY", "").strip(),
        enrich_credit_cap=_int(env.get("ENRICH_DEFAULT_CREDIT_CAP"), 25),
        enrich_lead_cap=_int(env.get("ENRICH_DEFAULT_LEAD_CAP"), 10),
        crm_event_token=env.get("CRM_EVENT_TOKEN", "").strip(),
    )
