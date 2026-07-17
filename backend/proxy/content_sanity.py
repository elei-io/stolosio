from dataclasses import dataclass
from html.parser import HTMLParser

_MOUNT_IDS = {"app", "root", "__next", "__nuxt", "svelte", "main-app"}
_MEANINGFUL_TAGS = {
    "a",
    "article",
    "button",
    "form",
    "img",
    "input",
    "li",
    "main",
    "table",
}
_IGNORED_TEXT_TAGS = {"head", "script", "style", "svg", "template"}
_JS_REQUIRED_PHRASES = (
    "enable javascript",
    "javascript is required",
    "you need to enable javascript",
    "please turn javascript on",
)
_ERROR_PHRASES = (
    "access denied",
    "application error",
    "browser not supported",
    "internal server error",
    "proxy error",
    "service unavailable",
)


@dataclass(frozen=True, slots=True)
class ContentSanity:
    state: str
    reason_codes: tuple[str, ...]
    facts: dict[str, int | bool]


class _DocumentFacts(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.visible_text: list[str] = []
        self.noscript_text: list[str] = []
        self.script_count = 0
        self.meaningful_elements = 0
        self.mount_depths: list[tuple[int, int, int]] = []
        self.empty_mounts = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        self.stack.append(tag)
        if tag == "script":
            self.script_count += 1
        if tag in _MEANINGFUL_TAGS:
            self.meaningful_elements += 1
        attributes = {key.lower(): value for key, value in attrs}
        identity = (attributes.get("id") or "").lower()
        if identity in _MOUNT_IDS:
            self.mount_depths.append(
                (len(self.stack), len(self.visible_text), self.meaningful_elements)
            )

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.mount_depths and self.mount_depths[-1][0] == len(self.stack):
            _, text_before, elements_before = self.mount_depths.pop()
            if (
                len(self.visible_text) == text_before
                and self.meaningful_elements == elements_before
            ):
                self.empty_mounts += 1
        if tag in self.stack:
            while self.stack:
                if self.stack.pop() == tag:
                    break

    def handle_data(self, data: str) -> None:
        normalized = " ".join(data.split())
        if not normalized:
            return
        if "noscript" in self.stack:
            self.noscript_text.append(normalized)
        if not any(tag in _IGNORED_TEXT_TAGS or tag == "noscript" for tag in self.stack):
            self.visible_text.append(normalized)

def inspect_content(html: str) -> ContentSanity:
    parser = _DocumentFacts()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return ContentSanity(
            "inconclusive",
            ("malformed_html",),
            {"bytes": len(html.encode(errors="ignore"))},
        )

    visible = " ".join(parser.visible_text)
    visible_lower = visible.lower()
    noscript_lower = " ".join(parser.noscript_text).lower()
    visible_chars = len(visible)
    js_required = any(
        phrase in visible_lower or phrase in noscript_lower
        for phrase in _JS_REQUIRED_PHRASES
    )
    error_page = any(phrase in visible_lower for phrase in _ERROR_PHRASES)
    shell = (
        parser.script_count > 0
        and visible_chars < 80
        and (
            parser.empty_mounts > 0
            or parser.meaningful_elements == 0
            or js_required
        )
    )
    facts: dict[str, int | bool] = {
        "bytes": len(html.encode(errors="ignore")),
        "visible_text_chars": visible_chars,
        "meaningful_elements": parser.meaningful_elements,
        "script_count": parser.script_count,
        "empty_mount_count": parser.empty_mounts,
        "javascript_required": js_required,
    }
    if error_page:
        return ContentSanity("unhealthy", ("error_page",), facts)
    if shell:
        return ContentSanity("unhealthy", ("javascript_app_shell",), facts)
    if visible_chars >= 80 or parser.meaningful_elements >= 2:
        return ContentSanity("healthy", (), facts)
    return ContentSanity("inconclusive", ("low_information_content",), facts)
