# No-Browser Execution

Some browser work is cheap enough that Harbor should not acquire a browser for it.
Harbor hides this optimization behind the same downstream abstraction used for real
browser sessions.

## Initial behavior

Harbor maintains in PostgreSQL:

- A log of seen domains.
- A log of seen Playwright commands.
- Per-domain counters for Playwright commands.
- Provider qualification and cost projections.

Unknown and unqualified domains begin on the operator-selected conservative provider.
HTTP becomes an automatic starting provider only after a strict background
`page.goto()` plus `page.content()` comparison qualifies it for that domain. Explicit
`harbor.provider.slug=http` continues to force the HTTP path.

For the initial implementation, only these logical client operations can remain on the
plain HTTP path:

- `page.goto`
- `page.content`
- Configuring `java_script_enabled` through
  `Emulation.setScriptExecutionDisabled`; Harbor records the setting for replay but
  does not treat it as browser work.

Every other non-bootstrap command triggers the browser journey. Harbor must:

1. Acquire the configured fallback browser session.
2. Replay every command already acknowledged by the HTTP facade in its original order,
   including every navigation and content read.
3. Wait for the real browser to catch up through the required response and lifecycle
   boundaries while suppressing duplicate replay output.
4. Execute the command that triggered browser acquisition.
5. Use the real browser for subsequent commands on that page.

If a client only calls `page.goto` followed by `page.content`, Harbor may complete
the work without involving a browser.

HTTP requests identify themselves truthfully as Harbor automation. The default
`User-Agent` includes the Harbor project URL rather than impersonating a browser, and
operators can configure `HTTP_USER_AGENT`, `HTTP_ACCEPT`, and
`HTTP_ACCEPT_LANGUAGE` for their deployment. Sensitive downstream session headers are
not forwarded implicitly.

Harbor receives CDP messages rather than Playwright API method names. Playwright also
sends protocol bootstrap commands before navigation, and content retrieval shares CDP
method names with title access and arbitrary evaluation. Harbor must therefore
recognize complete, acceptance-tested protocol sequences rather than classify a
session from method names alone. Unknown sequences take the safe path and acquire a
real browser.

The first implementation plan and the observed Playwright protocol evidence are in
[No-Browser Promotion](roadmap/no-browser-promotion.md).

## Future development

Harbor may gradually support more commands on the no-browser path. Each command should
be added and validated individually.

Potential future commands include:

- Static selectors.
- Text extraction.
- Attribute access.
- Link extraction.
- Page title access.
- Response status and header access.
- Redirect information.

These commands are future targets only. Until implemented, all of them trigger the
browser journey.
