from dataclasses import dataclass

_REFERENCE_PROVIDERS = frozenset({"browserless"})
_PROMOTION_PROVIDERS = frozenset({"http", "browserless"})
_MIN_REFERENCE_PROVIDERS = 1
_INCOMPLETE_COVERAGE = 0.35
_DEFICIENT_COVERAGE = 0.25


@dataclass(frozen=True, slots=True)
class RelativeContentResult:
    state: str
    coverage_percent: int
    deficient_dimensions: int
    reference_facts: dict[str, int]


def compare_content(
    facts_by_provider: dict[str, dict],
    absolutely_healthy: set[str],
) -> dict[str, RelativeContentResult]:
    reference_providers = sorted(
        provider
        for provider in absolutely_healthy
        if provider in _REFERENCE_PROVIDERS and provider in facts_by_provider
    )
    if len(reference_providers) < _MIN_REFERENCE_PROVIDERS:
        return {}

    use_primary = sum(
        bool(facts_by_provider[provider].get("primary_region_present"))
        for provider in reference_providers
    ) >= _MIN_REFERENCE_PROVIDERS
    metrics = (
        (
            "primary_visible_text_chars" if use_primary else "visible_text_chars",
            80,
            4,
        ),
        (
            "primary_meaningful_elements"
            if use_primary
            else "meaningful_elements",
            4,
            2,
        ),
        ("primary_link_count" if use_primary else "link_count", 2, 3),
        ("primary_image_count" if use_primary else "image_count", 2, 1),
    )
    reference_facts = {
        key: max(
            _fact(facts_by_provider[provider], key)
            for provider in reference_providers
        )
        for key, _, _ in metrics
    }

    results: dict[str, RelativeContentResult] = {}
    for provider in sorted(absolutely_healthy & _PROMOTION_PROVIDERS):
        facts = facts_by_provider.get(provider)
        if facts is None:
            continue
        weighted_coverage = 0.0
        total_weight = 0
        deficient = 0
        compared = 0
        for key, minimum, weight in metrics:
            reference = reference_facts[key]
            if reference < minimum:
                continue
            coverage = min(_fact(facts, key) / reference, 1.0)
            weighted_coverage += coverage * weight
            total_weight += weight
            compared += 1
            if coverage < _DEFICIENT_COVERAGE:
                deficient += 1
        if not compared or not total_weight:
            continue
        coverage = weighted_coverage / total_weight
        state = (
            "materially_incomplete"
            if coverage < _INCOMPLETE_COVERAGE and deficient >= 2
            else "comparable"
        )
        results[provider] = RelativeContentResult(
            state=state,
            coverage_percent=round(coverage * 100),
            deficient_dimensions=deficient,
            reference_facts=reference_facts,
        )
    return results


def _fact(facts: dict, key: str) -> int:
    value = facts.get(key, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
