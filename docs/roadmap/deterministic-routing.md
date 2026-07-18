# Domain Eligibility Routing

Status: implemented.

Harbor probes absolute navigation, HTTP, and header health for every provider.
Content health combines absolute rejection with bounded relative completeness inside
a synchronized provider cohort. An upper reference envelope from at least two healthy
full-browser participants can reject a provider that returns materially less primary
content across multiple dimensions.
Runtime command compatibility comes only from exact command shapes observed in
automatic sessions.

An incompatible live command suppresses that domain/provider pair immediately and
causes a replay-safe transition. One later compatible session restores it without a
probe; the escalating session and older concurrent sessions cannot restore it.

Automatic planning orders enabled providers that are both healthy and runtime-eligible
by cost. The configured default bootstraps domains with no current health evidence; it
is not appended around known evidence and is not a behavioral baseline.

Acceptance coverage includes the three-session suppress/restore/reuse sequence,
concurrent-session ordering, synchronized health cohorts, JavaScript app-shell
rejection, relative primary-content completeness, cost ordering, and explicit plan
exhaustion.
