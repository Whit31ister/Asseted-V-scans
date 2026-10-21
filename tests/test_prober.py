from core.prober import extract_title


def test_extract_title_normalizes_whitespace() -> None:
    assert extract_title("<title>  Example\n Site </title>") == "Example Site"
    assert extract_title("<html>No title</html>") is None
