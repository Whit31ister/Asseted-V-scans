from core.discovery import (
    parse_certspotter,
    parse_crtsh,
    parse_hackertarget,
    parse_otx,
    normalize_subdomain,
)


def test_normalize_subdomain_filters_invalid_names() -> None:
    assert normalize_subdomain("*.API.Example.COM.", "example.com") == "api.example.com"
    assert normalize_subdomain("example.com", "example.com") is None
    assert normalize_subdomain("attackerexample.com", "example.com") is None


def test_source_parsers_normalize_and_deduplicate_names() -> None:
    assert parse_crtsh(
        [{"name_value": "*.API.Example.COM\napi.example.com\noutside.example.net"}],
        "example.com",
    ) == {"api.example.com"}
    assert parse_certspotter(
        [{"dns_names": ["WWW.Example.COM.", "www.example.com"]}], "example.com"
    ) == {"www.example.com"}
    assert parse_otx(
        {"passive_dns": [{"hostname": "mail.example.com"}, {"subdomain": "vpn.example.com"}]},
        "example.com",
    ) == {"mail.example.com", "vpn.example.com"}
    assert parse_hackertarget(
        "api.example.com,192.0.2.1\nwww.example.com,192.0.2.2\nerror timeout",
        "example.com",
    ) == {"api.example.com", "www.example.com"}
