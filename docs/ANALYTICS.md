# Domain Provider Support

Harbor maintains an independent support matrix for every observed domain and enabled
provider. The matrix answers one question: **does Harbor currently have enough evidence
that this provider can execute this domain's observed journey?** It does not compare a
provider with another provider, and cost never changes a support conclusion.

## Support checks

After an eligible successful navigation, a leased background worker checks every
enabled provider, including providers more expensive than the source session. Each
check records only absolute, bounded results:

- Navigation completed.
- The main response had a healthy HTTP status.
- Response headers describe usable HTML rather than a download or non-HTML payload.
- The provider capability manifest covers CDP methods observed for the domain.
- Content looks meaningful and does not resemble an empty JavaScript app shell,
  explicit JavaScript-required page, or obvious error page.

Content is never required to match another acquisition. Dynamic text is expected.
Harbor stores bounded counts and reason codes, not HTML or a content fingerprint.
Ambiguous low-information pages remain `checking`; hard failures become `unsupported`.
After the configured number of healthy confirmations, the provider becomes `supported`.

Support evidence is valid only while its support-policy version and provider capability
manifest version match the current configuration.

## Runtime plan

For a known domain, automatic routing takes current, enabled `supported` providers and
orders them by observed average cost, then configured cost when no observation exists.
If a new CDP requirement appears or acquisition fails before unsafe side effects,
Harbor replans, excludes attempted providers, and transitions to the next supported
compatible provider.

The configured `default_provider` is the final automatic candidate. Harbor uses it
when no provider is known to be supported and appends it after known-supported
providers when it is compatible with the required commands. It is not a correctness
baseline: its result remains ordinary runtime evidence, and failure still returns an
explicit protocol error.

Explicit `harbor.provider.slug` selection remains an override and does not rewrite
support evidence.

## Storage and administration

PostgreSQL owns `domain_provider_support`, `support_probes`, routing configuration,
cost projections, leases, and factual `domain_provider_transition_stats`. DEBUG continues to
contain normalized session facts. Support and route plans are policy conclusions stored
and presented separately.
