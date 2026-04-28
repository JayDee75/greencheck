import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import (
    RENDER_WARNING,
    RULEBOOK,
    _candidate_blocks,
    _detect_chromium_executable,
    _extract_main_article_text,
    clean_snippet,
    find_issues_on_page,
    normalize_extracted_text,
    scan_site,
)


def test_color_context_without_claim_is_ignored():
    findings = find_issues_on_page(
        "https://example.com/catalog",
        "This t-shirt comes in green color and blue color options.",
    )
    assert findings == []


def test_absolute_claim_not_duplicated_as_generic_claim():
    findings = find_issues_on_page(
        "https://example.com/impact",
        "Our product is climate neutral across all operations.",
    )
    categories = [f.category for f in findings]
    assert "CARBON_NEUTRALITY_CLAIMS" in categories
    assert "GENERIC_ENVIRONMENTAL_CLAIMS" not in categories


def test_generic_claims_are_low_priority():
    findings = find_issues_on_page(
        "https://example.com/impact",
        "We are sustainable and eco-friendly in our services.",
    )
    assert findings
    assert any(f.severity == "low" for f in findings if f.category == "GENERIC_ENVIRONMENTAL_CLAIMS")


def test_image_filename_or_media_path_is_ignored_for_generic_claims():
    findings = find_issues_on_page(
        "https://example.com/gallery",
        "/sites/default/files/2023-01/close-up-crystal-globe-resting-green-grass-forest_1920x1280_JPG.jpg",
    )
    assert findings == []


def test_ecovadis_explanatory_context_is_not_generic_company_claim():
    findings = find_issues_on_page(
        "https://example.com/sustainability",
        (
            "EcoVadis is the globally recognised authority in corporate sustainability ratings, evaluating "
            "companies across four critical dimensions: Environment, Labour & Human Rights, Ethics, and "
            "Sustainable Procurement."
        ),
    )
    assert findings == []


def test_html_snippet_is_cleaned_to_plain_text_without_broken_entities():
    snippet = clean_snippet(
        '<li><strong>Corporate milestones</strong>: stay informed about our sustainable growth, ESG initiatives, '
        'and our continued journey as a European leader, helping 105,000 customers with their HR, pay &amp; time.</li>'
    )
    assert snippet == (
        'Corporate milestones: stay informed about our sustainable growth, ESG initiatives, and our continued journey '
        'as a European leader, helping 105,000 customers with their HR, pay & time.'
    )
    assert '<li>' not in snippet
    assert '<strong>' not in snippet
    assert '&amp' not in snippet


def test_press_page_navigation_context_is_not_flagged_as_generic_claim():
    findings = find_issues_on_page(
        'https://www.sdworx.com/en-en/about-sd-worx/press',
        'stay informed about our sustainable growth, ESG initiatives, and our continued journey as a European leader, helping 105,000 customers with their HR, pay & time.',
    )
    assert all(f.category != 'GENERIC_ENVIRONMENTAL_CLAIMS' for f in findings)


def test_generic_claim_keeps_full_sentence_block_without_truncation():
    text = (
        "At Cegeka, we’re not just writing code. We’re writing a better future. "
        "By embedding sustainable components into our solutions, we’re proving that digital innovation and ESG can go hand in hand."
    )
    findings = find_issues_on_page("https://example.com/esg", text)
    generic = [f for f in findings if f.category == "GENERIC_ENVIRONMENTAL_CLAIMS"]
    assert generic
    assert generic[0].evidence == text


def test_generic_claim_recommendation_mentions_scheme_certification_or_label():
    findings = find_issues_on_page(
        "https://example.com/services",
        "We deliver sustainable components in all our solutions.",
    )
    generic = [f for f in findings if f.category == "GENERIC_ENVIRONMENTAL_CLAIMS"]
    assert generic
    assert "scheme, certification, or label" in generic[0].how_to_fix


def test_tier2_broad_sustainability_claim_triggers_generic_detection():
    findings = find_issues_on_page(
        "https://example.com/esg",
        "We are writing a better future by embedding sustainable components in our solutions.",
    )
    generic = [f for f in findings if f.category == "GENERIC_ENVIRONMENTAL_CLAIMS"]
    assert generic
    assert "better future" in generic[0].evidence.lower()
    assert "sustainable components" in generic[0].evidence.lower()


def test_tier2_claim_with_connected_sentences_is_extracted_as_full_block():
    text = (
        "At Cegeka, we’re not just writing code.\n"
        "We’re writing a better future.\n"
        "By embedding sustainable components into our solutions, we’re proving that digital innovation and ESG can go hand in hand."
    )
    findings = find_issues_on_page("https://example.com/esg", text)
    generic = [f for f in findings if f.category == "GENERIC_ENVIRONMENTAL_CLAIMS"]
    assert generic
    assert generic[0].evidence == (
        "At Cegeka, we’re not just writing code. We’re writing a better future. "
        "By embedding sustainable components into our solutions, we’re proving that digital innovation and ESG can go hand in hand."
    )


def test_tier2_wording_without_classic_environment_term_still_detected():
    findings = find_issues_on_page(
        "https://example.com/innovation",
        "Responsible innovation creates a better future for our digital services.",
    )
    assert any(f.category == "GENERIC_ENVIRONMENTAL_CLAIMS" for f in findings)


def test_tier5_false_positive_without_tier1_or_tier2_does_not_trigger_generic_detection():
    findings = find_issues_on_page(
        "https://example.com/investors",
        "Our strategy focuses on financial sustainability and long-term profitability.",
    )
    assert all(f.category != "GENERIC_ENVIRONMENTAL_CLAIMS" for f in findings)


def test_scan_switches_to_playwright_when_static_returns_non_200(monkeypatch):
    static_html = "<html><body>Short teaser.</body></html>"
    rendered = "Reduce greenhouse gas emissions by at least 55% by 2030."

    monkeypatch.setattr("app.main.fetch_html", lambda *_args, **_kwargs: (static_html, 206))
    monkeypatch.setattr("app.main.extract_rendered_text_with_playwright", lambda *_args, **_kwargs: rendered)

    pages, findings, debug = scan_site("https://example.com/esg")
    assert pages == 1
    assert debug["extraction_mode"] == "STATIC+PLAYWRIGHT"
    assert debug["rendered_fallback_used"] is True
    assert any(f.category in {"FUTURE_TARGET", "FUTURE_NET_ZERO_TARGETS"} for f in findings)


def test_scan_uses_playwright_when_static_lacks_strong_future_target_signals(monkeypatch):
    static_html = "<html><body>" + ("Our ESG environment progress and sustainability roadmap. " * 60) + "</body></html>"
    rendered = "Reduce greenhouse gas emissions by at least 55% by 2030."

    monkeypatch.setattr("app.main.fetch_html", lambda *_args, **_kwargs: (static_html, 200))
    monkeypatch.setattr("app.main.extract_rendered_text_with_playwright", lambda *_args, **_kwargs: rendered)

    pages, findings, debug = scan_site("https://example.com/esg")
    assert pages == 1
    assert debug["playwright_used"] is True
    assert debug["playwright_error"] is None
    assert debug["extraction_mode"] == "STATIC+PLAYWRIGHT"
    assert "missing_strong_future_target_signal" in debug["rendered_fallback_reason"]
    assert debug["contains_target_claim"] is True
    assert debug["extracted_text_length"] > len(rendered)
    assert any(f.category == "FUTURE_NET_ZERO_TARGETS" for f in findings)


def test_scan_returns_warning_if_static_and_playwright_fail(monkeypatch):
    monkeypatch.setattr("app.main.fetch_html", lambda *_args, **_kwargs: (None, 403))
    monkeypatch.setattr("app.main.extract_rendered_text_with_playwright", lambda *_args, **_kwargs: "")

    pages, findings, debug = scan_site("https://example.com/blocked")
    assert pages == 1
    assert findings == []
    assert RENDER_WARNING in debug["warnings"]


def test_scan_debug_avoids_sync_playwright_loop_error(monkeypatch):
    monkeypatch.setattr("app.main.fetch_html", lambda *_args, **_kwargs: (None, 403))

    async def fake_playwright(*_args, **_kwargs):
        return {
            "text": "Reduce greenhouse gas emissions by at least 55% by 2030.",
            "playwright_used": True,
            "playwright_error": None,
            "chromium_path": "/usr/bin/chromium",
        }

    monkeypatch.setattr("app.main.extract_rendered_text_with_playwright", fake_playwright)

    _, findings, debug = scan_site("https://example.com/esg")

    assert debug["playwright_used"] is True
    assert debug["extraction_mode"] == "PLAYWRIGHT"
    assert "Sync API inside the asyncio loop" not in (debug.get("playwright_error") or "")
    assert any(f.category in {"FUTURE_TARGET", "FUTURE_NET_ZERO_TARGETS"} for f in findings)


def test_main_article_extraction_prioritizes_intro_and_excludes_related_teasers():
    html = """
    <html><body>
      <nav>Navigation content</nav>
      <article class="blog-post">
        <h1>Building a more responsible digital future</h1>
        <p>At Cegeka, we’re not just writing code. We’re writing a better future. By embedding sustainable components into our solutions, we’re proving that digital innovation and ESG can go hand in hand.</p>
        <p>Additional body content.</p>
      </article>
      <section class="related-articles">
        <h2>Related Articles</h2>
        <p>Accelerating to Zero Emission Cloud Services by 2030</p>
        <p>Cegeka has set a bold target: by 2030, clients will have access to Cegeka’s zero emission cloud services.</p>
      </section>
      <footer>Footer content</footer>
    </body></html>
    """
    extracted, debug = _extract_main_article_text(html)
    assert "sustainable components" in extracted.lower()
    assert "related articles" not in extracted.lower()
    assert "zero emission cloud services" not in extracted.lower()
    assert debug["intro_captured"] is True
    assert debug["related_articles_excluded"] is True
    assert debug["found_better_future"] is True


def test_related_articles_heading_inside_main_container_is_excluded():
    html = """
    <html><body>
      <main class="content">
        <h1>Building a more responsible digital future</h1>
        <p>At Cegeka, we’re not just writing code. We’re writing a better future. By embedding sustainable components into our solutions, we’re proving that digital innovation and ESG can go hand in hand.</p>
        <section>
          <h2>Related Articles</h2>
          <p>Accelerating to Zero Emission Cloud Services by 2030</p>
        </section>
      </main>
    </body></html>
    """
    extracted, _ = _extract_main_article_text(html)
    assert "sustainable components" in extracted.lower()
    assert "accelerating to zero emission cloud services by 2030" not in extracted.lower()


def test_main_container_is_not_excluded_by_nested_related_heading():
    html = """
    <html><body>
      <main class="article-main">
        <h1>Building a more responsible digital future</h1>
        <p>At Cegeka, we’re not just writing code. We’re writing a better future. By embedding sustainable components into our solutions, we’re proving that digital innovation and ESG can go hand in hand.</p>
        <div class="sidebar-slot">
          <section>
            <h3>Related Articles</h3>
            <p>Accelerating to Zero Emission Cloud Services by 2030</p>
          </section>
        </div>
      </main>
    </body></html>
    """
    extracted, debug = _extract_main_article_text(html)
    assert "building a more responsible digital future" in extracted.lower()
    assert "sustainable components" in extracted.lower()
    assert "zero emission cloud services" not in extracted.lower()
    assert debug["intro_captured"] is True
    assert debug["related_articles_excluded"] is True


def test_recommended_reading_section_is_excluded_from_primary_extraction():
    html = """
    <html><body>
      <article>
        <h1>Building a more responsible digital future</h1>
        <p>At Cegeka, we’re not just writing code. We’re writing a better future. By embedding sustainable components into our solutions, we’re proving that digital innovation and ESG can go hand in hand.</p>
        <section class="recommendation-widget">
          <h2>Recommended Reading</h2>
          <p>Accelerating to Zero Emission Cloud Services by 2030</p>
        </section>
      </article>
    </body></html>
    """
    extracted, _ = _extract_main_article_text(html)
    assert "sustainable components" in extracted.lower()
    assert "recommended reading" not in extracted.lower()
    assert "zero emission cloud services" not in extracted.lower()


def test_intro_block_claim_is_evaluated_as_generic_environmental_candidate():
    text = (
        "Building a more responsible digital future\n"
        "At Cegeka, we’re not just writing code. We’re writing a better future. By embedding sustainable components into our solutions, we’re proving that digital innovation and ESG can go hand in hand."
    )
    findings = find_issues_on_page("https://example.com/blog", text)
    generic = [f for f in findings if f.category == "GENERIC_ENVIRONMENTAL_CLAIMS"]
    assert generic
    assert "sustainable components" in generic[0].evidence.lower()
    assert "better future" in generic[0].evidence.lower()


def test_hero_block_with_two_signal_groups_is_forced_into_generic_candidate_pipeline():
    hero_block = (
        "Building a more responsible digital future. "
        "At Cegeka, we’re not just writing code. We’re writing a better future. "
        "By embedding sustainable components into our solutions, we’re proving that digital innovation and ESG can go hand in hand."
    )
    findings = find_issues_on_page(
        "https://example.com/esg",
        "Unrelated footer teaser.",
        hero_block=hero_block,
        extraction_debug={
            "main_title": "Building a more responsible digital future",
            "first_paragraph_after_title": (
                "At Cegeka, we’re not just writing code. We’re writing a better future. "
                "By embedding sustainable components into our solutions, we’re proving that digital innovation and ESG can go hand in hand."
            ),
        },
    )
    generic = [f for f in findings if f.category == "GENERIC_ENVIRONMENTAL_CLAIMS"]
    assert generic
    assert "responsible digital future" in generic[0].evidence.lower()
    assert "sustainable components" in generic[0].evidence.lower()


def test_future_target_regression_fixture_returns_exactly_one_issue():
    fixture_text = (
        "Our ESG Ambitions Environment Reduce greenhouse gas emissions by at least 55% by 2030, "
        "aligning with the EU's Green Deal and international environmental regulations."
    )
    findings = find_issues_on_page("https://example.com/esg", fixture_text)
    future_findings = [f for f in findings if f.category == "FUTURE_NET_ZERO_TARGETS"]
    assert len(future_findings) == 1
    assert len(findings) == 1
    assert "55% by 2030" in future_findings[0].evidence


def test_rule_level_future_target_unit_case_returns_amber_equivalent():
    text = (
        "Reduce greenhouse gas emissions by at least 55% by 2030, aligning with the EU's Green Deal "
        "and international environmental regulations."
    )
    findings = find_issues_on_page("https://example.com/environment", text)
    assert len(findings) == 1
    assert findings[0].category == "FUTURE_NET_ZERO_TARGETS"
    assert findings[0].severity == "medium"


def test_integration_mocked_extracted_text_contains_expected_future_target_details():
    extracted_text = (
        "Our ESG Ambitions Environment Reduce greenhouse gas emissions by at least 55% by 2030, "
        "aligning with the EU's Green Deal and international environmental regulations."
    )
    findings = find_issues_on_page("https://example.com/integration", extracted_text)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.evidence == extracted_text
    assert finding.category == "FUTURE_NET_ZERO_TARGETS"
    assert finding.severity == "medium"
    assert finding.message == RULEBOOK["FUTURE_TARGET"]
    assert "baseline year and emissions scopes" in finding.how_to_fix


def test_duplicate_generic_claims_are_fuzzy_deduplicated():
    text = (
        "Our services are sustainable for customers. "
        "Our services are sustainable for customer."
    )
    findings = find_issues_on_page("https://example.com/sustainability", text)
    generic = [f for f in findings if f.category == "GENERIC_ENVIRONMENTAL_CLAIMS"]
    assert len(generic) == 1


def test_sustainable_in_hr_context_is_not_flagged():
    findings = find_issues_on_page(
        "https://example.com/hr",
        "We help people build a sustainable career and strengthen HR excellence.",
    )
    assert all(f.category != "GENERIC_ENVIRONMENTAL_CLAIMS" for f in findings)


def test_downloadable_report_context_suppresses_generic_claims():
    findings = find_issues_on_page(
        "https://example.com/esg",
        "Our sustainable strategy is detailed in our ESG report. Download report (sustainability-report.pdf).",
    )
    assert all(f.category != "GENERIC_ENVIRONMENTAL_CLAIMS" for f in findings)


def test_sdgs_reference_is_treated_as_third_party_context():
    findings = find_issues_on_page(
        "https://example.com/esg",
        "Our SDGs alignment supports our sustainability framework and reporting.",
    )
    assert all(f.category != "GENERIC_ENVIRONMENTAL_CLAIMS" for f in findings)


def test_forward_looking_target_without_plan_is_high_risk():
    findings = find_issues_on_page(
        "https://example.com/esg",
        "We will reduce emissions by 55% by 2030.",
    )
    forward = [f for f in findings if f.category == "FUTURE_NET_ZERO_TARGETS"]
    assert forward
    assert forward[0].severity == "high"


def test_forward_looking_target_with_nearby_plan_is_not_high_risk():
    findings = find_issues_on_page(
        "https://example.com/esg",
        "We will reduce emissions by 55% by 2030. Baseline year 2019 with Scope 1 and Scope 2 milestones in our roadmap.",
    )
    forward = [f for f in findings if f.category == "FUTURE_NET_ZERO_TARGETS"]
    assert forward
    assert forward[0].severity == "medium"


def test_forward_target_detection_is_not_hardcoded_to_specific_brand_or_site():
    findings = find_issues_on_page(
        "https://example.org/environment",
        "We aim to cut Scope 1 and 2 emissions by 40% before 2030.",
    )
    assert any(f.category == "FUTURE_NET_ZERO_TARGETS" for f in findings)


def test_universal_forward_target_detects_non_ghg_environmental_subjects():
    findings = find_issues_on_page(
        "https://example.com/packaging",
        "Plastic-free packaging by 2030. 100% renewable electricity by 2028. Reduce water use by 25% by 2026.",
    )
    forward = [f for f in findings if f.category == "FUTURE_NET_ZERO_TARGETS"]
    assert len(forward) >= 3


def test_forward_target_not_suppressed_by_generic_esg_report_reference():
    findings = find_issues_on_page(
        "https://example.com/sustainability",
        "Reduce greenhouse gas emissions by at least 55% by 2030. See our ESG report for more details.",
    )
    forward = [f for f in findings if f.category == "FUTURE_NET_ZERO_TARGETS"]
    assert forward
    assert forward[0].severity == "high"


def test_sdworx_style_55_percent_2030_claim_detected_once():
    findings = find_issues_on_page(
        "https://www.any-company.com/sustainability",
        "We will reduce greenhouse gas emissions by at least 55% by 2030.",
    )
    forward = [f for f in findings if f.category == "FUTURE_NET_ZERO_TARGETS"]
    assert len(forward) == 1


def test_future_target_required_case_full_sentence_detected():
    text = (
        "Our ESG Ambitions Environment Reduce greenhouse gas emissions by at least 55% by 2030, "
        "aligning with the EU's Green Deal and international environmental regulations."
    )
    findings = find_issues_on_page("https://example.com/esg", text)
    forward = [f for f in findings if f.category == "FUTURE_NET_ZERO_TARGETS"]
    assert len(forward) == 1


def test_future_target_required_case_split_blocks_detected():
    blocks = [
        "Our ESG Ambitions",
        "Environment",
        "Reduce greenhouse gas emissions by at least 55% by 2030, aligning with the EU's Green Deal and international environmental regulations.",
    ]
    findings = find_issues_on_page("https://example.com/esg", "\n".join(blocks))
    forward = [f for f in findings if f.category == "FUTURE_NET_ZERO_TARGETS"]
    assert len(forward) == 1


def test_future_target_required_case_simple_reduction_detected():
    findings = find_issues_on_page(
        "https://example.com/targets",
        "Reduce greenhouse gas emissions by at least 55% by 2030.",
    )
    assert len([f for f in findings if f.category == "FUTURE_NET_ZERO_TARGETS"]) == 1


def test_future_target_required_case_scope_target_detected():
    findings = find_issues_on_page(
        "https://example.com/targets",
        "We aim to cut Scope 1 and 2 emissions by 40% before 2030.",
    )
    assert len([f for f in findings if f.category == "FUTURE_NET_ZERO_TARGETS"]) == 1


def test_future_target_required_case_renewable_target_detected():
    findings = find_issues_on_page(
        "https://example.com/energy",
        "100% renewable electricity by 2028.",
    )
    assert len([f for f in findings if f.category == "FUTURE_NET_ZERO_TARGETS"]) == 1


def test_future_target_required_case_plastic_free_target_detected():
    findings = find_issues_on_page(
        "https://example.com/packaging",
        "Plastic-free packaging by 2030.",
    )
    assert len([f for f in findings if f.category == "FUTURE_NET_ZERO_TARGETS"]) == 1


def test_short_first_paragraph_is_used_as_hero_intro_fallback():
    html = """
    <html><body>
      <main>
        <h1>heroBlock</h1>
        <p>hero candidate</p>
      </main>
    </body></html>
    """
    _, debug = _extract_main_article_text(html)
    assert debug["first_paragraph_after_title"] == "hero candidate"
    assert debug["hero_block"] == "heroBlock hero candidate"


def test_short_hero_block_is_included_in_candidate_blocks():
    blocks = _candidate_blocks("", hero_block="heroBlock hero candidate")
    assert blocks == ["heroBlock hero candidate"]


def test_extraction_includes_card_column_text_from_div_span():
    html = """
    <html><body>
      <main>
        <section class="cards">
          <div class="card"><span>Our ESG Ambitions</span></div>
          <div class="card"><span>Environment</span></div>
          <div class="card"><span>Reduce greenhouse gas emissions by at least 55% by 2030.</span></div>
        </section>
      </main>
    </body></html>
    """
    extracted, _ = _extract_main_article_text(html)
    findings = find_issues_on_page("https://example.com/esg", extracted)
    assert any(f.category == "FUTURE_NET_ZERO_TARGETS" for f in findings)


def test_cegeka_hero_block_is_extracted_exactly_and_generates_candidate_debug():
    html = """
    <html><body>
      <main>
        <h1>Building a more responsible digital future</h1>
        <p>At Cegeka, we’re not just writing code. We’re writing a better future. By embedding sustainable components into our solutions, we’re proving that digital innovation and ESG can go hand in hand.</p>
        <p>Other body copy.</p>
      </main>
    </body></html>
    """
    extracted, debug = _extract_main_article_text(html)
    expected = (
        "Building a more responsible digital future At Cegeka, we’re not just writing code. "
        "We’re writing a better future. By embedding sustainable components into our solutions, "
        "we’re proving that digital innovation and ESG can go hand in hand."
    )
    assert debug["hero_block"] == expected

    findings = find_issues_on_page("https://example.com/esg", extracted, hero_block=debug["hero_block"], extraction_debug=debug)
    assert any(f.category == "GENERIC_ENVIRONMENTAL_CLAIMS" for f in findings)
    claim_debug = debug["claim_pipeline_debug"]
    assert claim_debug["hero_candidate_created"] is True
    assert claim_debug["hero_hard_fallback_triggered"] is True
    assert claim_debug["hero_block"] == expected


def test_normalize_extracted_text_collapses_whitespace_and_removes_duplicate_lines():
    raw = "  Our ESG Ambitions \n\nReduce greenhouse gas emissions   by 55% by 2030.\nReduce greenhouse gas emissions by 55% by 2030. "
    assert normalize_extracted_text(raw) == "Our ESG Ambitions Reduce greenhouse gas emissions by 55% by 2030."


def test_scan_site_triggers_rendered_fallback_when_static_text_is_short_and_keyword_empty(monkeypatch):
    monkeypatch.setattr(
        "app.main.fetch_html",
        lambda *_args, **_kwargs: ("<html><body><main><p>Hello</p></main></body></html>", 200),
    )
    monkeypatch.setattr(
        "app.main.extract_rendered_text_with_playwright",
        lambda *_args, **_kwargs: "Reduce greenhouse gas emissions by at least 55% by 2030.",
    )

    _, findings, extraction_debug = scan_site("https://example.com/esg")

    assert extraction_debug["extraction_mode"] == "STATIC+PLAYWRIGHT"
    assert "short_static_text" in extraction_debug["rendered_fallback_reason"]
    assert extraction_debug["greenhouse_gas_in_text"] is True
    assert len([f for f in findings if f.category == "FUTURE_NET_ZERO_TARGETS"]) == 1


def test_detect_chromium_executable_prefers_env_var(monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", "/custom/chromium")
    monkeypatch.setattr("app.main.shutil.which", lambda *_args, **_kwargs: None)
    assert _detect_chromium_executable() == "/custom/chromium"


def test_extract_rendered_text_uses_detected_system_chromium(monkeypatch):
    launch_calls = []

    class DummyPage:
        async def goto(self, *_args, **_kwargs):
            class Response:
                status = 200

            return Response()

        async def wait_for_load_state(self, *_args, **_kwargs):
            return None

        async def wait_for_selector(self, *_args, **_kwargs):
            return None

        async def wait_for_function(self, *_args, **_kwargs):
            return None

        async def wait_for_timeout(self, *_args, **_kwargs):
            return None

        async def evaluate(self, *_args, **_kwargs):
            return "Reduce greenhouse gas emissions by at least 55% by 2030."

    class DummyContext:
        async def new_page(self):
            return DummyPage()

        async def close(self):
            return None

    class DummyBrowser:
        async def new_context(self, **_kwargs):
            return DummyContext()

        async def close(self):
            return None

    class DummyChromium:
        async def launch(self, **kwargs):
            launch_calls.append(kwargs)
            return DummyBrowser()

    class DummyPlaywright:
        chromium = DummyChromium()

    class DummyManager:
        async def __aenter__(self):
            return DummyPlaywright()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: DummyManager())
    monkeypatch.setattr("app.main._detect_chromium_executable", lambda: "/usr/bin/chromium")
    from app.main import extract_rendered_text_with_playwright

    result = asyncio.run(extract_rendered_text_with_playwright("https://example.com"))

    assert result["text"]
    assert result["playwright_error"] is None
    assert launch_calls
    launch_kwargs = launch_calls[0]
    assert launch_kwargs["executable_path"] == "/usr/bin/chromium"
    assert "--no-sandbox" in launch_kwargs["args"]
    assert "--disable-dev-shm-usage" in launch_kwargs["args"]


def test_extract_rendered_text_falls_back_to_inner_text_after_goto_timeout(monkeypatch):
    class DummyPage:
        async def goto(self, *_args, **_kwargs):
            raise TimeoutError("goto timed out")

        async def wait_for_timeout(self, *_args, **_kwargs):
            return None

        async def evaluate(self, *_args, **_kwargs):
            return "Reduce greenhouse gas emissions by at least 55% by 2030."

    class DummyContext:
        async def new_page(self):
            return DummyPage()

        async def close(self):
            return None

    class DummyBrowser:
        async def new_context(self, **_kwargs):
            return DummyContext()

        async def close(self):
            return None

    class DummyChromium:
        async def launch(self, **_kwargs):
            return DummyBrowser()

    class DummyPlaywright:
        chromium = DummyChromium()

    class DummyManager:
        async def __aenter__(self):
            return DummyPlaywright()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: DummyManager())
    from app.main import extract_rendered_text_with_playwright

    result = asyncio.run(extract_rendered_text_with_playwright("https://example.com"))

    assert "55% by 2030" in result["text"]
    assert result["playwright_error"] is None
