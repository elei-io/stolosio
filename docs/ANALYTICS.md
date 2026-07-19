# Routing and Usage Analytics

Harbor keeps routing evidence separate from protocol transport. Native browser
providers relay opaque CDP; Harbor does not infer command compatibility from a
hand-maintained method list.

## Health evidence

A leased background worker checks the promotable HTTP and Browserless paths
independently. Probes record bounded facts:

- navigation completion;
- a healthy main-response status;
- HTML-compatible headers rather than a download;
- meaningful content without an obvious error page, JavaScript-required warning, or
  empty application shell.

Probes never retain HTML or inspect historical CDP methods. Probe sessions remain
visible in Activity but do not feed domain or cost projections. Health is
versioned by the health policy and provider contract.

Browserbase is not probed. When it is enabled and has non-zero administrative
capacity, it remains the terminal correctness fallback.

## Runtime plan

For a known domain, Harbor orders current healthy HTTP and Browserless candidates by
observed average attempt cost, falling back to configured cost. Enabled Browserbase
capacity is appended as the terminal fallback and participates in cost ordering when
another eligible candidate exists.

The configured `default_provider` is only the bootstrap route when the domain has no
current health evidence. Explicit `harbor.provider.slug` selection remains an
override, but it cannot bypass provider admission limits.

An automatic session may start on HTTP and escalate once to a browser. Harbor replays
the bounded acknowledged bootstrap history, then forwards subsequent CDP traffic
directly without retaining another replay log or attempting browser-to-browser
transitions.

## Browser and command time

Acquisition attempts retain:

- capacity occupancy;
- connected browser time;
- provider session timestamps where available;
- Browserbase's estimated one-minute-minimum billable time;
- provider-specific chargeable time;
- the captured cost rate and cost basis; and
- modeled cost units.

Browserless chargeable time is its capacity-slot occupancy. Browserbase chargeable
time is estimated billable time, including its session minimum. HTTP uses execution
time. Modeled cost is the captured rate multiplied by that chargeable time; it is not
a provider invoice amount.

Harbor does not retain one row or one success event per CDP command. It accumulates a
bounded per-attempt method summary in memory and stores it transactionally while the
provider attempt is finalized. PostgreSQL folds that transient summary into
`provider_command_cost_stats`, one cumulative row per provider and bounded method,
then clears the attempt copy. The compact DEBUG summary is the retained historical
copy.

The projection records counts, failures, interruptions, end-to-end time, provider
latency, Harbor queue time, attributed browser time, and attributed cost. Because commands can
overlap, raw command durations are not summed as browser cost. Instead, Harbor
distributes at most the attempt's measured browser-connected time in proportion to
provider latency and records idle/connect/disconnect time as `__session_overhead__`.
The aggregate therefore answers which commands account for the most browser time
without retaining individual commands or arbitrary parameters.

Method identity is globally capped at 512 rows per provider, including an `__other__`
overflow row; session overhead is stored separately. The recorder folds each attempt
summary into PostgreSQL with one batch upsert rather than one write per command or
method.

The recorder commits compact event history before updating analytical projections.
Domain/session facts and attempt/cost facts use separate, short, replay-safe
transactions, with attempt rows locked in stable order and bounded batches. A
projection failure can therefore be retried from JetStream without duplicating
aggregates, and request cleanup never participates in a domain-to-attempt lock cycle.

## Storage and administration

PostgreSQL owns:

- `domain_provider_health` and `health_probes`;
- `domain_provider_cost_stats`;
- bounded `provider_command_cost_stats`;
- attempt-level browser and billable time;
- routing configuration and factual escalation statistics.

Operators can read the aggregate directly through:

```text
GET /v1/admin/command-costs
```

The endpoint supports `provider`, `include_overhead`, `sort_by`, and a bounded
`limit`. `sort_by` accepts cost, browser time, or command count. The Cost workspace
presents filterable finalized usage from:

```text
GET /v1/admin/costs/overview?window=7d
```

The supported windows are 24 hours, 7 days, 30 days, and 90 days. CDP action
attribution is intentionally labeled all-time because the bounded
`provider_command_cost_stats` projection is cumulative rather than time-bucketed.

The compact operational history uses one event class in each store rather than
different retention tiers. Generic successful commands, intermediate
queue/acquisition states, and DOM-ready/load notifications are not retained. DEBUG
contains normalized facts only; health and route plans are policy conclusions
presented outside DEBUG.

```text
POST /v1/admin/domains/{domain_id}/probes
{"providers": ["http", "browserless"]}
```

Omit `providers` to queue every promotable provider.
