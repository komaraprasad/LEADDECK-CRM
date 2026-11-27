import pytest
from app.normalize import normalize_person_url, normalize_company_url, normalize_domain, normalize_name

PERSON = "https://www.linkedin.com/in/jane-doe"

@pytest.mark.parametrize("url", [
    "HTTPS://www.linkedin.com/in/Jane-Doe/?utm=x",
    "in.linkedin.com/in/jane-doe",
    "linkedin.com/in/jane-doe/",
    "https://www.linkedin.com/in/jane%2Ddoe",
    "https://www.linkedin.com/in/jane-doe/details/experience/",
    "  https://uk.linkedin.com/in/JANE-DOE#about ",
])
def test_person_url_variants_collapse(url):
    assert normalize_person_url(url) == PERSON

@pytest.mark.parametrize("url", [None, "", "  ", "https://example.com/in/jane", "https://www.linkedin.com/company/acme", "not a url"])
def test_person_url_non_profiles_are_none(url):
    assert normalize_person_url(url) is None

def test_company_url():
    assert normalize_company_url("https://www.linkedin.com/company/Acme-Inc/about/?x=1") == "https://www.linkedin.com/company/acme-inc"
    assert normalize_company_url("https://www.linkedin.com/in/jane-doe") is None
    assert normalize_company_url(None) is None

def test_domain():
    assert normalize_domain("https://WWW.Acme.com/careers") == "acme.com"
    assert normalize_domain("acme.com") == "acme.com"
    assert normalize_domain("http://acme.co.in:8080/x") == "acme.co.in"
    assert normalize_domain("") is None
    assert normalize_domain("localhost") is None

def test_name():
    assert normalize_name("  José  Ñúñez ") == "jose nunez"
    assert normalize_name(None) == ""


def test_sales_navigator_lead_link_is_a_person_key():
    # TalentPulse sends Sales Navigator links; the lead ID is case-sensitive and kept, the search suffix dropped
    assert normalize_person_url("https://www.linkedin.com/sales/lead/ACwAAB123,NAME") == "https://www.linkedin.com/sales/lead/ACwAAB123"
