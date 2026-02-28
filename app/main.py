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
# YOUR EXISTING GREEN_CLAIMS_PATTERNS (unchanged)
# ---------------------------
GREEN_CLAIMS_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("Eco-friendly / milieuvriendelijk", re.compile(r"\b(milieuvriendelijk|eco(\s|-)?friendly|eco(\s|-)?vriendelijk)\b", re.I)),
    ("Duurzaam / sustainable", re.compile(r"\b(duurzaam(heid)?|sustainab(le|ility))\b", re.I)),
    ("Groen / green", re.compile(r"\b(groen(e)?|green)\b", re.I)),
    ("Planet friendly", re.compile(r"\bplanet(\s|-)?friendly\b", re.I)),
    ("Environmentally friendly", re.compile(r"\benvironmentally(\s|-)?friendly\b", re.I)),

    ("CO2-neutraal / carbon neutral", re.compile(r"\b(co2(\s|-)?neutraal|carbon(\s|-)?neutral|klimaat(\s|-)?neutraal|climate(\s|-)?neutral)\b", re.I)),
    ("Net zero", re.compile(r"\bnet(\s|-)?zero\b", re.I)),
    ("Emissievrij / zero emissions", re.compile(r"\b(emissie(\s|-)?vrij|zero(\s|-)?emissions?)\b", re.I)),
    ("Low carbon / lage uitstoot", re.compile(r"\b(low(\s|-)?carbon|lage(\s|-)?uitstoot|reduced(\s|-)?emissions?)\b", re.I)),
    ("CO2-compensatie / offsetting", re.compile(r"\b(compensat(ie|ies)|offset(s|ting)?|carbon(\s|-)?offset)\b", re.I)),

    ("100% hernieuwbaar / 100% renewable", re.compile(r"\b(100%\s*(hernieuwbaar|renewable)|volledig\s*(hernieuwbaar|renewable))\b", re.I)),
    ("Groene energie / green energy", re.compile(r"\b(groene(\s|-)?energie|green(\s|-)?energy)\b", re.I)),
    ("Energieneutraal", re.compile(r"\b(energie(\s|-)?neutraal|energy(\s|-)?neutral)\b", re.I)),
    ("Energiezuinig / energy efficient", re.compile(r"\b(energie(\s|-)?zuinig|energy(\s|-)?efficient|energy(\s|-)?saving)\b", re.I)),

    ("Recycleerbaar / recyclable", re.compile(r"\b(recycle(er)?baar|recyclable)\b", re.I)),
    ("Gerecycled / recycled content", re.compile(r"\b(gerecycl(ed|de)?|recycled(\s|-)?content)\b", re.I)),
    ("Plasticvrij / plastic free", re.compile(r"\b(plastic(\s|-)?vrij|plastic(\s|-)?free)\b", re.I)),
    ("Zero waste / afvalvrij", re.compile(r"\b(zero(\s|-)?waste|afval(\s|-)?vrij)\b", re.I)),
    ("Circulair / circular", re.compile(r"\b(circulair|circular(ity)?)\b", re.I)),
    ("Biologisch afbreekbaar / biodegradable", re.compile(r"\b(biologisch(\s|-)?afbreekbaar|biodegradab(le|ility))\b", re.I)),
    ("Composteerbaar / compostable", re.compile(r"\b(composteerbaar|compostable)\b", re.I)),

    ("Natuurvriendelijk / nature positive", re.compile(r"\b(natuurvriendelijk|nature(\s|-)?positive)\b", re.I)),
    ("Biodiversiteit", re.compile(r"\b(biodiversiteit|biodiversity)\b", re.I)),
    ("Ontbossingsvrij / deforestation-free", re.compile(r"\b(ontbossings(\s|-)?vrij|deforestation(\s|-)?free)\b", re.I)),

    ("Niet-toxisch / non-toxic", re.compile(r"\b(niet(\s|-)?toxisch|non(\s|-)?toxic)\b", re.I)),
    ("Vrij van schadelijke stoffen", re.compile(r"\b(vrij(\s|-)?van(\s|-)?schadelijke(\s|-)?stoffen|free(\s|-)?from(\s|-)?harmful(\s|-)?chemicals?)\b", re.I)),
    ("PFAS-vrij", re.compile(r"\bpfas(\s|-)?vrij\b", re.I)),

    ("Gecertificeerd / certified", re.compile(r"\b(gecertificeerd|certified|certification)\b", re.I)),
    ("ISO 14001", re.compile(r"\biso(\s|-)?14001\b", re.I)),
    ("B Corp / B-Corp", re.compile(r"\b(b(\s|-)?corp)\b", re.I)),
    ("FSC", re.compile(r"\bfsc\b", re.I)),
    ("PEFC", re.compile(r"\bpefc\b", re.I)),
    ("EU Ecolabel", re.compile(r"\b(eu(\s|-)?ecolabel|european(\s|-)?ecolabel)\b", re.I)),
    ("Fairtrade", re.compile(r"\bfair(\s|-)?trade\b", re.I)),
]


# ---------------------------
# B: Additional “EmpCo-style” detection
# ---------------------------
VAGUE_CLAIM_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("Vage claim: commitment/ambitie", re.compile(r"\b(committed|commitment|ambition|we aim|we strive|we work towards|dedicated|our goal|doelstelling|streven|ambitie)\b", re.I)),
    ("Vage claim: verantwoordelijk/responsible", re.compile(r"\b(responsible|verantwoord|conscious|bewust|respect for (the )?environment)\b", re.I)),
]

TARGET_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("Target / deadline claim", re.compile(r"\b(by|tegen)\s*20\d{2}\b", re.I)),
    ("Emissiereductie target", re.compile(r"\b(reduce|reduction|reduceer|verminder)\b.*\b(emissions?|emissies?|greenhouse gas|ghg|broeikasgas)\b", re.I)),
    ("Percentage reductie", re.compile(r"\b\d{1,3}\s*%\b.*\b(reduce|reduction|minder|lower)\b", re.I)),
]

COMPARATIVE_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("Vergelijkende claim", re.compile(r"\b(\d{1,3}\s*%)\s*(less|lower|minder)\b|\b(greener|greenest|most sustainable|duurzaamste|better for the environment|beter voor het milieu)\b", re.I)),
]

# Page-level evidence hints (stronger than sentence-level)
EVIDENCE_HINTS = [
    r"\b(lca|life\s*cycle\s*assessment|levenscyclusanalyse)\b",
    r"\b(scope\s*[123]|ghg\s*protocol|greenhouse\s*gas|broeikasgas)\b",
    r"\b(rapport|report|methodology|methodologie|assumptions|aannames)\b",
    r"\b(audit|verified|assurance|third(\s|-)?party|derde\s*partij)\b",
    r"\b(iso\s*14001|iso\s*14064|iso\s*14067)\b",
    r"\b(ecolabel|fsc|pefc|b\s*corp|fairtrade|ecovadis)\b",
    r"\b(certificaat|certificate|certification)\b",
    r"\b(data|dataset|figures|metrics|kpi|indicator)\b",
    r"\b(download|pdf)\b",
]
EVIDENCE_REGEX = re.compile("|".join(EVIDENCE_HINTS), re.I)


@dataclass
class Finding:
    label: str
    page_url: str
    snippet: str
    severity: str  # low / medium / high
    notes: str


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


def fetch_html(url: str, session: requests.Session, timeout: int = 15) -> Optional[str]:
    headers = {
        "User-Agent": "GreenCheckBot/2.0 (+https://durably.eu) requests",
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
    links = []
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        if href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:") or href.startswith("javascript:"):
            continue
        absolute = urljoin(base_url, href).split("#")[0]
        links.append(absolute)
    return links


def extract_all_text(html: str) -> str:
    """
    Extract more than just visible body text:
    - visible text
    - meta description/og:description
    - titles, alt text
    This improves recall vs strict sentence splitting.
    """
    soup = BeautifulSoup(html, "html.parser")

    # remove scripts/styles but KEEP text from noscript (sometimes important)
    for tag in soup(["script", "style", "svg"]):
        tag.decompose()

    chunks: List[str] = []

    # meta descriptions
    for meta_name in ["description", "og:description", "twitter:description"]:
        m = soup.find("meta", attrs={"name": meta_name}) or soup.find("meta", attrs={"property": meta_name})
        if m and m.get("content"):
            chunks.append(m["content"])

    # title
    if soup.title and soup.title.string:
        chunks.append(soup.title.string)

    # alt text
    for img in soup.find_all("img", alt=True):
        if img.get("alt"):
            chunks.append(img["alt"])

    # visible text
    visible = soup.get_text(separator=" ")
    visible = re.sub(r"\s+", " ", visible).strip()
    chunks.append(visible)

    all_text = " ".join(chunks)
    all_text = re.sub(r"\s+", " ", all_text).strip()
    return all_text


def snippet_around(text: str, start: int, end: int, window: int = 140) -> str:
    s = max(0, start - window)
    e = min(len(text), end + window)
    return re.sub(r"\s+", " ", text[s:e]).strip()


def severity_for_match(label: str, page_has_evidence: bool, matched_text: str) -> Tuple[str, str]:
    abs_claim = bool(re.search(r"\b(100%|zero|net(\s|-)?zero|co2(\s|-)?neutraal|carbon(\s|-)?neutral|emissie(\s|-)?vrij|zero(\s|-)?emissions?)\b", matched_text, re.I))
    if page_has_evidence:
        return "low", "Op de pagina staan signalen van onderbouwing (rapport/methodologie/certificering/data)."
    if abs_claim:
        return "high", "Absolute klimaat/CO₂ claim of sterke claim zonder zichtbare onderbouwing op de pagina."
    return "medium", "Claim gedetecteerd zonder zichtbare onderbouwing op de pagina."


def add_matches(findings: List[Finding], page_url: str, text: str, label: str, pattern: re.Pattern, page_has_evidence: bool):
    for m in pattern.finditer(text):
        snip = snippet_around(text, m.start(), m.end())
        sev, notes = severity_for_match(label, page_has_evidence, snip)
        findings.append(Finding(label=label, page_url=page_url, snippet=snip, severity=sev, notes=notes))


def dedupe_findings(findings: List[Finding]) -> List[Finding]:
    seen: Set[str] = set()
    out: List[Finding] = []
    for f in findings:
        key = f"{f.page_url}|{f.label}|{f.severity}|{f.snippet[:120]}"
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


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

        # queue same-domain links
        for link in extract_links(url, html):
            if link not in visited and same_domain(start_url, link):
                queue.append(link)

        text = extract_all_text(html)
        page_has_evidence = bool(EVIDENCE_REGEX.search(text))

        # A) Your green claim patterns
        for label, pattern in GREEN_CLAIMS_PATTERNS:
            add_matches(findings, url, text, label, pattern, page_has_evidence)

        # B) Vague/targets/comparisons
        for label, pattern in VAGUE_CLAIM_PATTERNS:
            add_matches(findings, url, text, label, pattern, page_has_evidence)

        for label, pattern in TARGET_PATTERNS:
            add_matches(findings, url, text, label, pattern, page_has_evidence)

        for label, pattern in COMPARATIVE_PATTERNS:
            add_matches(findings, url, text, label, pattern, page_has_evidence)

        time.sleep(0.15)

    findings = dedupe_findings(findings)
    return len(visited), findings


def calc_risk_score(findings: List[Finding]) -> int:
    """
    More realistic risk score:
    - high findings weigh more
    - medium moderate
    - low minimal
    """
    high = sum(1 for f in findings if f.severity == "high")
    med = sum(1 for f in findings if f.severity == "medium")
    low = sum(1 for f in findings if f.severity == "low")

    score = high * 18 + med * 9 + low * 2
    return max(0, min(100, score))


# ---------------------------
# Routes (keep branding!)
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

    pages_scanned, findings = scan_site(target, max_pages=max_pages_int)
    risk = calc_risk_score(findings)

    grouped: Dict[str, List[Finding]] = {"high": [], "medium": [], "low": []}
    for f in findings:
        grouped.setdefault(f.severity, []).append(f)

    grouped["high"].sort(key=lambda x: (x.page_url, x.label))
    grouped["medium"].sort(key=lambda x: (x.page_url, x.label))
    grouped["low"].sort(key=lambda x: (x.page_url, x.label))

    context = {
        "request": request,
        "target_url": target,
        "pages_scanned": pages_scanned,
        "max_pages": max_pages_int,
        "risk_score": risk,
        "findings_total": len(findings),
        "findings_high": grouped["high"],
        "findings_medium": grouped["medium"],
        "findings_low": grouped["low"],
        "disclaimer": "Deze scan is indicatief en vormt geen juridisch advies.",
    }
    return templates.TemplateResponse("report.html", context)


@app.get("/health")
async def health():
    return {"ok": True}
