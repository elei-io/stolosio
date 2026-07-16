from urllib.parse import urlsplit, urlunsplit


def normalize_domain(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or parsed.hostname is None:
            return None
        return parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        return None


def sanitize_url(value: str) -> str | None:
    domain = normalize_domain(value)
    if domain is None:
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = domain if port is None or default_port else f"{domain}:{port}"
    return urlunsplit((scheme, netloc, parsed.path or "/", "", ""))
