"""CSV / xlsx export with spreadsheet-formula neutralization."""
from __future__ import annotations
import csv
import io
from openpyxl import Workbook

EXPORT_COLUMNS = [
    # Name: the tool's name, else one read from the LinkedIn link (then Name Source says so)
    ("display_name", "Name"), ("name_source", "Name Source"), ("title", "Title"), ("headline", "Headline"), ("company_name", "Company"), ("company_industry", "Company Industry"), ("company_size", "Company Size"),
    ("hiring_for", "Hiring For"), ("job_url", "Job URL"),
    ("location", "Location"), ("job_location", "Job Location"), ("linkedin_url", "LinkedIn URL"), ("email", "Email"), ("email_status", "Email Status"), ("personal_email", "Personal Email"), ("all_emails", "All Emails"),
    ("phone", "Phone"), ("all_phones", "All Phones"), ("company_phone", "Company Phone"),
    ("lead_type", "Lead Type"), ("do_not_contact_text", "Do Not Contact"), ("tools", "Found By"), ("last_seen", "Last Seen"),
]
_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(v: object) -> str:
    """Prefix a quote when Excel/Sheets would interpret the cell as a formula."""
    if v is None:
        return ""
    s = str(v)
    return "'" + s if s.startswith(_DANGEROUS) else s


def _rows(rows):
    for r in rows:
        yield [safe_cell(r.get(k) or (r.get("full_name") if k == "display_name" else None)) for k, _ in EXPORT_COLUMNS]


def build_csv(rows: list[dict]) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf)
    w.writerow([h for _, h in EXPORT_COLUMNS])
    w.writerows(_rows(rows))
    return ("﻿" + buf.getvalue()).encode("utf-8")  # BOM so Excel reads Unicode correctly


def build_xlsx(rows: list[dict]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Leads"
    ws.append([h for _, h in EXPORT_COLUMNS])
    for r in _rows(rows):
        ws.append(r)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
