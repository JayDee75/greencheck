from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import find_issues_on_page


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
