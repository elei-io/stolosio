# Adaptive HTTP Execution

An automatic Harbor session may satisfy a bounded journey through plain HTTP while
preserving the same downstream CDP endpoint and logical session identity.

The HTTP facade initially covers `page.goto`, `page.content`, declarative
`Emulation.setScriptExecutionDisabled` state for replay, and the exact Playwright
bootstrap commands required by those operations.

Every other non-bootstrap command is a new runtime requirement. Harbor suppresses HTTP
for that domain and asks the eligibility planner for the full ordered set of remaining
compatible candidates. For each
candidate it acquires the destination, replays safe commands, verifies lifecycle
catch-up, switches execution, and only then releases the source. A failed destination
leaves the source intact while Harbor tries the next candidate.

If another incompatible command appears, Harbor suppresses that provider and can
transition to the next eligible provider. Attempted providers are excluded. Commands
with unsafe side effects are not replayed across providers, and exhausting a safe plan
returns an explicit protocol error.

Explicit `harbor.provider.slug=http` forces HTTP but does not grant unsupported
behavior. HTTP identifies itself truthfully as Harbor automation and never forwards
sensitive downstream headers implicitly.

HTTP health comes from the same synchronized cohort as every other provider. Content
sanity rejects empty JavaScript application shells and bot challenges, while relative
completeness rejects HTTP when healthy full browsers consistently produce materially
more primary content. Runtime compatibility comes from exact command shapes in real
sessions, not method names or probe results.

See [Domain Provider Eligibility](ANALYTICS.md) and
[Provider Transitions](roadmap/provider-transitions.md).
