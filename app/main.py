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
# App setup (branding via templates + static)
# ---------------------------
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---------------------------
# EmpCo-focused rules (HIGH / MEDIUM)
# Output fields MUST match templates/report.html:
# category, url, message, how_to_fix, evidence, severity
# ---------------------------

# Strong indicators that a page contains substantiation/plan details
PLAN_KEYWORDS = re.compile(
    r"\b("
    r"baseline\s*year|base\s*year|referentiejaar|basisjaar|"
    r"scope\s*1|scope\s*2|scope\s*3|"
    r"interim\s*target|tussen(doel|doelen)|milestone|"
    r"roadmap|transition\s*plan|implement(ation)?\s*plan|actieplan|"
    r"capex|opex|investment|investeringen|"
    r"governance|verantwoordelijkheden|"
    r"third[-\s]?party|independent|verified|assurance|audit|"
    r"SBTi|science[-\s]?based|GHG\s*Protocol|"
    r"methodology|methodologie|calculation|berekening|"
    r"data|kpi|metric|indicator|rapport|report"
    r")\b",
    re.I,
)

# HIGH: future-looking targets with % + year + emissions/CO2 context
FUTURE_TARGET = re.compile(
    r"(?is)\b("
    r"reduce|cut|lower|decrease|bring\s*down|"
    r"aim\s*to|target|commit|pledge|plan\s*to|will|shall"
    r")\b.{0,140}?\b("
    r"emissions?|greenhouse\s*gas|ghg|co2|carbon"
    r")\b.{0,160}?\b("
    r"\d{1,3}\s*(%|percent)"
    r")\b.{0,140}?\b("
    r"by|before|in"
    r")\s*(20\d{2})\b"
)

# HIGH: net zero / carbon neutral / climate neutral / zero emissions
ABSOLUTE_CLAIMS = re.compile(
    r"\b("
    r"net\s*zero|"
    r"carbon\s*neutral|"
    r"climate\s*neutral|"
    r"co2\s*-?\s*neutral|co2\s*-?\s*neutraal|"
    r"zero\s*emissions?|emissie\s*-?\s*vrij|"
    r"fully\s*decarboni(s|z)ed|"
    r"100%\s*(renewable|hernieuwbaar)"
    r")\b",
    re.I,
)

# MEDIUM: vague “environmental claim” / generic sustainability phrasing
VAGUE_ENV_CLAIMS = re.compile(
    r"\b("
    r"sustainable\s+(future|growth|business|hr|work|workforce|strategy)|"
    r"to\s*(a|an)\s*sustainable\s+future|"
    r"creating\s+a\s+better\s+future|"
    r"better\s+for\s+the\s+planet|"
    r"environmentally\s+friendly|eco(\s|-)?friendly|planet(\s|-)?friendly|"
    r"green\s+future|"
    r"responsible\s+business|"
    r"we\s*(care|focus)\s*on\s*sustainability|"
    r"committed\s+to\s+sustainability|"
    r"do\s*our\s*part\s*for\s*(the\s*)?(planet|environment)"
    r")\b",
    re.I,
)

# MEDIUM: “generic environmental claim” without clear scope/meaning
GENERIC_ENV_FRAMING = re.compile(
    r"\b("
    r"environment(al)?\s+(ambition|ambitions|impact|impacts)|"
    r"esg\s+ambitions?|"
    r"green\s+deal|"
    r"international\s+environmental\s+regulations?"
    r")\b",
    re.I,
)


@dataclass
class Finding:
    category: str
    url: str
    message: str
    evidence: str
    severity: str  # "high" or "medium"
    how_to_fix: str


# ---------------------------
# Helpers
# ---------------------------
def normalize_url(u: str) -> str:
    u = (u or "").strip()
    if not u:
        return ""
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    return u


def same_domain(a: str, b: str) -> bool:
    try:
        return urlparse(a).netloc.lower() == urlparse(b).netloc.lower()
    except Exception:
        return False


def fetch_html(url: str, session: requests.Session, timeout: int = 18) -> Optional[str]:
    headers = {
        "User-Agent": "Durably-GreenCheck/1.3 (+https://durably.eu) requests",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        r = session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        if r.status_code >= 400:
            return None
        ct = (r.headers.get("content-type") or "").lower()
        if "text/html" not in ct and "application/xhtml" not in ct:
            return None
        return r.text
    except Exception:
        return None


def extract_links(base_url: str, html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: List[str] = []
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        if href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:") or href.startswith("javascript:"):
            continue
        absolute = urljoin(base_url, href).split("#")[0]
        links.append(absolute)
    return links


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def make_chunks(text: str) -> List[str]:
    """
    More robust than sentence splitting:
    - Keep headings/bullets (often no punctuation)
    - Split on newline, bullets, and punctuation
    """
    if not text:
        return []

    # Normalize bullets
    t = text.replace("•", "\n• ").replace("·", "\n· ").replace("–", "-").replace("—", "-")
    # First split on newlines
    parts = []
    for line in t.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Split long lines on punctuation boundaries too
        sub = re.split(r"(?<=[\.\!\?])\s+|;\s+|\|\s+| - ", line)
        for s in sub:
            s = s.strip()
            if s and len(s) >= 25:
                parts.append(s)

    # Also add “windows” for patterns spanning multiple segments
    joined = " ".join(parts)
    windows = []
    step = 350
    win = 700
    if len(joined) > 0:
        for i in range(0, len(joined), step):
            windows.append(joined[i : i + win].strip())
    return parts + windows


def clip(s: str, n: int = 280) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"


def page_has_plan(text: str) -> bool:
    return bool(PLAN_KEYWORDS.search(text or ""))


# ---------------------------
# Rule evaluation (EmpCo-oriented)
# ---------------------------
def find_issues_on_page(page_url: str, text: str) -> List[Finding]:
    issues: List[Finding] = []
    has_plan = page_has_plan(text)

    chunks = make_chunks(text)

    # 1) FUTURE TARGET (HIGH)
    for ch in chunks:
        m = FUTURE_TARGET.search(ch)
        if not m:
            continue

        pct = m.group(3)
        year = m.group(5)

        if has_plan:
            # If the page has plan keywords somewhere, downgrade to MEDIUM
            sev = "medium"
            msg = f"Future target mentioned ({pct} by {year}) — check if plan details are concrete and verifiable."
            fix = (
                "Zorg dat er publiek een concreet implementatie/transition plan staat: baseline year, scope 1/2/3, "
                "interim milestones, governance, measures, en liefst onafhankelijke verificatie."
            )
        else:
            sev = "high"
            msg = f"Future climate/emissions target ({pct} by {year}) without clear implementation plan context."
            fix = (
                "Voeg naast de claim een publiek, verifieerbaar plan toe: baseline year, scope 1/2/3, interim targets, "
                "maatregelen, governance, rapportage, en onafhankelijke verificatie/assurance."
            )

        issues.append(
            Finding(
                category="FUTURE_TARGET",
                url=page_url,
                message=msg,
                evidence=clip(ch),
                severity=sev,
                how_to_fix=fix,
            )
        )

    # 2) ABSOLUTE CLAIMS (HIGH unless plan evidence exists)
    for ch in chunks:
        if not ABSOLUTE_CLAIMS.search(ch):
            continue

        if has_plan:
            sev = "medium"
            msg = "Absolute climate claim (net zero/carbon neutral/etc.) — verify scope and substantiation."
            fix = (
                "Maak scope expliciet (1/2/3), boundary, methode/standaard, baselines, offsets (indien gebruikt), "
                "en toon verificatie/assurance."
            )
        else:
            sev = "high"
            msg = "Absolute climate claim (net zero/carbon neutral/etc.) without nearby substantiation."
            fix = (
                "Vermijd absolute claims zonder directe onderbouwing. Voeg scope/boundary, methode, baselines, "
                "bewijs/rapport en verificatie toe."
            )

        issues.append(
            Finding(
                category="ABSOLUTE_CLAIM",
                url=page_url,
                message=msg,
                evidence=clip(ch),
                severity=sev,
                how_to_fix=fix,
            )
        )

    # 3) VAGUE ENVIRONMENTAL CLAIMS (MEDIUM)
    for ch in chunks:
        if not VAGUE_ENV_CLAIMS.search(ch):
            continue

        issues.append(
            Finding(
                category="GENERIC_ENVIRONMENTAL_CLAIM",
                url=page_url,
                message="Generic/vague environmental claim — may be considered too broad or unverifiable under EmpCo.",
                evidence=clip(ch),
                severity="medium",
                how_to_fix=(
                    "Vervang vage termen door specifieke, verifieerbare statements: wat exact, scope, KPI’s/metrics, "
                    "periode, methode, en link naar bewijs (rapport/data)."
                ),
            )
        )

    # 4) GENERIC ESG/ENV FRAMING (MEDIUM) — catches “ESG ambitions… reduce GHG… align with Green Deal…”
    for ch in chunks:
        if not GENERIC_ENV_FRAMING.search(ch):
            continue

        issues.append(
            Finding(
                category="ENVIRONMENTAL_FRAMING",
                url=page_url,
                message="Environmental/ESG framing detected — ensure claims are precise, scoped and substantiated.",
                evidence=clip(ch),
                severity="medium",
                how_to_fix=(
                    "Maak claims concreet: definities, scope/boundary, cijfers, methode, en directe link naar onderbouwing "
                    "(niet enkel 'aligned with EU Green Deal')."
                ),
            )
        )

    return issues


# ---------------------------
# Crawl + scan
# ---------------------------
def scan_site(start_url: str, max_pages: int = 10) -> Tuple[int, List[Finding]]:
    start_url = normalize_url(start_url)
    if not start_url:
        return 0, []

    session = requests.Session()
    visited: Set[str] = set()
    queue: deque[str] = deque([start_url])

    findings: List[Finding] = []

    while queue and len(visited) < max_pages:
        url = queue.popleft()
        if url in visited:
            continue
        if not same_domain(start_url, url):
            continue

        html = fetch_html(url, session=session)
        visited.add(url)

        if not html:
            continue

        # enqueue more links
        for link in extract_links(url, html):
            if link not in visited and same_domain(start_url, link):
                queue.append(link)

        text = html_to_text(html)
        findings.extend(find_issues_on_page(url, text))

        time.sleep(0.12)

    # Deduplicate identical evidence on same url/category/message
    uniq = {}
    for f in findings:
        key = (f.url, f.category, f.message, f.evidence[:120])
        uniq[key] = f
    findings = list(uniq.values())

    # Sort: high first then medium
    findings.sort(key=lambda x: (0 if x.severity == "high" else 1, x.url, x.category))
    return len(visited), findings


def calc_risk_score(findings: List[Finding]) -> int:
    high = sum(1 for f in findings if f.severity == "high")
    med = sum(1 for f in findings if f.severity == "medium")
    # heavier weighting (EmpCo focus)
    score = high * 25 + med * 10
    return max(0, min(100, score))


# ---------------------------
# Routes (keep branding + match your templates)
# ---------------------------
@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/scan")
async def scan(request: Request, url: str = Form(...), max_pages: int = Form(10)):
    target = normalize_url(url)

    try:
        max_pages_int = int(max_pages)
    except Exception:
        max_pages_int = 10
    max_pages_int = max(1, min(50, max_pages_int))

    if not target:
        return RedirectResponse(url="/", status_code=303)

    pages_scanned, findings_obj = scan_site(target, max_pages=max_pages_int)
    risk = calc_risk_score(findings_obj)

    # Convert to dicts exactly as your report.html expects:
    findings = [
        {
            "category": f.category,
            "url": f.url,
            "message": f.message,
            "how_to_fix": f.how_to_fix,
            "evidence": f.evidence,
            "severity": f.severity,  # "high" / "medium"
        }
        for f in findings_obj
    ]

    context = {
        "request": request,
        "input_url": target,         # <-- your template uses input_url
        "pages_scanned": pages_scanned,
        "risk_score": risk,
        "findings": findings,        # <-- your template loops findings
    }

    return templates.TemplateResponse("report.html", context)


@app.get("/health")
async def health():
    return {"ok": True}
