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
