# No-Browser Execution

Some browser work is cheap enough that Harbor should not acquire a browser for it.
Harbor hides this optimization behind the same downstream abstraction used for real
browser sessions.

## Initial behavior

Harbor maintains in PostgreSQL:

- A log of seen domains.
- A log of seen Playwright commands.
- Per-domain counters for Playwright commands.

When Harbor encounters an unseen domain, or a domain with no historical Playwright
command usage, it begins with a plain HTTP request instead of acquiring a browser and
calling `page.goto`.

For the initial implementation, only these commands can remain on the plain HTTP path:

- `page.goto`
- `page.get_content`

Every other command triggers the browser journey. Harbor must:

1. Acquire a real browser session.
2. Perform a real `page.goto` for the URL previously fetched over HTTP.
3. Execute the command that triggered browser acquisition.
4. Use the real browser for subsequent commands on that page.

If a client only calls `page.goto` followed by `page.get_content`, Harbor may complete
the work without involving a browser.

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
