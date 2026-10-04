"""The capture contract (docs/api.md), exercised through CaptureService with fake fetchers and browser tiers."""

import asyncio

import pytest
from bs4 import BeautifulSoup

from pagecapture import (
    CaptureRequest,
    CaptureService,
    ExcludedUrl,
    Exclusion,
    FetchError,
    HostNotFound,
    HttpResponse,
    RedirectLoop,
    Settings,
    UnsupportedMediaType,
)
from pagecapture.cache import MemoryMethodCache, url_keys
from pagecapture.render import Rendered, content_lines

ARTICLE = (
    "<html><head><title>A long story</title></head><body><main><h1>Headline</h1><h2>Part</h2>"
    + "".join(
        f"<p>Paragraph {i} with enough words to count as real server rendered content here.</p>" for i in range(60)
    )
    + "</main></body></html>"
)
APP_SHELL = (
    '<html><head><title>App</title><script src="/app.js"></script></head>'
    '<body><div id="root"></div><noscript>You need to enable JavaScript to run this app.</noscript></body></html>'
)
CHALLENGE = "<html><head><title>Just a moment...</title></head><body>Performing security verification</body></html>"
BLOCK_PAGE = (
    "<html><head><title>Attention Required! | Cloudflare</title></head><body><h1>Sorry, you have been blocked</h1>"
    "<p>You are unable to access example.test</p><p>Cloudflare Ray ID: 8f1</p></body></html>"
)


class FakeFetcher:
    def __init__(self, status=200, body=ARTICLE, headers=None, error=None):
        self.status, self.body, self.error = status, body, error
        self.headers = headers or [("Content-Type", "text/html; charset=utf-8")]

    async def fetch(self, url, timeout_s, exclusions=(), accept=None):
        if isinstance(self.error, Exception):
            raise self.error
        if self.error:
            raise FetchError(self.error)
        body = self.body.encode() if isinstance(self.body, str) else self.body
        return HttpResponse(url, url, self.status, self.headers, body, [], 12.0)


class FakeTier:
    def __init__(self, tier, html, paid=False, status=200, proxied=False):
        self.tier, self.paid, self.html, self.status, self.calls = tier, paid, html, status, 0
        self.proxied = proxied

    async def render(self, url, deadline_s, exclusions=()):
        self.calls += 1
        text = BeautifulSoup(self.html, "lxml").get_text("\n")
        lines = content_lines(text)
        return Rendered(
            url=url,
            final_url=url,
            status=self.status,
            html=self.html,
            lines=lines or {"x"},
            final_lines=len(lines),
            seconds=3.2,
            steps=[{"step": "parsed", "t": 0.9, "new_lines": 5, "new_items": 0}],
            final_state={"chars": len(text), "mount": False, "pending": 0},
        )


def run(service, url="https://example.test/page", **request):
    return asyncio.run(service.capture(CaptureRequest(url=url, **request)))


def service(fetcher, managed=None, challenge=None, cache=None, local=None):
    s = Settings(browser_ws=None, challenge_browser_ws=None, canary_rate=0.0)
    return CaptureService(
        s,
        fetcher=fetcher,
        managed=managed,
        challenge_resolution=challenge,
        local_resolution=local,
        cache=cache if cache is not None else MemoryMethodCache(),
    )


def test_usable_html_without_a_browser_is_failure_evidence():
    r = run(service(FakeFetcher()))
    assert r.outcome == "failed" and r.failure.code == "browser_unavailable"
    assert r.document.body == ARTICLE.encode()
    assert [a.path for a in r.evidence.attempts] == ["http"] and r.evidence.cost.browser_seconds == 0
    assert r.evidence.attempts[0].decision == "fail"


def test_usable_html_is_rendered_by_default_and_confirmed():
    managed = FakeTier("managed", ARTICLE)
    r = run(service(FakeFetcher(), managed=managed))
    assert r.outcome == "captured" and r.document.representation == "response_body"  # verified: the exact bytes
    comparison = r.evidence.attempts[-1].comparison
    assert comparison["http_sufficient"] and comparison["http_coverage"] >= 0.95


def test_a_confirmed_url_skips_rendering_on_recrawl():
    managed, cache = FakeTier("managed", ARTICLE), MemoryMethodCache()
    svc = service(FakeFetcher(), managed=managed, cache=cache)
    run(svc)
    r = run(svc)
    assert managed.calls == 1 and r.evidence.attempts[0].decision_reason.startswith("cache: this URL")


def test_cached_html_needs_no_browser_but_failed_canary_is_not_success():
    cache = MemoryMethodCache()
    run(service(FakeFetcher(), managed=FakeTier("managed", ARTICLE), cache=cache))
    cached = run(service(FakeFetcher(), cache=cache))
    assert cached.outcome == "captured" and cached.document.body == ARTICLE.encode()
    assert [a.tier for a in cached.evidence.attempts] == ["direct"]

    refused = FakeTier("managed", "<html><body>Forbidden</body></html>", status=403)
    svc = service(FakeFetcher(), managed=refused, cache=cache)
    svc.settings.canary_rate = 1.0
    r = run(svc)
    assert r.outcome == "failed" and r.failure.code == "incomplete_content"
    assert "canary" in r.evidence.attempts[0].decision_reason
    assert r.document.body == ARTICLE.encode()
    entry = asyncio.run(cache.get(url_keys("https://example.test/page")[0]))
    assert entry.comparisons == 1


def test_a_pattern_skips_rendering_after_three_confirmations():
    managed = FakeTier("managed", ARTICLE)
    svc = service(FakeFetcher(), managed=managed)
    for i in (101, 102, 103):
        run(svc, url=f"https://shop.test/p/{i}")
    r = run(svc, url="https://shop.test/p/104")
    assert managed.calls == 3 and r.evidence.attempts[0].decision_reason.startswith(
        "cache: pattern https://shop.test:443/p/{id}"
    )


def test_render_adding_content_is_kept_and_not_cached_as_sufficient():
    richer = ARTICLE.replace(
        "</main>",
        "".join(f"<p>Extra loaded review {i} about the product and its quality.</p>" for i in range(40)) + "</main>",
    )
    managed = FakeTier("managed", richer)
    svc = service(FakeFetcher(), managed=managed)
    r = run(svc)
    assert r.document.representation == "rendered_html" and not r.evidence.attempts[-1].comparison["http_sufficient"]
    run(svc)
    assert managed.calls == 2  # no skip: plain HTTP was not enough here


def test_browser_challenged_while_http_was_usable_keeps_http_as_failure_evidence():
    r = run(service(FakeFetcher(), managed=FakeTier("managed", CHALLENGE)))
    assert r.outcome == "failed" and r.failure.code == "bot_challenge"
    assert r.document.body == ARTICLE.encode()


def test_url_keys_generalise_ids_and_slugs():
    exact, pattern = url_keys("https://www.news.test/world/2026/some-long-article-title-here?id=5&utm=x")
    assert exact == "url:https://www.news.test:443/world/2026/some-long-article-title-here?id=5&utm=x"
    assert pattern == "pattern:https://www.news.test:443/world/{id}/*?id&utm"
    assert (
        url_keys("https://en.wikipedia.org/wiki/Finland")[1] == url_keys("https://en.wikipedia.org/wiki/Web_crawler")[1]
    )
    assert url_keys("https://shop.test/about")[1] != url_keys("https://shop.test/pricing")[1]


def test_app_shell_is_rendered_by_the_managed_tier():
    managed = FakeTier("managed", ARTICLE)
    r = run(service(FakeFetcher(body=APP_SHELL), managed=managed))
    assert r.outcome == "captured" and r.document.representation == "rendered_html"
    assert [(a.path, a.decision) for a in r.evidence.attempts] == [("http", "escalate"), ("browser", "accept")]
    assert managed.calls == 1 and not r.evidence.cost.paid


def test_app_shell_without_a_browser_is_a_gateway_failure():
    r = run(service(FakeFetcher(body=APP_SHELL)))
    assert r.outcome == "failed" and r.failure.code == "browser_unavailable" and r.failure.category == "gateway"


def test_challenge_without_resolution_fails_as_bot_challenge():
    fetcher = FakeFetcher(
        status=403, body=CHALLENGE, headers=[("Content-Type", "text/html"), ("cf-mitigated", "challenge")]
    )
    challenge = FakeTier("challenge_resolution", ARTICLE, paid=True)
    r = run(service(fetcher, challenge=challenge))
    assert r.outcome == "failed" and r.failure.code == "bot_challenge" and r.failure.resolution_attempted is False
    assert challenge.calls == 0 and r.document is not None  # the challenge page is kept as evidence


def test_challenge_with_resolution_uses_the_costlier_tier():
    fetcher = FakeFetcher(
        status=403, body=CHALLENGE, headers=[("Content-Type", "text/html"), ("cf-mitigated", "challenge")]
    )
    challenge = FakeTier("challenge_resolution", ARTICLE, paid=True)
    r = run(service(fetcher, managed=FakeTier("managed", ARTICLE), challenge=challenge), resolve_bot_challenges=True)
    assert r.outcome == "captured" and r.evidence.cost.paid and challenge.calls == 1
    assert r.evidence.attempts[-1].tier == "challenge_resolution"


def test_managed_render_hitting_a_challenge_keeps_the_http_evidence():
    r = run(service(FakeFetcher(body=APP_SHELL), managed=FakeTier("managed", CHALLENGE)))
    assert r.outcome == "failed" and r.failure.code == "bot_challenge"
    assert r.document.representation == "response_body"  # never downgrade to the challenge page


def test_block_page_fails_fast_without_a_proxied_challenge_tier():
    fetcher = FakeFetcher(
        status=403, body=BLOCK_PAGE, headers=[("Content-Type", "text/html"), ("Server", "cloudflare")]
    )
    challenge = FakeTier("challenge_resolution", ARTICLE, paid=True)
    r = run(service(fetcher, challenge=challenge), resolve_bot_challenges=True)
    assert r.outcome == "failed" and r.failure.code == "bot_blocked" and r.failure.resolution_attempted is False
    assert challenge.calls == 0 and not r.evidence.cost.paid  # no paid render that couldn't succeed


def test_block_page_is_tried_on_a_proxied_challenge_tier():
    fetcher = FakeFetcher(
        status=403, body=BLOCK_PAGE, headers=[("Content-Type", "text/html"), ("Server", "cloudflare")]
    )
    challenge = FakeTier("challenge_resolution", ARTICLE, paid=True, proxied=True)
    r = run(service(fetcher, challenge=challenge), resolve_bot_challenges=True)
    assert r.outcome == "captured" and challenge.calls == 1 and r.evidence.cost.paid
    blocked = FakeTier("challenge_resolution", BLOCK_PAGE, paid=True, proxied=True)
    r = run(service(fetcher, challenge=blocked), resolve_bot_challenges=True)
    assert r.failure.code == "bot_blocked" and r.failure.resolution_attempted is True


def test_not_found_is_permanent_and_rate_limit_is_transient_with_retry_after():
    r = run(service(FakeFetcher(status=404, body="<html><title>404 Not Found</title><body>Not found</body></html>")))
    assert (r.failure.code, r.failure.category, r.failure.transient) == ("not_found", "website", False)
    r = run(
        service(
            FakeFetcher(
                status=429, body="Too Many Requests", headers=[("Content-Type", "text/plain"), ("Retry-After", "30")]
            )
        )
    )
    assert (r.failure.code, r.failure.transient, r.failure.retry_after_seconds) == ("rate_limited", True, 30.0)


def test_unreachable_is_a_transient_network_failure():
    r = run(service(FakeFetcher(error="ConnectError: temporary failure in name resolution")))
    assert (r.outcome, r.failure.code, r.failure.category, r.failure.transient) == (
        "failed",
        "unreachable",
        "network",
        True,
    )


def test_a_host_that_does_not_exist_is_a_permanent_network_failure():
    r = run(service(FakeFetcher(error=HostNotFound("missing.example.test"))))
    assert (r.outcome, r.failure.code, r.failure.category, r.failure.transient) == (
        "failed",
        "host_not_found",
        "network",
        False,
    )
    assert r.evidence.attempts[0].assessment.primary == "unreachable"


def test_a_redirect_loop_is_a_permanent_website_failure():
    r = run(service(FakeFetcher(error=RedirectLoop("more than 10 redirects"))))
    assert (r.outcome, r.failure.code, r.failure.category, r.failure.transient) == (
        "failed",
        "redirect_loop",
        "website",
        False,
    )
    assert r.failure.retry_after_seconds is None and r.document is None


def test_non_html_documents_are_captured_as_sent():
    r = run(service(FakeFetcher(body=b"%PDF-1.7 ...", headers=[("Content-Type", "application/pdf")])))
    assert r.outcome == "captured" and r.document.media_type == "application/pdf"


def test_json_shape_matches_the_contract():
    out = run(service(FakeFetcher())).to_json()
    assert set(out) == {
        "schema",
        "reference",
        "outcome",
        "requested_url",
        "final_url",
        "started_at",
        "finished_at",
        "response",
        "document",
        "failure",
        "evidence",
    }
    assert {"representation", "media_type", "charset", "body_base64", "content_sha256", "content_bytes"} == set(
        out["document"]
    )
    assert {"attempts", "cost", "versions"} == set(out["evidence"])


def test_request_validation():
    assert CaptureRequest.from_json({"url": "https://a.test/", "resolve_bot_challenges": True}).resolve_bot_challenges
    with pytest.raises(ValueError):
        CaptureRequest.from_json({"url": "ftp://a.test/"})
    with pytest.raises(ValueError):
        CaptureRequest.from_json({"url": "https://a.test/", "render": "always"})


class RaisingTier(FakeTier):
    def __init__(self, error):
        super().__init__("challenge_resolution", "", paid=True)
        self.error = error

    async def render(self, url, deadline_s, exclusions=()):
        self.calls += 1
        raise self.error


def test_solver_giving_up_is_a_bot_challenge_with_resolution_attempted():
    from pagecapture.adapters import ChallengeNotPassed

    fetcher = FakeFetcher(
        status=403, body=CHALLENGE, headers=[("Content-Type", "text/html"), ("cf-mitigated", "challenge")]
    )
    tier = RaisingTier(ChallengeNotPassed("BrowserQL: Captcha solving timed out after 60000 ms"))
    r = run(service(fetcher, challenge=tier), resolve_bot_challenges=True)
    assert (r.failure.code, r.failure.resolution_attempted) == ("bot_challenge", True)


def test_refused_browser_connection_after_a_block_page_is_bot_blocked():
    class RefusedTier(FakeTier):
        async def render(self, url, deadline_s, exclusions=()):
            return Rendered(
                url=url,
                final_url="chrome-error://chromewebdata/",
                html="",
                seconds=5.0,
                error="navigation failed: ERR_HTTP2_PROTOCOL_ERROR",
            )

    fetcher = FakeFetcher(status=403, body=BLOCK_PAGE, headers=[("Content-Type", "text/html")])
    tier = RefusedTier("challenge_resolution", "", paid=True, proxied=True)
    r = run(service(fetcher, challenge=tier), resolve_bot_challenges=True)
    assert (r.failure.code, r.failure.resolution_attempted) == ("bot_blocked", True)
    assert "ERR_HTTP2_PROTOCOL_ERROR" in r.failure.message


def test_a_busy_fleet_is_a_transient_capacity_failure():
    from pagecapture.render import BrowserCapacity

    class Busy(FakeTier):
        async def render(self, url, deadline_s, exclusions=()):
            raise BrowserCapacity("no browser capacity: 503")

    r = run(service(FakeFetcher(body=APP_SHELL), managed=Busy("managed", "")))
    assert (r.failure.code, r.failure.category, r.failure.transient) == ("capacity", "gateway", True)


def test_a_refusing_challenge_provider_is_capacity_with_its_retry_after():
    from pagecapture.render import BrowserCapacity

    fetcher = FakeFetcher(
        status=403, body=CHALLENGE, headers=[("Content-Type", "text/html"), ("cf-mitigated", "challenge")]
    )
    tier = RaisingTier(BrowserCapacity("Browserless refused (concurrency): HTTP 429", retry_after_seconds=30.0))
    r = run(service(fetcher, challenge=tier), resolve_bot_challenges=True)
    assert (r.failure.code, r.failure.transient, r.failure.retry_after_seconds) == ("capacity", True, 30.0)


def test_a_challenge_provider_outage_stays_browser_unavailable():
    fetcher = FakeFetcher(
        status=403, body=CHALLENGE, headers=[("Content-Type", "text/html"), ("cf-mitigated", "challenge")]
    )
    tier = RaisingTier(RuntimeError("BrowserQL HTTP 502: Bad Gateway"))
    r = run(service(fetcher, challenge=tier), resolve_bot_challenges=True)
    assert (r.failure.code, r.failure.retry_after_seconds) == ("browser_unavailable", None)


@pytest.mark.parametrize("status", [403, 429, 500])
def test_browser_refused_with_an_error_status_keeps_plain_html_as_failure_evidence(status):
    refused = FakeTier(
        "managed",
        "<html><head><title>403 Forbidden</title></head><body><h1>403 Forbidden</h1></body></html>",
        status=status,
    )
    r = run(service(FakeFetcher(), managed=refused))
    assert r.outcome == "failed" and r.failure.code == "incomplete_content" and r.failure.transient
    assert r.document.body == ARTICLE.encode()
    assert any(f"HTTP {status}" in n for n in r.evidence.attempts[0].notes)


def test_search_shell_with_surrounding_text_is_not_success_when_browser_is_refused():
    # Oikotie: substantial introductory/navigation text hides the absent JS-loaded listings from classification.
    shell = ARTICLE.replace("</main>", '<div id="listing-results"></div><script src="/search.js"></script></main>')
    cache = MemoryMethodCache()
    refused = FakeTier("managed", "<html><body>Forbidden</body></html>", status=403)
    svc = service(FakeFetcher(body=shell), managed=refused, cache=cache)
    r = run(svc)
    assert r.evidence.attempts[0].assessment.primary is None
    assert r.outcome == "failed" and r.failure.code == "incomplete_content"
    assert r.document.body == shell.encode() and r.evidence.attempts[-1].decision == "fail"
    assert asyncio.run(cache.get(url_keys("https://example.test/page")[0])) is None
    run(svc)
    assert refused.calls == 2


@pytest.mark.parametrize("code", ["capacity", "browser_unavailable", "bot_challenge"])
def test_browser_exception_cannot_promote_unverified_html_to_success(code):
    from pagecapture.adapters import ChallengeNotPassed
    from pagecapture.render import BrowserCapacity

    error = {
        "capacity": BrowserCapacity("busy", retry_after_seconds=30),
        "browser_unavailable": RuntimeError("unavailable"),
        "bot_challenge": ChallengeNotPassed("challenge held"),
    }[code]
    tier = RaisingTier(error)
    tier.tier, tier.paid = "managed", False
    r = run(service(FakeFetcher(), managed=tier))
    assert r.outcome == "failed" and r.failure.code == code
    assert r.document.body == ARTICLE.encode()
    if code == "capacity":
        assert r.failure.retry_after_seconds == 30


@pytest.mark.parametrize("html", ["", "<html><body></body></html>"])
def test_empty_render_cannot_promote_unverified_html_to_success(html):
    r = run(service(FakeFetcher(), managed=FakeTier("managed", html)))
    assert r.outcome == "failed"
    assert r.failure.code == ("browser_unavailable" if not html else "incomplete_content")
    assert r.document.body == ARTICLE.encode()


def test_insufficient_render_budget_preserves_html_as_deadline_failure():
    svc = service(FakeFetcher(), managed=FakeTier("managed", ARTICLE))
    r = run(svc, deadline_ms=4000)
    assert r.outcome == "failed" and r.failure.code == "deadline_exceeded"
    assert r.document.body == ARTICLE.encode() and svc.managed.calls == 0


@pytest.mark.asyncio
async def test_slow_cache_lookup_is_bounded_and_retains_plain_html():
    class SlowCache(MemoryMethodCache):
        async def get(self, key):
            await asyncio.Event().wait()

    svc = service(FakeFetcher(), managed=FakeTier("managed", ARTICLE), cache=SlowCache())
    async with asyncio.timeout(3):
        result = await svc.capture(CaptureRequest(url="https://example.test/slow-cache", deadline_ms=100))
    assert result.failure.code == "deadline_exceeded"
    assert result.document.body == ARTICLE.encode()
    assert result.evidence.cost.bytes == len(ARTICLE.encode())
    assert result.evidence.attempts[-1].decision == "fail"
    assert svc.managed.calls == 0


def test_navigation_only_render_with_empty_main_cannot_verify_an_article():
    navigation = (
        "<html><head><title>Navigation</title></head><body><nav>"
        + "".join(f"<a href='/year/{i}'>Year in review {i}</a>" for i in range(40))
        + "</nav><main></main></body></html>"
    )
    cache = MemoryMethodCache()
    result = run(service(FakeFetcher(), managed=FakeTier("managed", navigation), cache=cache))
    assert result.outcome == "failed" and result.failure.code == "incomplete_content"
    assert result.document.body == ARTICLE.encode()
    assert asyncio.run(cache.get(url_keys("https://example.test/page")[0])) is None


def test_renderer_restarts_a_dead_driver_once_and_retries():
    from pagecapture.render import Rendered, Renderer
    from pagecapture.render.adaptive import DRIVER_GONE

    class Flaky(Renderer):
        def __init__(self):
            super().__init__(Settings(browser_ws="ws://fleet"))
            self.calls, self.restarts, self._pw = 0, 0, object()

        async def _render(self, url, deadline_s, attach_ws, exclusions, endpoint=None):
            self.calls += 1
            if self.calls == 1:
                raise Exception(f"Page.evaluate: {DRIVER_GONE}")
            return Rendered(url=url, html="<html></html>", lines={"x"})

        async def _restart_driver(self, dead):
            self.restarts += 1
            self._pw = object()

    r = Flaky()
    out = asyncio.run(r.render("https://example.test/", 60))
    assert out.lines == {"x"} and r.calls == 2 and r.restarts == 1


def test_empty_plain_page_and_empty_render_is_incomplete_not_captured():
    empty = "<html><head><title>Welcome</title></head><body></body></html>"
    r = run(service(FakeFetcher(body=empty), managed=FakeTier("managed", empty)))
    assert r.outcome == "failed" and r.failure.code == "incomplete_content"


def test_empty_plain_page_with_a_small_render_returns_the_render():
    small = "<html><head><title>Telegram</title></head><body><p>Log in to Telegram by QR code</p></body></html>"
    r = run(
        service(FakeFetcher(body="<html><body><div id='x'></div></body></html>"), managed=FakeTier("managed", small))
    )
    assert r.outcome == "captured" and r.document.representation == "rendered_html"


# ---- schema v2: exclusions, accept, size cap ----


class RaisingFetcher:
    def __init__(self, error):
        self.error = error

    async def fetch(self, url, timeout_s, exclusions=(), accept=None):
        raise self.error


HTML_ONLY = ("text/html", "application/xhtml+xml", "application/xml", "text/xml")


def test_a_redirect_to_an_excluded_url_fails_as_excluded():
    r = run(service(RaisingFetcher(ExcludedUrl("https://example.test/private"))))
    assert r.outcome == "failed" and r.document is None
    assert (r.failure.code, r.failure.category, r.failure.transient) == ("excluded", "content", False)


def test_an_unaccepted_media_type_fails_without_a_document():
    response = HttpResponse(
        "https://example.test/a.pdf", "https://example.test/a.pdf", 200, [("Content-Type", "application/pdf")], b""
    )
    r = run(service(RaisingFetcher(UnsupportedMediaType(response, "application/pdf"))), accept=HTML_ONLY)
    assert r.outcome == "failed" and r.document is None and r.response.status_code == 200
    assert (r.failure.code, r.failure.category, r.failure.transient) == ("unsupported_media_type", "content", False)


def test_an_undeclared_media_type_is_sniffed_against_accept():
    unlabelled = FakeFetcher(headers=[("Server", "x")], body=b"\x89PNG binary")
    r = run(service(unlabelled), accept=HTML_ONLY)
    assert r.failure.code == "unsupported_media_type" and r.document is None
    r = run(service(FakeFetcher(headers=[("Server", "x")]), managed=FakeTier("managed", ARTICLE)), accept=HTML_ONLY)
    assert r.outcome == "captured" and r.document.media_type == "text/html"


def test_an_accepted_xml_document_is_captured_as_sent():
    xml = '<?xml version="1.0"?><urlset><url><loc>https://example.test/</loc></url></urlset>'
    r = run(service(FakeFetcher(body=xml, headers=[("Content-Type", "application/xml")])), accept=HTML_ONLY)
    assert r.outcome == "captured" and r.document.media_type == "application/xml"


URLSET = (
    '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    + "".join(f"<url><loc>https://example.test/p/{i}</loc></url>" for i in range(50))
    + "</urlset>"
)
XML_VIEWER = (
    "<html><body><div>This XML file does not appear to have any style information associated with it.</div>"
    + "".join(f"<p>https://example.test/p/{i} and more words in the browser's tree view</p>" for i in range(80))
    + "</body></html>"
)


@pytest.mark.parametrize("media", ["application/xml", "text/xml", "application/rss+xml"])
def test_xml_is_returned_as_sent_and_never_rendered(media):
    managed = FakeTier("managed", XML_VIEWER)
    r = run(service(FakeFetcher(body=URLSET, headers=[("Content-Type", f"{media}; charset=utf-8")]), managed=managed))
    assert r.outcome == "captured" and managed.calls == 0
    assert r.document.representation == "response_body" and r.document.media_type == media
    assert r.document.body == URLSET.encode()
    assert r.evidence.attempts[-1].decision_reason == f"XML ({media}): returned as sent"


def test_xhtml_is_still_rendered_to_verify():
    managed = FakeTier("managed", ARTICLE)
    r = run(service(FakeFetcher(headers=[("Content-Type", "application/xhtml+xml")]), managed=managed))
    assert managed.calls == 1 and r.evidence.attempts[0].decision == "escalate"


@pytest.mark.parametrize("media", ["application/xml", "text/xml"])
def test_a_challenge_served_as_xml_is_still_classified(media):
    fetcher = FakeFetcher(status=403, body=CHALLENGE, headers=[("Content-Type", media), ("cf-mitigated", "challenge")])
    r = run(service(fetcher, managed=FakeTier("managed", XML_VIEWER)))
    assert r.outcome == "failed" and r.failure.code == "bot_challenge"


def test_a_block_page_served_as_xml_is_still_classified():
    fetcher = FakeFetcher(status=403, body=BLOCK_PAGE, headers=[("Content-Type", "text/xml"), ("Server", "cloudflare")])
    r = run(service(fetcher, managed=FakeTier("managed", XML_VIEWER)))
    assert r.outcome == "failed" and r.failure.code == "bot_blocked"


def test_an_error_page_keeps_its_failure_but_drops_an_unaccepted_body():
    fetcher = FakeFetcher(
        status=429, body='{"error": "slow down"}', headers=[("Content-Type", "application/json"), ("Retry-After", "60")]
    )
    r = run(service(fetcher), accept=HTML_ONLY)
    assert r.failure.code == "rate_limited" and r.failure.retry_after_seconds == 60 and r.document is None


def test_a_body_over_the_size_cap_is_rendered_not_returned_as_sent():
    class Truncating(FakeFetcher):
        async def fetch(self, url, timeout_s, exclusions=(), accept=None):
            response = await super().fetch(url, timeout_s)
            response.truncated = True
            return response

    managed = FakeTier("managed", ARTICLE)
    r = run(service(Truncating(), managed=managed))
    assert managed.calls == 1 and r.outcome == "captured" and r.document.representation == "rendered_html"
    assert r.evidence.attempts[0].decision == "escalate"
    r = run(service(Truncating()))
    assert r.failure.code == "browser_unavailable"


def test_exclusions_reach_the_browser_and_an_excluded_landing_page_fails():
    class Excluding(FakeTier):
        async def render(self, url, deadline_s, exclusions=()):
            self.exclusions = exclusions
            rendered = await super().render(url, deadline_s)
            rendered.html, rendered.excluded_url = "", "https://example.test/private"
            return rendered

    managed = Excluding("managed", ARTICLE)
    rule = Exclusion("example.test", "/private")
    r = run(service(FakeFetcher(body=APP_SHELL), managed=managed), exclusions=(rule,))
    assert managed.exclusions == (rule,)
    assert r.failure.code == "excluded" and r.document is None and r.final_url == "https://example.test/private"


def test_local_resolution_success_avoids_paid_fallback():
    local = FakeTier("local_resolution", ARTICLE)
    paid = FakeTier("challenge_resolution", ARTICLE, paid=True)
    r = run(service(FakeFetcher(body=CHALLENGE), local=local, challenge=paid), resolve_bot_challenges=True)
    assert r.outcome == "captured" and not r.evidence.cost.paid
    assert [a.tier for a in r.evidence.attempts] == ["direct", "local_resolution"]
    assert local.calls == 1 and paid.calls == 0
    assert r.evidence.cost.browser_seconds == 3.2


@pytest.mark.parametrize("allow_paid", [False, True])
def test_local_resolution_failure_escalates_only_when_paid_permitted(allow_paid):
    local = FakeTier("local_resolution", CHALLENGE)
    paid = FakeTier("challenge_resolution", ARTICLE, paid=True)
    r = run(service(FakeFetcher(body=CHALLENGE), local=local, challenge=paid), resolve_bot_challenges=allow_paid)
    assert local.calls == paid.calls == int(allow_paid)
    assert r.evidence.cost.paid == allow_paid
    assert r.outcome == ("captured" if allow_paid else "failed")
    if not allow_paid:
        assert not r.failure.resolution_attempted
    else:
        assert [a.tier for a in r.evidence.attempts] == ["direct", "local_resolution", "challenge_resolution"]
        assert r.evidence.attempts[1].decision == "escalate"
        assert r.evidence.cost.browser_seconds == 6.4


def test_managed_challenge_tries_local_before_paid():
    local = FakeTier("local_resolution", ARTICLE)
    paid = FakeTier("challenge_resolution", ARTICLE, paid=True)
    r = run(
        service(FakeFetcher(body=APP_SHELL), managed=FakeTier("managed", CHALLENGE), local=local, challenge=paid),
        resolve_bot_challenges=True,
    )
    assert r.outcome == "captured" and paid.calls == 0
    assert [a.tier for a in r.evidence.attempts] == ["direct", "managed", "local_resolution"]


def test_local_resolution_failure_cannot_loop():
    local = FakeTier("local_resolution", CHALLENGE)
    paid = FakeTier("challenge_resolution", CHALLENGE, paid=True)
    r = run(service(FakeFetcher(body=CHALLENGE), local=local, challenge=paid), resolve_bot_challenges=True)
    assert r.failure.code == "bot_challenge" and r.failure.resolution_attempted
    assert local.calls == paid.calls == 1


def test_local_timeout_leaves_budget_for_paid_fallback():
    class SlowLocal(FakeTier):
        async def render(self, url, deadline_s, exclusions=()):
            self.calls += 1
            assert deadline_s <= 0.01
            await asyncio.sleep(1)

    local = SlowLocal("local_resolution", CHALLENGE)
    paid = FakeTier("challenge_resolution", ARTICLE, paid=True)
    svc = service(FakeFetcher(body=CHALLENGE), local=local, challenge=paid)
    svc.settings.local_resolution_cap_s = 0.01
    r = run(svc, resolve_bot_challenges=True)
    assert r.outcome == "captured" and local.calls == paid.calls == 1
    assert r.evidence.attempts[1].duration_ms > 0
    assert r.evidence.attempts[1].decision == "escalate"


def test_local_excluded_result_never_escalates():
    class ExcludedLocal(FakeTier):
        async def render(self, *args, **kwargs):
            value = await super().render(*args, **kwargs)
            value.excluded_url = "https://blocked.test/"
            return value

    paid = FakeTier("challenge_resolution", ARTICLE, paid=True)
    r = run(
        service(FakeFetcher(body=CHALLENGE), local=ExcludedLocal("local_resolution", ARTICLE), challenge=paid),
        resolve_bot_challenges=True,
    )
    assert r.failure.code == "excluded" and paid.calls == 0
