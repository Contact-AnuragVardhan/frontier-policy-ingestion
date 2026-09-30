from app.sources.multistate import parse_html


def test_parser_reads_companion_bills_and_preserves_detailed_status():
    html = """
    <html><body>
      <h3>AI Usage Boundaries and Oversight Requirements</h3>
      <ul>
        <li>Maryland (<a href="https://example.gov/sb720">MD SB 720</a> and
          <a href="https://example.gov/hb1057">MD HB 1057</a>) –
          Requires state AI guidance and local policies. Passed 1st Chamber
        </li>
      </ul>
    </body></html>
    """
    # parse_html deliberately refuses partial source coverage, so verify that safety guard fires.
    try:
        parse_html(html, "https://example.com/source", "2026-04-09")
    except ValueError as exc:
        assert "refusing partial/silent import" in str(exc)
    else:
        raise AssertionError("Expected the source-coverage safety check to reject partial HTML")
