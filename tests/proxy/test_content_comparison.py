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
            "browserbase": _facts(
                text=1_120, elements=51, links=10, images=31
            ),
        },
        {"http", "browserless", "browserbase"},
    )

    assert results["http"].state == "materially_incomplete"
    assert results["http"].coverage_percent < 10
    assert results["browserless"].state == "comparable"


def test_browserless_alone_is_the_relative_reference() -> None:
    results = compare_content(
        {
            "http": _facts(text=4, elements=0, links=0, images=0),
            "browserless": _facts(
                text=1_081, elements=48, links=10, images=33
            ),
        },
        {"http", "browserless"},
    )

    assert results["http"].state == "materially_incomplete"
    assert results["browserless"].state == "comparable"


def test_smaller_but_semantically_populated_document_remains_comparable() -> None:
    results = compare_content(
        {
            "http": _facts(
                text=700, elements=30, links=8, images=12
            ),
            "browserless": _facts(
                text=1_000, elements=50, links=10, images=20
            ),
        },
        {"http", "browserless"},
    )

    assert results["http"].state == "comparable"


def test_browserbase_is_not_used_as_a_promotion_reference() -> None:
    results = compare_content(
        {
            "http": _facts(text=11, elements=1, links=0, images=0),
            "browserless": _facts(text=11, elements=1, links=0, images=0),
            "browserbase": _facts(text=614, elements=61, links=27, images=23),
        },
        {"http", "browserless", "browserbase"},
    )

    assert results == {}
