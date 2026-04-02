from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import _extract_main_article_text, clean_snippet, find_issues_on_page


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


def test_no_low_severity_findings_are_emitted():
    findings = find_issues_on_page(
        "https://example.com/impact",
        "We are sustainable and eco-friendly in our services.",
    )
    assert findings
    assert all(f.severity in {"high", "medium"} for f in findings)


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
