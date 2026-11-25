import csv, io, pytest
from openpyxl import Workbook, load_workbook
from app.exporter import build_csv, build_xlsx, safe_cell
from app.importer import parse_export_file


def test_formula_neutralized_csv_and_xlsx():
    rows = [{"full_name": "=HYPERLINK(\"http://x\",\"y\")", "headline": "+1+1", "title": "-2", "company_name": "@SUM(A1)", "email": "a@b.co"}]
    got = list(csv.reader(io.StringIO(build_csv(rows).decode("utf-8-sig"))))
    assert got[1][0].startswith("'=") and got[1][1] == "'-2" and got[1][2] == "'+1+1" and got[1][3] == "'@SUM(A1)"
    ws = load_workbook(io.BytesIO(build_xlsx(rows))).active
    assert str(ws["A2"].value).startswith("'=") and ws["A2"].data_type == "s"


def test_csv_roundtrip_commas_quotes_unicode():
    rows = [{"full_name": 'Ñúñez, "José"', "company_name": "Acme, Inc."}]
    got = list(csv.reader(io.StringIO(build_csv(rows).decode("utf-8-sig"))))
    assert got[1][0] == 'Ñúñez, "José"' and got[1][3] == "Acme, Inc."


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
