from core.discovery import normalize_subdomain


def test_normalize_subdomain_filters_invalid_names() -> None:
    assert normalize_subdomain("*.API.Example.COM.", "example.com") == "api.example.com"
    assert normalize_subdomain("example.com", "example.com") is None
    assert normalize_subdomain("attackerexample.com", "example.com") is None

