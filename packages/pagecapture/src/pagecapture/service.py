"""Capture acquisition and validation, with caller-gated challenge resolution.

Plain HTTP and managed rendering precede local resolution; both local and paid
resolution require resolve_bot_challenges. Each stage is assessed independently.
The local resolver retries with a native browser identity and a bounded wait.
Exclusions, content validation and the capture deadline apply to every local attempt.
"""

import asyncio
import datetime as dt
import random
import re
import time
from importlib.metadata import PackageNotFoundError, version

from . import failures
from .adapters import (
    BqlBrowserTier,
    BrowserTier,
    CdpBrowserTier,
    ChallengeNotPassed,
    ExcludedUrl,
    Fetcher,
    FetchError,
    HostNotFound,
    HttpResponse,
    HttpxFetcher,
    RedirectLoop,
    UnsupportedMediaType,
    media_type,
)
from .api import Assessment, Attempt, CaptureRequest, CaptureResult, Document, Evidence, Reason, Response, accepts
from .cache import MethodCache, MethodPolicy, default_cache
from .classify import Classifier, rules
from .compare import coverage, http_windows
from .config import Settings
from .document import Document as ParsedPage
from .document import is_xml
from .fetch import Fetched, make_response
from .labels import RENDER_NEED, Verdict
from .render import RENDERER_VERSION, BrowserCapacity, Rendered

ESCALATE_TO_BROWSER = set(RENDER_NEED) | {"interstitial"}  # a browser gets past consent walls and redirect stubs
MIN_RENDERED_CHARS = 200


def now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sniffed_media_type(body: bytes) -> str:
    """For a response that declared no media type: HTML and XML are recognisable, anything else is opaque."""
    start = body[:512].lstrip().lower()
    if start.startswith((b"<!doctype html", b"<html")):
        return "text/html"
    if start.startswith(b"<?xml"):
        return "application/xml"
    return "application/octet-stream"


def assessment_of(verdict: Verdict) -> Assessment:
    reasons = [Reason(c, verdict.flags[c].confidence, verdict.flags[c].source) for c in verdict.true_reasons()]
    completeness = None
    if not verdict.rejected:
        completeness = "complete"
    elif verdict.reason == "partial":
        completeness = "partial"
    elif verdict.reason == "app_shell":
        completeness = "empty"
    return Assessment(primary=verdict.reason, reasons=reasons, completeness=completeness, confidence=verdict.confidence)


# Rendered-page reasons that mean the browser was refused (an error status), not that the page is gone or walled
BROWSER_REFUSED = {"client_error", "server_error", "rate_limited"}

# The browser's proxy failed, not the site: a gateway problem (proxy providers refuse some site categories)
PROXY_FAILURE = re.compile(r"ERR_(TUNNEL_CONNECTION_FAILED|PROXY_[A-Z_]+|SOCKS_[A-Z_]+)")


class CaptureService:
    """service = CaptureService()                      # defaults: requests + browser tiers from settings/env
    result = await service.capture(CaptureRequest(url="https://example.com/"))
    result.to_json()                                # the endpoint's response body
    """

    def __init__(
        self,
        settings: Settings | None = None,
        fetcher: Fetcher | None = None,
        managed: BrowserTier | None = None,
        challenge_resolution: BrowserTier | None = None,
        classifier: Classifier | None = None,
        cache: MethodCache | None = None,
        local_resolution: BrowserTier | None = None,
    ):
        self.settings = settings or Settings()
        s = self.settings
        self.fetcher = fetcher or HttpxFetcher(s)
        self.managed = managed or (CdpBrowserTier(s.browser_ws, "managed", False, s) if s.browser_ws else None)
        self.local_resolution = local_resolution or (
            CdpBrowserTier(s.browser_ws, "local_resolution", False, s) if s.browser_ws else None
        )
        self.challenge_resolution = challenge_resolution or _challenge_tier(s)
        self.classifier = classifier or Classifier(s)
        self.policy = MethodPolicy(cache if cache is not None else default_cache(s), s)

    async def close(self) -> None:
        for part in (self.fetcher, self.managed, self.local_resolution, self.challenge_resolution):
            if hasattr(part, "close"):
                await part.close()

    def versions(self) -> dict[str, str]:
        try:
            package = version("pagecapture")
        except PackageNotFoundError:
            package = "dev"
        return {"pagecapture": package, "classifier": "rules", "renderer": RENDERER_VERSION}

    async def capture(self, request: CaptureRequest) -> CaptureResult:
        result = await self._capture(request)
        document = result.document
        if document and document.representation == "response_body" and not accepts(request.accept, document.media_type):
            result.document = None  # evidence of a failure (an error page) in a type the caller doesn't store
        return result

    async def _capture(self, request: CaptureRequest) -> CaptureResult:
        s = self.settings
        t0 = time.monotonic()
        budget = min(s.capture_cap_s, request.deadline_ms / 1000) if request.deadline_ms else s.capture_cap_s

        def remaining() -> float:
            return budget - (time.monotonic() - t0)

        result = CaptureResult(
            outcome="failed",
            requested_url=request.url,
            final_url=request.url,
            started_at=now(),
            finished_at="",
            reference=request.reference,
            evidence=Evidence(versions=self.versions()),
        )

        try:
            async with asyncio.timeout(budget):
                return await self._acquire(request, result, t0, remaining)
        except TimeoutError:
            if result.evidence.attempts:
                last = result.evidence.attempts[-1]
                last.decision, last.decision_reason = "fail", "capture deadline expired"
                last.reason_code = "acquisition"
            result.failure = failures.failure("deadline_exceeded", "capture deadline expired")
            return self._finish(result)

    async def _acquire(self, request: CaptureRequest, result: CaptureResult, t0: float, remaining) -> CaptureResult:
        s = self.settings

        # 1. plain HTTP
        def elapsed_ms() -> float:
            return round((time.monotonic() - t0) * 1000, 1)

        try:
            http = await self.fetcher.fetch(
                request.url,
                timeout_s=min(s.http_timeout_s, remaining()),
                exclusions=request.exclusions,
                accept=request.accept,
            )
        except FetchError as e:
            result.evidence.attempts.append(
                Attempt("http", "direct", None, elapsed_ms(), Assessment(primary="unreachable"), "fail", str(e))
            )
            result.failure = failures.failure(
                "host_not_found" if isinstance(e, HostNotFound) else "unreachable", str(e)
            )
            return self._finish(result)
        except RedirectLoop as e:
            result.evidence.attempts.append(
                Attempt("http", "direct", None, elapsed_ms(), Assessment(primary="unreachable"), "fail", str(e))
            )
            result.failure = failures.failure("redirect_loop", str(e))
            return self._finish(result)
        except ExcludedUrl as e:
            result.evidence.attempts.append(
                Attempt("http", "direct", None, elapsed_ms(), Assessment(primary=None), "fail", str(e))
            )
            result.failure = failures.failure("excluded", str(e))
            return self._finish(result)
        except UnsupportedMediaType as e:
            return self._unsupported(result, e.response, e.media_type)
        if http.truncated:
            return await self._too_large(request, result, http, remaining)
        http_doc = self._http_document(http)
        # Keep already-acquired bytes as failure evidence if classification/cache work times out.
        result.document = http_doc
        result.final_url, result.response = http.final_url, Response(http.status_code, http.headers, http.redirects)
        if 200 <= http.status_code < 300 and not accepts(request.accept, http_doc.media_type):  # sniffed
            return self._unsupported(result, http, http_doc.media_type)
        result.evidence.cost.bytes += len(http.body)
        verdict = await asyncio.to_thread(self.classifier.classify, Fetched(request.url, http.as_requests(), None))
        attempt = Attempt(
            "http",
            "direct",
            http.status_code,
            round(http.elapsed_ms + verdict.latency_ms, 1),
            assessment_of(verdict),
            "accept",
            "",
            notes=list(verdict.notes),
            reason_code="assessment" if verdict.reason else "acquisition",
        )
        result.evidence.attempts.append(attempt)
        reason = verdict.reason

        if (reason is None or reason in ESCALATE_TO_BROWSER) and is_xml(http_doc.media_type):
            # A browser would replace XML with its XML viewer page: return the bytes as sent, never render them.
            attempt.reason_code = "media_type"
            attempt.decision_reason = f"XML ({http_doc.media_type}): returned as sent"
            return self._captured(result, http_doc)
        if reason is None:
            # Looks usable, but raw HTML can't show everything a browser would add: render by default, skip only
            # on evidence that plain HTTP is enough here (cache), except for a canary share that re-checks it.
            cached = await self.policy.http_sufficient(request.url, len(http.body))
            if cached and random.random() >= s.canary_rate:
                attempt.reason_code = cached.code
                attempt.decision_reason = cached.detail
                return self._captured(result, http_doc)
            if self.managed is None:
                attempt.decision = "fail"
                attempt.decision_reason = "HTML needs verification but no browser is configured"
                result.document = http_doc
                result.failure = failures.failure("browser_unavailable", attempt.decision_reason)
                return self._finish(result)
            attempt.decision = "escalate"
            attempt.reason_code = "canary" if cached else "verify_http"
            attempt.decision_reason = (
                "canary: re-checking a cached HTTP-sufficient page"
                if cached
                else "render to verify: no evidence yet that plain HTTP is enough here"
            )
            return await self._render(
                request,
                result,
                self.managed,
                http,
                http_doc,
                remaining,
                http_usable=True,
                http_page=getattr(verdict, "_document", None),
            )
        if reason == "payload_mismatch":
            if http.body.strip():
                attempt.reason_code = "media_type"
                attempt.decision_reason = f"not HTML ({http_doc.media_type}): returned as sent"
                return self._captured(result, http_doc)
            attempt.decision, attempt.decision_reason = "fail", "empty response body"
            result.document, result.failure = None, failures.failure("incomplete_content", "empty response body")
            return self._finish(result)
        if reason == "bot_challenge":
            block = rules.block_page(verdict._document) if getattr(verdict, "_document", None) else None
            return await self._challenge(request, result, attempt, http, http_doc, remaining, block)
        if reason in ESCALATE_TO_BROWSER:
            if self.managed is None:
                attempt.decision, attempt.decision_reason = (
                    "fail",
                    f"{reason}: a browser is needed but none is configured",
                )
                result.document, result.failure = (
                    http_doc,
                    failures.failure("browser_unavailable", "no browser tier configured"),
                )
                return self._finish(result)
            attempt.decision, attempt.decision_reason = (
                "escalate",
                f"{reason}: content missing without a browser"
                if reason in RENDER_NEED
                else "interstitial: a browser can get past it",
            )
            return await self._render(request, result, self.managed, http, http_doc, remaining)
        # blocked or broken: nothing a browser would fix
        attempt.decision, attempt.decision_reason = "fail", reason
        result.document = http_doc
        result.failure = failures.from_reason(
            reason, http.status_code, verdict.flags[reason].detail, http.retry_after_seconds()
        )
        return self._finish(result)

    def _unsupported(self, result: CaptureResult, http: HttpResponse, media: str) -> CaptureResult:
        """The caller doesn't store this media type: fail without a document (the body is never read, or dropped)."""
        result.final_url, result.response = http.final_url, Response(http.status_code, http.headers, http.redirects)
        result.evidence.attempts.append(
            Attempt(
                "http",
                "direct",
                http.status_code,
                round(http.elapsed_ms, 1),
                Assessment(primary=None),
                "fail",
                f"media type {media} not accepted",
                reason_code="media_type",
            )
        )
        result.failure = failures.failure("unsupported_media_type", f"media type {media} is not accepted")
        return self._finish(result)

    async def _too_large(self, request, result: CaptureResult, http: HttpResponse, remaining) -> CaptureResult:
        """A plain body over the size cap is never returned as the exact bytes: the page renders instead."""
        result.final_url, result.response = http.final_url, Response(http.status_code, http.headers, http.redirects)
        result.evidence.cost.bytes += len(http.body)
        cap = self.settings.http_max_response_bytes
        attempt = Attempt(
            "http",
            "direct",
            http.status_code,
            round(http.elapsed_ms, 1),
            Assessment(primary=None),
            "escalate",
            f"body larger than {cap} bytes: rendering instead",
        )
        result.evidence.attempts.append(attempt)
        if self.managed is None:
            attempt.decision = "fail"
            result.failure = failures.failure("browser_unavailable", f"body larger than {cap} bytes and no browser")
            return self._finish(result)
        return await self._render(request, result, self.managed, http, None, remaining)

    def _why_not_resolve(self, request, block: str | None) -> str | None:
        """Why challenge resolution won't be tried for this page, or None when it will."""
        if not request.resolve_bot_challenges:
            return "paid resolution not permitted"
        if self.challenge_resolution is None:
            return "no challenge-resolution tier configured"
        if block and not getattr(self.challenge_resolution, "proxied", False):
            return "the challenge tier has no proxies, and a block page refuses the identity, not the browser"
        return None

    def _next_resolution(self, request, result, block):
        """Caller-gated resolution tries local before paid; neither repeats."""
        if not request.resolve_bot_challenges:
            return None, "challenge resolution not permitted"
        used = {a.tier for a in result.evidence.attempts}
        if self.local_resolution is not None and "local_resolution" not in used:
            return self.local_resolution, None
        if "challenge_resolution" in used:
            return None, "resolution did not pass it"
        why = self._why_not_resolve(request, block)
        return (self.challenge_resolution, None) if why is None else (None, why)

    @staticmethod
    def _resolution_attempted(result):
        return any(a.tier in {"local_resolution", "challenge_resolution"} for a in result.evidence.attempts)

    @staticmethod
    def _http_block(url: str, http: HttpResponse) -> str | None:
        return rules.block_page(ParsedPage(url, http.as_requests()))

    @staticmethod
    def _bot_failure(block: str | None, reason: str, attempted: bool):
        return failures.failure("bot_blocked" if block else "bot_challenge", reason, resolution_attempted=attempted)

    async def _challenge(self, request, result, attempt, http, http_doc, remaining, block: str | None) -> CaptureResult:
        """Try local resolution before caller-gated paid fallback."""
        what = block or "bot challenge"
        tier, why_not = self._next_resolution(request, result, block)
        if tier is not None:
            attempt.decision, attempt.decision_reason = "escalate", f"{what}: trying {tier.tier}"
            return await self._render(request, result, tier, http, http_doc, remaining)
        attempt.decision, attempt.decision_reason = "fail", f"{what}: {why_not}"
        result.document = http_doc
        result.failure = self._bot_failure(block, attempt.decision_reason, self._resolution_attempted(result))
        return self._finish(result)

    def _unverified_http(self, result: CaptureResult, http_doc: Document, why: str) -> CaptureResult:
        """Preserve plain HTML as evidence, never promote classifier acceptance to verified content."""
        result.evidence.attempts[0].notes.append(f"not verified by rendering: {why}")
        result.document = http_doc
        result.failure = failures.failure("incomplete_content", f"HTML completeness could not be verified: {why}")
        return self._finish(result)

    async def _render(
        self,
        request,
        result,
        tier: BrowserTier,
        http: HttpResponse,
        http_doc: Document | None,
        remaining,
        http_usable: bool = False,
        http_page=None,
    ) -> CaptureResult:
        if remaining() <= 5:
            result.document, result.failure = http_doc, failures.failure("deadline_exceeded", "no time left to render")
            return self._finish(result)
        render_started = time.monotonic()
        try:
            render_budget = remaining() - 2
            if tier.tier == "local_resolution":
                render_budget = min(render_budget, self.settings.local_resolution_cap_s)
            async with asyncio.timeout(render_budget):
                rendered = await tier.render(request.url, deadline_s=render_budget, exclusions=request.exclusions)
        except ChallengeNotPassed as e:  # the tier tried; the protection held
            result.evidence.attempts.append(
                Attempt(
                    "browser",
                    tier.tier,
                    None,
                    0.0,
                    Assessment(primary="bot_challenge"),
                    "fail",
                    f"resolution did not pass it: {e}"[:300],
                )
            )
            self._record_failed_render(result, tier, render_started)
            fallback = await self._local_fallback(
                request, result, tier, http, http_doc, remaining, http_usable, http_page
            )
            if fallback is not None:
                return fallback
            result.document = http_doc
            result.failure = self._bot_failure(self._http_block(request.url, http), str(e)[:300], True)
            return self._finish(result)
        except Exception as e:  # the tier couldn't give us a browser
            busy = isinstance(e, BrowserCapacity)
            result.evidence.attempts.append(
                Attempt(
                    "browser",
                    tier.tier,
                    None,
                    0.0,
                    Assessment(primary=None),
                    "fail",
                    f"{'no browser capacity' if busy else 'browser unavailable'}: {e!r}"[:300],
                )
            )
            self._record_failed_render(result, tier, render_started, acquired=not busy)
            fallback = await self._local_fallback(
                request, result, tier, http, http_doc, remaining, http_usable, http_page
            )
            if fallback is not None:
                return fallback
            result.document = http_doc
            result.failure = failures.failure(
                "capacity" if busy else "deadline_exceeded" if isinstance(e, TimeoutError) else "browser_unavailable",
                repr(e)[:300],
                getattr(e, "retry_after_seconds", None) if busy else None,
            )
            return self._finish(result)
        cost = result.evidence.cost
        cost.browser_seconds += rendered.seconds
        cost.bytes += rendered.bytes
        cost.paid = cost.paid or getattr(tier, "paid", False)
        if rendered.excluded_url:
            result.evidence.attempts.append(
                Attempt(
                    "browser",
                    tier.tier,
                    rendered.status,
                    round(rendered.seconds * 1000, 1),
                    Assessment(primary=None),
                    "fail",
                    rendered.error or "excluded",
                    steps=rendered.steps,
                )
            )
            result.final_url, result.document = rendered.excluded_url, None
            result.failure = failures.failure("excluded", f"the page led to an excluded URL: {rendered.excluded_url}")
            return self._finish(result)
        assessment, notes, page = self._assess_rendered(rendered)
        attempt = Attempt(
            "browser",
            tier.tier,
            rendered.status,
            round(rendered.seconds * 1000, 1),
            assessment,
            "accept",
            "",
            steps=rendered.steps,
            notes=notes,
            reason_code="assessment" if assessment.primary else "acquisition",
        )
        result.evidence.attempts.append(attempt)
        rendered_doc = (
            Document("rendered_html", "text/html", "utf-8", rendered.html.encode()) if rendered.html else None
        )

        if not rendered.html or (rendered.error and not rendered.lines):
            rendered.error = rendered.error or "no page content could be read"
            attempt.decision, attempt.decision_reason = "fail", f"render failed: {rendered.error}"
            fallback = await self._local_fallback(
                request, result, tier, http, http_doc, remaining, http_usable, http_page
            )
            if fallback is not None:
                return fallback
            if (
                tier.tier == "challenge_resolution"
                and rendered.error.startswith("navigation failed")
                and not PROXY_FAILURE.search(rendered.error)
            ):
                # plain HTTP met bot protection and the site refused the browser's connection outright (Akamai
                # answers this way): a block, not a gateway problem
                result.document = http_doc
                result.failure = self._bot_failure(
                    self._http_block(request.url, http) or "connection refused",
                    f"the site refused the browser's connection ({rendered.error})",
                    True,
                )
                return self._finish(result)
            code = "deadline_exceeded" if "Timeout" in rendered.error else "browser_unavailable"
            message = rendered.error + (
                " (the tier's proxy couldn't reach the site)" if PROXY_FAILURE.search(rendered.error) else ""
            )
            result.document, result.failure = http_doc, failures.failure(code, message)
            return self._finish(result)
        primary = assessment.primary
        if primary == "bot_challenge":
            block = rules.block_page(page)
            what = (block or "bot challenge") + " in the browser"
            next_tier, why_not = self._next_resolution(request, result, block)
            if next_tier is not None:
                attempt.decision, attempt.decision_reason = "escalate", f"{what}: trying {next_tier.tier}"
                return await self._render(
                    request,
                    result,
                    next_tier,
                    http,
                    http_doc,
                    remaining,
                    http_usable=http_usable,
                    http_page=http_page,
                )
            attempt.decision = "fail"
            attempt.decision_reason = f"{what}: {why_not}"
            # never downgrade: the plain response is better evidence than a challenge page
            result.document = http_doc
            result.failure = self._bot_failure(block, attempt.decision_reason, self._resolution_attempted(result))
            return self._finish(result)
        if primary in BROWSER_REFUSED and http_usable:
            # The browser was refused. Plain HTML may still lack JS-loaded content despite passing classification.
            attempt.decision, attempt.decision_reason = (
                "fail",
                f"the browser got {primary} ({rendered.status}); plain HTML completeness is unverified",
            )
            return self._unverified_http(result, http_doc, f"the browser got HTTP {rendered.status}")
        if primary is not None:  # the rendered page itself is a 404, login wall, block page, ...
            attempt.decision, attempt.decision_reason = "fail", f"rendered page: {primary}"
            result.document = rendered_doc
            result.failure = failures.from_reason(primary, rendered.status, primary)
            return self._finish(result)
        if assessment.completeness == "empty":
            attempt.decision, attempt.decision_reason = "fail", "still no content after rendering"
            # Retain plain text as failure evidence; surrounding text cannot prove JS-loaded content is present.
            plain = http_page or ParsedPage(request.url, http.as_requests())
            if http_usable and plain.words >= 5:
                return self._unverified_http(result, http_doc, "the rendered page came out empty")
            rendered_chars = (rendered.final_state or {}).get("chars", 0)
            if http_usable and rendered_doc is not None and rendered_chars >= 20 and not assessment.primary:
                attempt.decision, attempt.decision_reason = (
                    "accept",
                    "a small page: little content, plain response empty",
                )
                attempt.notes.append(f"little content ({rendered_chars} characters)")
                return self._captured(result, rendered_doc)
            result.document, result.failure = (
                rendered_doc,
                failures.failure("incomplete_content", attempt.decision_reason),
            )
            return self._finish(result)

        # A trustworthy render: compare it with the plain response and remember whether plain HTTP was enough.
        if http_page is None:
            http_page = ParsedPage(request.url, http.as_requests())
        # reuse the text classification already extracted instead of parsing the page again
        share = coverage(http_windows(http_page), rendered.lines) if http.body else 0.0
        sufficient = http_usable and share >= self.settings.sufficient_coverage
        await self.policy.record(request.url, sufficient, len(http.body))
        attempt.reason_code = "content_comparison"
        attempt.comparison = {"http_coverage": round(share, 3), "http_sufficient": sufficient}
        if sufficient:  # verified: the exact bytes the server sent hold the content
            attempt.decision_reason = (
                f"rendered content confirms the plain response ({share:.0%} of it was already there)"
            )
            return self._captured(result, http_doc)
        attempt.decision_reason = f"rendered content is complete (plain response had {share:.0%} of it)"
        result.final_url = rendered.final_url or result.final_url
        if rendered.status:
            result.response = Response(
                rendered.status, http.headers if rendered.status == http.status_code else [], http.redirects
            )
        return self._captured(result, rendered_doc)

    @staticmethod
    def _record_failed_render(result, tier, started, acquired=True):
        seconds = time.monotonic() - started
        result.evidence.attempts[-1].duration_ms = round(seconds * 1000, 1)
        if acquired:
            result.evidence.cost.browser_seconds += seconds
            result.evidence.cost.paid |= getattr(tier, "paid", False)

    async def _local_fallback(self, request, result, tier, http, http_doc, remaining, http_usable, http_page):
        if tier.tier != "local_resolution":
            return None
        next_tier, _ = self._next_resolution(request, result, self._http_block(request.url, http))
        if next_tier is None:
            return None
        attempt = result.evidence.attempts[-1]
        attempt.decision = "escalate"
        attempt.decision_reason += f"; trying {next_tier.tier}"
        return await self._render(
            request,
            result,
            next_tier,
            http,
            http_doc,
            remaining,
            http_usable=http_usable,
            http_page=http_page,
        )

    def _assess_rendered(self, rendered: Rendered) -> tuple[Assessment, list[str], ParsedPage]:
        """Rules (not the model) on the rendered DOM, plus the renderer's own signals of an unfinished page."""
        page = ParsedPage(
            rendered.url,
            make_response(
                rendered.status or 200,
                rendered.final_url or rendered.url,
                {"Content-Type": "text/html; charset=utf-8"},
                rendered.html.encode(),
            ),
        )
        flags, _ = rules.evaluate(page)
        reasons = [Reason(c, f.confidence, "rule") for c, f in flags.items() if f.value and c != "payload_mismatch"]
        primary = next((r.code for r in reasons), None)
        state, notes = rendered.final_state or {}, []
        empty = state.get("chars", len(" ".join(rendered.lines))) < MIN_RENDERED_CHARS or state.get("mount", False)
        if page.stats.main_words == 0 and page.stats.content_words < 5 and page.words >= 20:
            empty = True
            notes.append("declared main content is empty; only navigation remains")
        if rendered.early_dom:
            notes.append("the page stopped answering: the HTML is the DOM from early in the render")
        if rendered.transfer_capped:
            notes.append("stopped loading after the transfer cap: later resources were not fetched")
        if rendered.virtualized:
            notes.append("page drops content while scrolling: final HTML holds less than was shown")
        if state.get("pending"):
            notes.append(f"{state['pending']} loading placeholder(s) still visible at the end")
        if rendered.error:
            notes.append(f"render stopped early: {rendered.error[:120]}")
        completeness = "empty" if empty else "complete"
        return (
            Assessment(
                primary=primary,
                reasons=reasons,
                completeness=completeness,
                confidence=0.9 if not state.get("pending") else 0.75,
            ),
            notes,
            page,
        )

    @staticmethod
    def _http_document(http: HttpResponse) -> Document:
        mt, charset = media_type(http.headers)
        return Document("response_body", mt or sniffed_media_type(http.body), charset, http.body)

    def _captured(self, result: CaptureResult, document: Document) -> CaptureResult:
        result.outcome, result.document, result.failure = "captured", document, None
        return self._finish(result)

    @staticmethod
    def _finish(result: CaptureResult) -> CaptureResult:
        result.finished_at = now()
        return result


def _challenge_tier(s: Settings) -> BrowserTier | None:
    """A BrowserQL URL (https://…/bql) unblocks server-side then renders over CDP; a ws(s):// URL is plain CDP."""
    if not s.challenge_browser_ws:
        return None
    tier = BqlBrowserTier if s.challenge_browser_ws.startswith("http") else CdpBrowserTier
    return tier(s.challenge_browser_ws, "challenge_resolution", True, s, proxied=s.challenge_browser_proxied)


def capture(url: str, resolve_bot_challenges: bool = False, settings: Settings | None = None) -> CaptureResult:
    """Synchronous convenience wrapper for one URL."""

    async def run():
        service = CaptureService(settings)
        try:
            return await service.capture(CaptureRequest(url=url, resolve_bot_challenges=resolve_bot_challenges))
        finally:
            await service.close()

    return asyncio.run(run())
