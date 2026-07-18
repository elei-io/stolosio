from backend.proxy.content_comparison import compare_content


def _facts(
    *,
    text: int,
    elements: int,
    links: int,
    images: int,
) -> dict[str, int | bool]:
    return {
        "primary_region_present": True,
        "primary_visible_text_chars": text,
        "primary_meaningful_elements": elements,
        "primary_link_count": links,
        "primary_image_count": images,
    }


def test_materially_incomplete_provider_is_suppressed_by_browser_consensus() -> None:
    results = compare_content(
        {
            "http": _facts(text=4, elements=0, links=0, images=0),
            "browserless": _facts(
                text=1_081, elements=48, links=10, images=33
            ),
            "chromium": _facts(
                text=1_120, elements=51, links=10, images=31
            ),
            "camoufox": _facts(
                text=1_030, elements=46, links=10, images=32
            ),
        },
        {"http", "browserless", "chromium", "camoufox"},
    )

    assert results["http"].state == "materially_incomplete"
    assert results["http"].coverage_percent < 10
    assert results["browserless"].state == "comparable"


def test_one_browser_cannot_establish_relative_reference() -> None:
    results = compare_content(
        {
            "http": _facts(text=4, elements=0, links=0, images=0),
            "browserless": _facts(
                text=1_081, elements=48, links=10, images=33
            ),
        },
        {"http", "browserless"},
    )

    assert results == {}


def test_smaller_but_semantically_populated_document_remains_comparable() -> None:
    results = compare_content(
        {
            "lightpanda": _facts(
                text=700, elements=30, links=8, images=12
            ),
            "browserless": _facts(
                text=1_000, elements=50, links=10, images=20
            ),
            "chromium": _facts(
                text=1_100, elements=52, links=11, images=22
            ),
        },
        {"lightpanda", "browserless", "chromium"},
    )

    assert results["lightpanda"].state == "comparable"


def test_upper_envelope_exposes_browser_that_only_returned_shell() -> None:
    results = compare_content(
        {
            "http": _facts(text=11, elements=1, links=0, images=0),
            "browserless": _facts(text=11, elements=1, links=0, images=0),
            "chromium": _facts(text=11, elements=1, links=0, images=0),
            "camoufox": _facts(text=614, elements=61, links=27, images=23),
        },
        {"http", "browserless", "chromium", "camoufox"},
    )

    assert results["http"].state == "materially_incomplete"
    assert results["browserless"].state == "materially_incomplete"
    assert results["chromium"].state == "materially_incomplete"
    assert results["camoufox"].state == "comparable"
