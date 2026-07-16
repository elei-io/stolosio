from backend.events.normalization import filter_headers, normalize_domain, sanitize_url


def test_sanitize_url_removes_credentials_query_fragment_and_default_port() -> None:
    assert (
        sanitize_url("https://user:pass@ExAmPle.com.:443/path?q=secret#fragment")
        == "https://example.com/path"
    )


def test_sanitize_url_normalizes_idna_and_rejects_unsafe_schemes() -> None:
    assert sanitize_url("https://bücher.example/") == "https://xn--bcher-kva.example/"
    assert normalize_domain("https://BÜCHER.example./x") == "xn--bcher-kva.example"
    assert sanitize_url("file:///etc/passwd") is None
    assert sanitize_url("javascript:alert(1)") is None
    assert sanitize_url("https://example.com:invalid/x") is None


def test_filter_headers_is_allowlist_only_and_sanitizes_location() -> None:
    assert filter_headers(
        {
            "Content-Type": "text/html",
            "Set-Cookie": "secret=value",
            "Authorization": "Bearer secret",
            "Location": "https://user:pass@example.com/next?token=secret",
            "Retry-After": ["10", "20"],
        }
    ) == {
        "content-type": "text/html",
        "location": "https://example.com/next",
        "retry-after": ["10", "20"],
    }


def test_filter_headers_skips_malformed_provider_values() -> None:
    assert filter_headers({"Content-Length": 42, "Retry-After": ["10", 20]}) == {
        "retry-after": "10"
    }
