# Analytics

Analytics is a future Harbor capability. It will use historical session observations to
build evidence-backed conclusions that improve future session routing.

This capability is intentionally separate from the debug stream. The debug stream is a
filtered but faithful record of what happened. Analytics interprets observations across
many sessions and turns them into domain-level knowledge.

Potential conclusions include:

- A domain can normally run over plain HTTP unless future evidence contradicts that
  conclusion.
- A domain or route regularly requires a real browser.
- A domain appears to have lost confidence in the current IP and should use a proxy.
- A provider has historically performed better for a domain than another provider.
- A previously reliable domain strategy is no longer working.

Harbor may use these conclusions when choosing between HTTP and browser providers,
deciding whether to use a proxy, or selecting a browser with different stealth
characteristics.

Conclusions must remain revisable as new evidence arrives. Historical success is a
useful routing signal, not a permanent fact about a domain.

No analytics schema, confidence model, thresholds, or decision algorithm is defined
yet. Those details should be designed later from real observations collected by Harbor.
