# HTTP Execution and Live Escalation

An automatic Stolosio session may satisfy a bounded journey through plain HTTP while
preserving the same downstream CDP endpoint and logical session identity.

The HTTP facade initially covers `page.goto`, `page.content`, declarative
`Emulation.setScriptExecutionDisabled` state for replay, and the exact Playwright
bootstrap commands required by those operations.

Every other non-bootstrap command is a browser requirement. HTTP transport failure,
non-success status, invalid HTML headers, excessive response size, or failed content
sanity is also a browser requirement. Stolosio asks the planner
for Browserless or Browserbase, acquires one browser, replays safe HTTP state, verifies
document readiness and execution-context catch-up, switches execution, and only then
releases the HTTP source. Replay does not wait for every page subresource to finish.
Execution-context mappings are tracked across the replayed session rather than being
owned by the command that happened to receive each context event. Navigation waits
for its response and matching default context; later commands wait only for the
specific execution context they reference.
An immediate browser command error terminates that candidate without consuming the
whole replay timeout. A failed acquisition leaves the HTTP source intact while Stolosio
tries the next candidate.

After that escalation, Stolosio forwards CDP opaquely and never switches browser
providers. The provider's CDP response is authoritative. Commands with unsafe side
effects are not replayed during escalation, and exhausting the acquisition
plan returns an explicit protocol error.

Explicit `stolosio.provider.slug=http` forces HTTP but does not grant unsupported
behavior. HTTP identifies itself truthfully as Stolosio automation and never forwards
sensitive downstream headers implicitly.

Separately, background promotion probes compare HTTP with Browserless. Content
sanity rejects empty JavaScript application shells and bot challenges, while relative
completeness rejects HTTP when Browserless consistently produces materially more
primary content. Browserbase is never probed automatically and contributes no
promotion evidence; operators may run an explicit paid diagnostic probe.

See [Domain Provider Eligibility](ANALYTICS.md).
