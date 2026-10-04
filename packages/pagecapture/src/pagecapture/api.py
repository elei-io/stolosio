"""The capture contract (docs/api.md, schema v2): request and result types, and their JSON form.

These types are the public interface. A service (e.g. stolosio's POST /v1/capture) takes a CaptureRequest and
returns CaptureResult.to_json(); everything else in the package is implementation.
"""

import base64
import hashlib
import posixpath
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

SCHEMA_VERSION = "2"
MAX_EXCLUSIONS = 1000

Outcome = Literal["captured", "failed"]
Representation = Literal["response_body", "rendered_html"]
FailureCategory = Literal["website", "network", "gateway", "content"]
Path = Literal["http", "browser"]
DecisionCode = Literal[
    "cache_url", "cache_pattern", "canary", "verify_http", "assessment",
    "content_comparison", "acquisition", "media_type",
]
Tier = Literal["direct", "managed", "local_resolution", "challenge_resolution"]

_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_MEDIA_TYPE = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*")


def _normalized_path(value: str) -> str:
    """Decoded path segments, so /admin never matches /administrator and /a/../admin matches /admin."""
    return "/" + posixpath.normpath(unquote(value).replace("\\", "/")).lstrip("/")


@dataclass(frozen=True)
class Exclusion:
    """A URL the caller must never fetch: `host` is exact, `*.example.com` (the domain and its subdomains) or `*`;
    `path_prefix` matches whole path segments."""

    host: str
    path_prefix: str = "/"

    @classmethod
    def from_json(cls, data: Any) -> "Exclusion":
        if not isinstance(data, dict) or set(data) - {"host", "path_prefix"} or not isinstance(data.get("host"), str):
            raise ValueError('an exclusion is {"host": str, "path_prefix": str}')
        host = data["host"].strip().lower().rstrip(".")
        if host != "*":
            wildcard = host.startswith("*.")
            name = host[2:] if wildcard else host
            try:
                name = name.encode("idna").decode("ascii")
            except UnicodeError as e:
                raise ValueError(f"invalid exclusion host: {data['host']!r}") from e
            if len(name) > 253 or not all(_LABEL.fullmatch(label) for label in name.split(".")):
                raise ValueError(f"invalid exclusion host: {data['host']!r}")
            host = ("*." if wildcard else "") + name
        path = data.get("path_prefix", "/")
        if not isinstance(path, str) or not path.startswith("/") or "?" in path or "#" in path or len(path) > 2048:
            raise ValueError(f"exclusion path_prefix must be an absolute path without query or fragment: {path!r}")
        return cls(host, _normalized_path(path))

    def matches(self, url: str) -> bool:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        if (
            self.host != "*"
            and host != self.host
            and not (self.host.startswith("*.") and (host == self.host[2:] or host.endswith(self.host[1:])))
        ):
            return False
        path = _normalized_path(parts.path)
        return self.path_prefix == "/" or path == self.path_prefix or path.startswith(self.path_prefix + "/")


def excluded(url: str, exclusions: "tuple[Exclusion, ...] | list[Exclusion]") -> bool:
    return any(e.matches(url) for e in exclusions)


def accepts(accept: tuple[str, ...] | None, media_type: str) -> bool:
    """Does the caller store this media type? None accepts any."""
    return accept is None or media_type in accept


@dataclass
class CaptureRequest:
    url: str
    resolve_bot_challenges: bool = False  # permits local resolution and paid fallback
    exclusions: tuple[Exclusion, ...] = ()  # never fetched: not on any redirect hop, not by the browser
    accept: tuple[str, ...] | None = None  # media types the caller stores; anything else fails without a body
    reference: str | None = None  # caller's trace id, echoed back
    deadline_ms: int | None = None  # upper bound for the whole capture (default: the service's own caps)

    @classmethod
    def from_json(cls, data: Any) -> "CaptureRequest":
        """Validate a request body. Raises ValueError (a 400) for anything invalid, including an excluded URL."""
        if not isinstance(data, dict):
            raise ValueError("the request body must be a JSON object")
        unknown = set(data) - {"url", "resolve_bot_challenges", "exclusions", "accept", "reference", "deadline_ms"}
        if unknown:
            raise ValueError(f"unknown request fields: {sorted(unknown)}")
        url = data.get("url")
        if not isinstance(url, str) or urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).hostname:
            raise ValueError("url must be an absolute http(s) URL")
        resolve = data.get("resolve_bot_challenges", False)
        if not isinstance(resolve, bool):
            raise ValueError("resolve_bot_challenges must be a boolean")
        raw = data.get("exclusions", [])
        if not isinstance(raw, list) or len(raw) > MAX_EXCLUSIONS:
            raise ValueError(f"exclusions must be a list of at most {MAX_EXCLUSIONS} entries")
        exclusions = tuple(dict.fromkeys(Exclusion.from_json(e) for e in raw))
        if excluded(url, exclusions):
            raise ValueError("url is excluded by the request's exclusions")
        accept = data.get("accept")
        if accept is not None:
            if not isinstance(accept, list) or not accept or not all(isinstance(a, str) for a in accept):
                raise ValueError("accept must be a non-empty list of media types")
            accept = tuple(dict.fromkeys(a.strip().lower() for a in accept))
            if not all(_MEDIA_TYPE.fullmatch(a) for a in accept):
                raise ValueError("accept entries must be media types such as text/html")
        reference = data.get("reference")
        if reference is not None and (not isinstance(reference, str) or len(reference) > 256):
            raise ValueError("reference must be a string of at most 256 characters")
        deadline_ms = data.get("deadline_ms")
        if deadline_ms is not None and (
            isinstance(deadline_ms, bool) or not isinstance(deadline_ms, int) or deadline_ms <= 0
        ):
            raise ValueError("deadline_ms must be a positive integer")
        return cls(
            url=url,
            resolve_bot_challenges=resolve,
            exclusions=exclusions,
            accept=accept,
            reference=reference,
            deadline_ms=deadline_ms,
        )


@dataclass
class Redirect:
    status: int
    url: str
    location: str


@dataclass
class Response:
    status_code: int
    headers: list[tuple[str, str]]  # in received order, duplicates kept
    redirects: list[Redirect] = field(default_factory=list)


@dataclass
class Document:
    representation: Representation  # response_body: exact HTTP bytes; rendered_html: the browser's final DOM
    media_type: str
    charset: str | None
    body: bytes

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    @property
    def content_bytes(self) -> int:
        return len(self.body)


@dataclass
class Failure:
    code: str  # see FAILURES in failures.py / docs/api.md
    category: FailureCategory  # only "website" is evidence about a site's health
    transient: bool
    message: str
    retry_after_seconds: float | None = None
    resolution_attempted: bool | None = None  # for bot_challenge / bot_blocked: was challenge resolution tried?


@dataclass
class Reason:
    code: str  # a reason column of docs/labels.md
    confidence: float
    source: str  # "rule"


@dataclass
class Assessment:
    primary: str | None  # the first true reason, None when the content is usable
    reasons: list[Reason] = field(default_factory=list)
    completeness: Literal["complete", "partial", "empty"] | None = None
    confidence: float | None = None


@dataclass
class Attempt:
    path: Path
    tier: Tier
    status_code: int | None
    duration_ms: float
    assessment: Assessment
    decision: Literal["accept", "escalate", "fail"]
    decision_reason: str
    steps: list[dict] = field(default_factory=list)  # browser attempts: what each wait / scroll added
    notes: list[str] = field(default_factory=list)
    reason_code: DecisionCode = "acquisition"  # stable machine reason; decision_reason is explanatory prose
    comparison: dict | None = None  # browser attempts: {"http_coverage": 0.97, "http_sufficient": true}


@dataclass
class Cost:
    browser_seconds: float = 0.0
    paid: bool = False  # a costlier tier (challenge resolution) was used
    bytes: int = 0


@dataclass
class Evidence:
    attempts: list[Attempt] = field(default_factory=list)
    cost: Cost = field(default_factory=Cost)
    versions: dict[str, str] = field(default_factory=dict)


@dataclass
class CaptureResult:
    outcome: Outcome
    requested_url: str
    final_url: str
    started_at: str
    finished_at: str
    reference: str | None = None
    response: Response | None = None  # the response the document came from (or the last one seen)
    document: Document | None = None  # on captured; on failed when there is evidence (a 404 page, a challenge page)
    failure: Failure | None = None
    evidence: Evidence = field(default_factory=Evidence)

    def to_json(self, include_body: bool = True) -> dict:
        """The response body of the capture endpoint."""
        out = {
            "schema": SCHEMA_VERSION,
            "reference": self.reference,
            "outcome": self.outcome,
            "requested_url": self.requested_url,
            "final_url": self.final_url,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "response": None,
            "document": None,
            "failure": asdict(self.failure) if self.failure else None,
            "evidence": asdict(self.evidence),
        }
        if self.response:
            out["response"] = {
                "status_code": self.response.status_code,
                "headers": [list(h) for h in self.response.headers],
                "redirects": [asdict(r) for r in self.response.redirects],
            }
        if self.document:
            d = self.document
            out["document"] = {
                "representation": d.representation,
                "media_type": d.media_type,
                "charset": d.charset,
                "content_sha256": d.content_sha256,
                "content_bytes": d.content_bytes,
            }
            if include_body:
                out["document"]["body_base64"] = base64.b64encode(d.body).decode()
        return out
