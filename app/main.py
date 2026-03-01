from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass
from typing import List, Dict, Set, Tuple, Optional

import requests
from bs4 import BeautifulSoup

from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from urllib.parse import urlparse, urljoin


# ---------------------------
# App setup (branding intact)
# ---------------------------
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---------------------------
# EmpCo-focused claim detection
# ---------------------------

CLAIM_TYPES: List[Tuple[str, re.Pattern, str]] = [
    # 🔴 FUTURE TARGETS (HIGH RISK)
    (
        "Future climate/emissions target",
        re.compile(
            r"(reduce|cut|lower|decrease|aim\s+to\s+reduce)[^.]{0,80}?"
            r"(emissions|co2|carbon|ghg)[^.]{0,80}?"
            r"(at\s+least\s+)?\d{1,3}\s*%[^.]{0,80}?"
            r"(by|before)\s+20\d{2}",
            re.I,
        ),
        "high",
    ),

    # 🔴 NET ZERO / CARBON NEUTRAL CLAIMS
    (
        "Net zero / carbon neutral claim",
        re.compile(r"\b(net\s*zero|carbon\s*neutral|climate\s*neutral)\b", re.I),
        "high",
    ),

    # 🟡 VAGUE ENVIRONMENTAL CLAIMS (EmpCo focus)
    (
        "Vague environmental claim",
        re.compile(
            r"\b("
            r"sustainable\s+(future|growth|business|work|hr)|"
            r"green\s+future|"
            r"environmentally\s+friendly|"
            r"eco(\s|-)?friendly|"
            r"planet\s+friendly|"
            r"better\s+for\s+the\s+planet|"
            r"future\s+for\s+(employees|customers|society)|"
            r"responsible\s+business"
            r")\b",
            re.I,
        ),
        "medium",
    ),

    # 🟡 GENERIC SUSTAINABILITY CLAIMS
    (
        "Generic sustainability claim",
        re.compile(r"\b(sustainable|duurzaam|green)\b", re.I),
        "medium",
    ),
]


# Evidence hints (reduces severity if present)
EVIDENCE_REGEX = re.compile(
    r"(report|methodology|data|kpi|verified|audit|lca|scope\s*[123]|science\s*based\s*targets)",
    re.I,
)


@dataclass
class Finding:
    label: str
    page_url: str
    snippet: str
    severity: str
    notes: str


# ---------------------------
# Helpers
# ---------------------------
def normalize_url(u: str) -> str:
    if not u:
        return ""
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    return u.strip()


def same_domain(a: str, b: str) -> bool:
    return urlparse(a).netloc.lower() == urlparse(b).netloc.lower()


def fetch_html(url: str, session: requests.Session) -> Optional[str]:
    try:
        r = session.get(url, timeout=15)
        if r.status_code >= 400:
            return None
        return r.text
    except Exception:
        return None


def extract_links(base_url: str, html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("#") or href.startswith("mailto:"):
            continue
        absolute = urljoin(base_url, href).split("#")[0]
        links.append(absolute)
    return links


def extract_sentences(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(" ")
    text = re.sub(r"\s+", " ", text)

    parts = re.split(r"(?<=[\.\!\?])\s+", text)
    return [p.strip() for p in parts if len(p.strip()) > 40]


def classify_claim(label: str, sentence: str, base_severity: str) -> Tuple[str, str]:
    if EVIDENCE_REGEX.search(sentence):
        return "low", "Claim bevat verwijzing naar onderbouwing (rapport/methodologie/data)."
    if base_severity == "high":
        return "high", "Toekomstige klimaatdoelstelling vereist concreet plan volgens EmpCo."
    return "medium", "Vage of algemene milieuclaim mogelijk strijdig met EmpCo."


# ---------------------------
# Scanner
# ---------------------------
def scan_site(start_url: str, max_pages: int = 10):
    start_url = normalize_url(start_url)
    session = requests.Session()

    visited: Set[str] = set()
    queue = deque([start_url])
    findings: List[Finding] = []

    while queue and len(visited) < max_pages:
        url = queue.popleft()
        if url in visited or not same_domain(start_url, url):
            continue

        html = fetch_html(url, session)
        visited.add(url)
        if not html:
            continue

        for link in extract_links(url, html):
            if link not in visited:
                queue.append(link)

        sentences = extract_sentences(html)

        for s in sentences:
            for label, pattern, severity_base in CLAIM_TYPES:
                if pattern.search(s):
                    severity, notes = classify_claim(label, s, severity_base)
                    if severity == "low":
                        continue  # 🔥 filter out low risk
                    findings.append(
                        Finding(
                            label=label,
                            page_url=url,
                            snippet=s[:220] + "…",
                            severity=severity,
                            notes=notes,
                        )
                    )

        time.sleep(0.1)

    return len(visited), findings


def calc_risk_score(findings: List[Finding]) -> int:
    high = sum(1 for f in findings if f.severity == "high")
    medium = sum(1 for f in findings if f.severity == "medium")
    return min(100, high * 20 + medium * 10)


# ---------------------------
# Routes (branding stays)
# ---------------------------
@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request:": request})


@app.post("/scan")
async def scan(request: Request, url: str = Form(...), max_pages: int = Form(10)):
    target = normalize_url(url)
    pages_scanned, findings = scan_site(target, max_pages)

    risk = calc_risk_score(findings)

    context = {
        "request": request,
        "input_url": target,
        "pages_scanned": pages_scanned,
        "risk_score": risk,
        "findings": findings,
    }

    return templates.TemplateResponse("report.html", context)
