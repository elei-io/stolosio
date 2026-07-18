# Provider Transitions

Status: implemented for the bounded HTTP facade and replay-safe transitions.

Harbor keeps one downstream WebSocket and logical session while moving execution
between providers. When the current provider cannot execute an exact command shape,
Harbor durably suppresses that domain/provider pair before acquiring a replacement.

The runtime replans against current health and runtime eligibility, includes
acknowledged and pending commands as exact capability requirements, excludes attempted
providers, and tries the full remaining plan. Replay preserves command order and maps
browser-context, target, session, frame, loader, execution-context, object, and window
identifiers.

Harbor acquires, replays, and verifies the destination before switching and releasing
the source. One active source and one identified replacement may overlap; ordinary
attempts may not. Plan exhaustion and replay ambiguity are explicit protocol errors.

`execution.transitioned` remains a factual DEBUG event. Durable runtime suppression is
a separate policy state. Explicit provider selections never transition or update that
state.
