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


def test_aws_waf_bot_challenge_is_not_misclassified_as_app_shell() -> None:
    result = inspect_content(
        """
        <html><head>
          <script>
            window.awsWafCookieDomainList = ['imdb.com'];
            window.gokuProps = {};
          </script>
          <script src="https://example.token.awswaf.com/challenge.js"></script>
        </head><body>
          <div id="challenge-container"></div>
          <script>AwsWafIntegration.getToken()</script>
          <noscript>
            In order to continue, we need to verify that you're not a robot.
            This requires JavaScript.
          </noscript>
        </body></html>
        """
    )

    assert result.state == "unhealthy"
    assert result.reason_codes == ("bot_challenge",)
    assert result.facts["bot_challenge"] is True


def test_cloudflare_bot_challenge_is_detected_by_bounded_marker() -> None:
    result = inspect_content(
        """
        <html><head><title>Just a moment...</title></head><body>
          <div id="cf-chl-widget"></div>
          <script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>
        </body></html>
        """
    )

    assert result.state == "unhealthy"
    assert result.reason_codes == ("bot_challenge",)


def test_javascript_requirement_without_challenge_marker_remains_app_shell() -> None:
    result = inspect_content(
        """
        <html><body>
          <div id="root"></div>
          <script src="/assets/app.js"></script>
          <noscript>You need to enable JavaScript to run this app.</noscript>
        </body></html>
        """
    )

    assert result.state == "unhealthy"
    assert result.reason_codes == ("javascript_app_shell",)
    assert result.facts["bot_challenge"] is False


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
    assert result.facts["primary_region_present"] is True
    assert result.facts["primary_visible_text_chars"] > 40
    assert result.facts["primary_link_count"] == 1


def test_low_information_page_remains_inconclusive() -> None:
    result = inspect_content("<html><body><p>OK</p></body></html>")

    assert result.state == "inconclusive"
    assert result.reason_codes == ("low_information_content",)
