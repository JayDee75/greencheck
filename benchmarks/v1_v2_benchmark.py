
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Set

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import find_issues_on_page


# Simulated legacy (v1) detector: intentionally broad and noisier heuristics.
V1_GENERIC = re.compile(r"\b(green|sustainable|eco[-\s]?friendly|environmentally\s+friendly)\b", re.I)
V1_ABSOLUTE = re.compile(r"\b(net\s*zero|carbon\s*neutral|climate\s*neutral)\b", re.I)
V1_TARGET = re.compile(r"\b(net\s*zero\s*by\s*20\d{2}|\d{1,3}%\s*(by|in)\s*20\d{2})\b", re.I)
V1_LABEL = re.compile(r"\b(label|badge|seal|certified)\b", re.I)


@dataclass
class Sample:
    id: str
    text: str
    expected: Set[str]


SAMPLES: List[Sample] = [
    Sample(
        id="tp_generic",
        text="Our company delivers a sustainable future through green solutions for everyone.",
        expected={"GENERIC_ENVIRONMENTAL_CLAIMS"},
    ),
    Sample(
        id="tp_carbon",
        text="This product is carbon neutral because we offset all emissions with credits.",
        expected={"CARBON_NEUTRALITY_CLAIMS"},
    ),
    Sample(
        id="tp_target",
        text="We will reduce GHG emissions by 55% by 2030.",
        expected={"FUTURE_NET_ZERO_TARGETS"},
    ),
    Sample(
        id="tp_label",
        text="Look for our own eco badge across all products.",
        expected={"SUSTAINABILITY_LABELS"},
    ),
    Sample(
        id="tn_color",
        text="This t-shirt is available in green and blue colors.",
        expected=set(),
    ),
    Sample(
        id="tn_certified_specific",
        text="Packaging is certified with the EU Ecolabel according to third-party criteria.",
        expected=set(),
    ),
    Sample(
        id="tn_data_specific",
        text="Scope 1 and 2 emissions fell 18% versus 2022 baseline, assured by an external auditor.",
        expected=set(),
    ),
    Sample(
        id="mixed",
        text="Our sustainable products are climate neutral and net zero by 2040, backed by offsets.",
        expected={"GENERIC_ENVIRONMENTAL_CLAIMS", "CARBON_NEUTRALITY_CLAIMS", "FUTURE_NET_ZERO_TARGETS"},
    ),
]


def detect_v1(text: str) -> Set[str]:
    found: Set[str] = set()
    if V1_GENERIC.search(text):
        found.add("GENERIC_ENVIRONMENTAL_CLAIMS")
    if V1_ABSOLUTE.search(text):
        found.add("CARBON_NEUTRALITY_CLAIMS")
    if V1_TARGET.search(text):
        found.add("FUTURE_NET_ZERO_TARGETS")
    if V1_LABEL.search(text) and re.search(r"\b(our|own|internal|self)\b", text, re.I):
        found.add("SUSTAINABILITY_LABELS")
    return found


def detect_v2(text: str) -> Set[str]:
    findings = find_issues_on_page("https://benchmark.local", text)
    return {f.category for f in findings}


def evaluate(detector_name: str, detector) -> Dict[str, float]:
    tp = fp = fn = tn = 0
    exact = 0
    for sample in SAMPLES:
        predicted = detector(sample.text)
        expected = sample.expected

        if predicted == expected:
            exact += 1

        if expected:
            if predicted:
                tp += 1
            else:
                fn += 1
        else:
            if predicted:
                fp += 1
            else:
                tn += 1

    total = len(SAMPLES)
    accuracy = (tp + tn) / total
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    false_positive_rate = fp / (fp + tn) if (fp + tn) else 0.0

    return {
        "detector": detector_name,
        "samples": total,
        "accuracy": round(accuracy, 4),
        "recall": round(recall, 4),
        "false_positive_rate": round(false_positive_rate, 4),
        "exact_match_rate": round(exact / total, 4),
    }


if __name__ == "__main__":
    results = {
        "v1": evaluate("v1", detect_v1),
        "v2": evaluate("v2", detect_v2),
    }
    print(json.dumps(results, indent=2))
