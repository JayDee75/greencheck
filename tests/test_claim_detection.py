from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import clean_snippet, find_issues_on_page


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
