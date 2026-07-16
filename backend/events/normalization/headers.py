from collections.abc import Mapping, Sequence

from backend.events.normalization.urls import sanitize_url

_ALLOWED_HEADERS = {"content-type", "content-length", "location", "retry-after"}


def filter_headers(
    headers: Mapping[str, str | Sequence[str]],
) -> dict[str, str | list[str]]:
    filtered: dict[str, str | list[str]] = {}
    for name, raw_value in headers.items():
        if not isinstance(name, str):
            continue
        normalized_name = name.lower()
        if normalized_name not in _ALLOWED_HEADERS:
            continue
        if isinstance(raw_value, str):
            values = [raw_value]
        elif isinstance(raw_value, Sequence) and not isinstance(raw_value, bytes):
            values = [value for value in raw_value if isinstance(value, str)]
        else:
            continue
        if normalized_name == "location":
            values = [value for item in values if (value := sanitize_url(item)) is not None]
        if not values:
            continue
        filtered[normalized_name] = values[0] if len(values) == 1 else values
    return filtered
