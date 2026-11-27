import csv, io, pytest
from openpyxl import Workbook, load_workbook
from app.exporter import build_csv, build_xlsx, safe_cell
from app.importer import parse_export_file


def test_formula_neutralized_csv_and_xlsx():
    rows = [{"full_name": "=HYPERLINK(\"http://x\",\"y\")", "headline": "+1+1", "title": "-2", "company_name": "@SUM(A1)", "email": "a@b.co"}]
    got = list(csv.reader(io.StringIO(build_csv(rows).decode("utf-8-sig"))))
    c = {h: v for h, v in zip(got[0], got[1])}
    assert c["Name"].startswith("'=") and c["Title"] == "'-2" and c["Headline"] == "'+1+1" and c["Company"] == "'@SUM(A1)"
    wb = load_workbook(io.BytesIO(build_xlsx(rows)))
    assert wb.sheetnames == ["All Leads", "Summary"] and wb.active.title == "All Leads"
    ws = wb["All Leads"]
    assert str(ws["A2"].value).startswith("=HYPERLINK") and ws["A2"].data_type == "s"   # stored as text, never a formula
    assert all(c.data_type != "f" for row in ws.iter_rows() for c in row)


def test_csv_roundtrip_commas_quotes_unicode():
    rows = [{"full_name": 'Ñúñez, "José"', "company_name": "Acme, Inc."}]
    got = list(csv.reader(io.StringIO(build_csv(rows).decode("utf-8-sig"))))
    c = {h: v for h, v in zip(got[0], got[1])}
    assert c["Name"] == 'Ñúñez, "José"' and c["Company"] == "Acme, Inc."


def test_safe_cell_none_and_plain():
    assert safe_cell(None) == "" and safe_cell("hello") == "hello" and safe_cell(5) == "5"


HBF_CSV = ("Person Name,Current Designation,Person LinkedIn URL,Company Name,Hiring Status\n"
           "Asha Rao,Engineering Manager,https://www.linkedin.com/in/asha-rao,Globex,hiring\n"
           "Ravi K,Recruiter,https://www.linkedin.com/in/ravi-k,Umbrella,hiring\n").encode()


def test_import_hbf_csv_uses_export_headers():
    res = parse_export_file("hiringbadge", "x.csv", HBF_CSV)
    assert [r.fields["full_name"] for r in res.records] == ["Asha Rao", "Ravi K"]
    assert res.records[0].fields["title"] == "Engineering Manager" and not res.errors


def test_import_jobharvest_xlsx_export_headers():
    wb = Workbook(); ws = wb.active
    ws.append(["Job Title", "Company Name", "Company URL", "Job ID", "Contact Profile Link(s)"])
    ws.append(["SRE", "Globex", "https://www.linkedin.com/company/globex", "9", "https://www.linkedin.com/in/asha-rao"])
    buf = io.BytesIO(); wb.save(buf)
    res = parse_export_file("jobharvest", "jobs.xlsx", buf.getvalue())
    kinds = sorted(r.kind for r in res.records)
    assert kinds == ["company", "job", "person"]


def test_unknown_headers_clear_error():
    with pytest.raises(ValueError, match="missing expected columns"):
        parse_export_file("hiringbadge", "x.csv", b"foo,bar\n1,2\n")


def test_bad_type_and_size_and_unknown_tool():
    with pytest.raises(ValueError, match="unsupported"):
        parse_export_file("hiringbadge", "x.pdf", b"x")
    with pytest.raises(ValueError, match="too large"):
        parse_export_file("hiringbadge", "x.csv", b"a" * (10 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="unknown tool"):
        parse_export_file("nope", "x.csv", b"a")


def test_xlsx_one_tool_has_no_duplicate_all_leads_sheet():
    one = [{"full_name": "A", "tools": "commentminer", "email": "a@x.co"}, {"full_name": "B", "tools": "commentminer"}]
    wb = load_workbook(io.BytesIO(build_xlsx(one)))
    assert wb.sheetnames == ["CommentMiner", "Summary"] and wb.active.title == "CommentMiner"
    mixed = one + [{"full_name": "C", "tools": "hiringbadge"}]
    assert load_workbook(io.BytesIO(build_xlsx(mixed))).sheetnames == ["All Leads", "Hiring Badge Finder", "CommentMiner", "Summary"]
    assert load_workbook(io.BytesIO(build_xlsx([]))).sheetnames == ["All Leads", "Summary"]


def test_commentminer_sheet_shows_enrichment_title_and_location():
    from app.exporter import TOOL_COLUMNS
    cols = TOOL_COLUMNS["commentminer"]
    assert cols.index("title_only") > cols.index("company_phone") and "location" in cols
    wb = load_workbook(io.BytesIO(build_xlsx([{"full_name": "A", "tools": "commentminer", "headline": "Recruiter at X",
                                                "title": "Bench Sales Recruiter", "location": "Hyderabad"}])))
    ws = wb["CommentMiner"]; hdr = [c.value for c in ws[1]]
    assert ws.cell(row=2, column=hdr.index("Title") + 1).value == "Bench Sales Recruiter"
    assert ws.cell(row=2, column=hdr.index("Location") + 1).value == "Hyderabad"
    assert ws.cell(row=2, column=hdr.index("Headline / Role") + 1).value == "Recruiter at X"


def test_title_column_never_repeats_the_headline():
    rows = [{"full_name": "No Title", "tools": "talentpulse", "headline": "Long about-me text", "title": None},
            {"full_name": "Has Title", "tools": "talentpulse", "headline": "AI Engineer", "title": "AI Engineer"}]
    ws = load_workbook(io.BytesIO(build_xlsx(rows)))["TalentPulse"]; hdr = [c.value for c in ws[1]]
    t, h = hdr.index("Title") + 1, hdr.index("Headline") + 1
    assert (ws.cell(row=2, column=t).value, ws.cell(row=2, column=h).value) in ((None, "Long about-me text"), ("", "Long about-me text"))
    assert ws.cell(row=3, column=t).value == "AI Engineer" and ws.cell(row=3, column=h).value in (None, "")
