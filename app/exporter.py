"""CSV / xlsx export with spreadsheet-formula neutralization.

The xlsx is laid out for people: a Summary sheet, an All Leads sheet and one sheet per tool, each with only the
columns that tool can fill, grouped (Lead, Company, Contact, Location, Source, Status), with readable values,
widths, a frozen header, filters and clickable links. CSV keeps one fixed column set (machine-friendly)."""
from __future__ import annotations
import csv
import io
from datetime import datetime, timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.config import TOOL_LABELS

# (key, header, group, width). Order = order in every sheet.
COLUMNS = [
    ("display_name", "Name", "lead", 24), ("name_source", "Name Source", "lead", 13), ("role", "Title / Headline", "lead", 38),
    ("lead_type", "Lead Type", "lead", 16), ("linkedin_url", "LinkedIn", "lead", 16),
    ("company_name", "Company", "company", 26), ("company_industry", "Company Industry", "company", 24), ("company_size", "Company Size", "company", 13),
    ("email", "Email", "contact", 32), ("email_status", "Email Status", "contact", 12), ("personal_email", "Personal Email", "contact", 28),
    ("all_emails", "All Emails", "contact", 42), ("comment_emails", "Other Emails In Comments", "contact", 34),
    ("phone", "Phone", "contact", 18), ("all_phones", "All Phones", "contact", 32), ("company_phone", "Company Phone", "contact", 18),
    ("location", "Location", "location", 24), ("job_location", "Job Location", "location", 26),
    ("tools", "Found By", "source", 18), ("hiring_for", "Hiring For", "source", 30), ("job_url", "Job Link", "source", 12),
    ("post_title", "Commented On Post", "source", 34), ("post_url", "Post Link", "source", 12), ("comment", "Their Comment", "source", 46),
    ("comment_count", "Comments", "source", 10),
    ("do_not_contact_text", "Do Not Contact", "status", 14), ("last_seen", "Last Seen (UTC)", "status", 17),
]
EXPORT_COLUMNS = [(k, h) for k, h, _, _ in COLUMNS]     # CSV column set (and kept for callers/tests)
_COL = {k: (h, g, w) for k, h, g, w in COLUMNS}

_BASE = ["display_name", "name_source", "role", "lead_type", "linkedin_url", "company_name", "company_industry", "company_size",
         "email", "email_status", "personal_email", "all_emails", "phone", "all_phones", "company_phone", "location"]
_STATUS = ["tools", "do_not_contact_text", "last_seen"]
TOOL_COLUMNS = {
    "hiringbadge": _BASE + _STATUS,
    "jobharvest": _BASE + ["job_location", "hiring_for", "job_url"] + _STATUS,
    "commentminer": _BASE[:12] + ["comment_emails"] + _BASE[12:] + ["post_title", "post_url", "comment", "comment_count"] + _STATUS,
    "talentpulse": _BASE + _STATUS,
}
# Shown even when empty in a sheet: an empty Email/Phone column tells the reader what is still missing.
_ALWAYS = {"display_name", "role", "company_name", "email", "phone", "linkedin_url", "tools"}
_LINKS = {"linkedin_url": "Open profile", "job_url": "Open job", "post_url": "Open post"}
_GROUP_FILL = {"lead": "1E3A8A", "company": "0F766E", "contact": "7C3AED", "location": "B45309", "source": "BE185D", "status": "475569"}
_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(v: object) -> str:
    """Prefix a quote when Excel/Sheets would interpret the cell as a formula (CSV)."""
    if v is None:
        return ""
    s = str(v)
    return "'" + s if s.startswith(_DANGEROUS) else s


def _when(v) -> str:
    s = str(v or "")
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return s


def _value(r: dict, k: str):
    """Readable value for one column."""
    if k == "display_name":
        return r.get("display_name") or r.get("full_name") or ""
    if k == "role":
        return r.get("role") or r.get("title") or r.get("headline") or ""
    v = r.get(k)
    if k == "tools":
        names = v if isinstance(v, list) else [t.strip() for t in str(v or "").split(",") if t.strip()]
        return ", ".join(TOOL_LABELS.get(t, t) for t in names)
    if k == "lead_type":
        return (v or "unknown").replace("_", " ").capitalize()
    if k == "email_status":
        return (v or "").capitalize()
    if k == "last_seen":
        return _when(v)
    if k == "comment_count":
        return v or ""
    return "" if v is None else v


def _rows(rows):
    for r in rows:
        yield [safe_cell(_value(r, k)) for k, _ in EXPORT_COLUMNS]


def build_csv(rows: list[dict]) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf)
    w.writerow([h for _, h in EXPORT_COLUMNS])
    w.writerows(_rows(rows))
    return ("﻿" + buf.getvalue()).encode("utf-8")  # BOM so Excel reads Unicode correctly


def _tools_of(r) -> list[str]:
    v = r.get("tools")
    return v if isinstance(v, list) else [t.strip() for t in str(v or "").split(",") if t.strip()]


def _sheet(wb, title: str, rows: list[dict], keys: list[str]) -> None:
    keys = [k for k in keys if k in _ALWAYS or any(str(_value(r, k) or "").strip() for r in rows)]  # drop empty columns
    ws = wb.create_sheet(title)
    ws.append([_COL[k][0] for k in keys])
    for c, k in enumerate(keys, 1):
        cell = ws.cell(row=1, column=c)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=_GROUP_FILL[_COL[k][1]])
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(c)].width = _COL[k][2]
    for r in rows:
        ws.append([""] * len(keys))
        rn = ws.max_row
        for c, k in enumerate(keys, 1):
            v = _value(r, k)
            cell = ws.cell(row=rn, column=c)
            if k in _LINKS and v:
                cell.value, cell.hyperlink = _LINKS[k], str(v)
                cell.font = Font(color="2563EB", underline="single")
                continue
            cell.value = v if isinstance(v, int) else ("" if v is None else str(v))
            if isinstance(cell.value, str) and cell.value.startswith("="):
                cell.data_type = "s"  # text, never a formula (lead data is untrusted)
            if k == "comment":
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            else:
                cell.alignment = Alignment(vertical="top")
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "B2"
    if rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(keys))}{len(rows) + 1}"


def build_xlsx(rows: list[dict]) -> bytes:
    wb = Workbook()
    summary = wb.active
    summary.title = "Summary"
    per_tool = {t: [r for r in rows if t in _tools_of(r)] for t in TOOL_COLUMNS}
    tools_present = [t for t in TOOL_COLUMNS if per_tool[t]]
    # an export of one tool only: its sheet already is every lead, so no duplicate "All Leads" sheet
    with_all = not (len(tools_present) == 1 and len(per_tool[tools_present[0]]) == len(rows))
    has = lambda rs, k: sum(1 for r in rs if str(_value(r, k) or "").strip())
    summary.append(["LeadDeck export"])
    summary.append(["Exported " + datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC") + "  ·  " + str(len(rows)) + " leads"
                    + ("  ·  " + ", ".join(TOOL_LABELS.get(t, t) for t in tools_present) if tools_present else "")])
    summary.append([])
    summary.append(["Sheet", "Leads", "With email", "With phone", "Employer contacts"])
    sheets = ([("All Leads", rows)] if with_all else []) + [(TOOL_LABELS.get(t, t), per_tool[t]) for t in tools_present]
    for name, rs in sheets:
        summary.append([name, len(rs), has(rs, "email"), has(rs, "phone"), sum(1 for r in rs if r.get("lead_type") == "employer_contact")])
    summary.append([])
    notes = ["Each sheet after this one lists leads; header colours: blue = lead, green = company, purple = contact, "
             "amber = location, pink = where it was found, grey = status.",
             "Columns a sheet has no data for are left out. Links say Open profile / Open job / Open post."]
    if with_all and len(tools_present) > 1:
        notes.insert(0, "A lead found by several tools is on each of those tools' sheets.")
    for n in notes:
        summary.append([n])
        rn = summary.max_row
        summary.merge_cells(start_row=rn, start_column=1, end_row=rn, end_column=5)
        summary.cell(row=rn, column=1).alignment = Alignment(wrap_text=True, vertical="top")
        summary.row_dimensions[rn].height = 32
    summary["A1"].font = Font(bold=True, size=14)
    summary["A2"].font = Font(color="475569")
    summary.merge_cells("A2:E2")
    for c in range(1, 6):
        summary.cell(row=4, column=c).font = Font(bold=True, color="FFFFFF")
        summary.cell(row=4, column=c).fill = PatternFill("solid", fgColor="1E3A8A")
    for col, w in zip("ABCDE", (26, 10, 12, 12, 18)):
        summary.column_dimensions[col].width = w
    if with_all:
        _sheet(wb, "All Leads", rows, [k for k, _ in EXPORT_COLUMNS])
    for t in tools_present:
        _sheet(wb, TOOL_LABELS.get(t, t)[:31], per_tool[t], TOOL_COLUMNS[t])
    if not rows:
        _sheet(wb, "Leads", rows, [k for k, _ in EXPORT_COLUMNS])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
