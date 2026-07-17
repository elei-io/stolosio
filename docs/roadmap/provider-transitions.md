# Provider Transitions

Status: implemented for the bounded HTTP facade and replay-safe transitions.

Harbor keeps one downstream WebSocket and logical session while moving execution from
one provider to another. A change occurs when the current provider cannot serve a new
CDP requirement or fails safely before side effects.

The runtime replans against the domain support matrix, includes acknowledged and
pending commands as capability requirements, excludes attempted providers, and tries
the full ordered plan. The configured default remains the final compatible automatic
candidate. Replay preserves command order and maps
browser-context, target, session, frame, loader, execution-context, object, and window
identifiers.

A provider transition is bounded by command, byte, and time budgets. Replay eligibility
belongs to the provider materialization strategy rather than a routing inference.
Harbor acquires, replays, and verifies the destination before switching and releasing
the source. Plan exhaustion and replay ambiguity are explicit protocol errors.
`execution.transitioned` is a factual DEBUG event, not a recommendation or a durable
rule that a domain always needs a particular provider.

Explicit provider selections never participate in automatic transitions. In
particular, explicit HTTP remains HTTP and rejects unsupported commands.
