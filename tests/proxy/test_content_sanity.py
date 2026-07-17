from backend.proxy.content_sanity import inspect_content


def test_javascript_app_shell_is_unhealthy_for_http_acquisition() -> None:
    result = inspect_content(
        """
        <!doctype html>
        <html><body>
          <div id="root"></div>
          <script src="/assets/app.js"></script>
        </body></html>
        """
    )

    assert result.state == "unhealthy"
    assert result.reason_codes == ("javascript_app_shell",)
    assert result.facts["empty_mount_count"] == 1


def test_dynamic_meaningful_content_is_healthy_without_fingerprinting() -> None:
    result = inspect_content(
        """
        <html><body><main>
          <article><h1>Listing 38491</h1>
          <p>This text may change on every request, but it is a meaningful document.</p>
          <a href="/next">Next listing</a>
          </article>
        </main></body></html>
        """
    )

    assert result.state == "healthy"
    assert result.reason_codes == ()


def test_low_information_page_remains_inconclusive() -> None:
    result = inspect_content("<html><body><p>OK</p></body></html>")

    assert result.state == "inconclusive"
    assert result.reason_codes == ("low_information_content",)
