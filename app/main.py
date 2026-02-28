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

# Serve /static/style.css and your logo svg
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")


# ---------------------------
# Expanded "green claims" list (NL + EN + common variants)
# We use regex patterns so we catch variations.
# ---------------------------
GREEN_CLAIMS_PATTERNS: List[Tuple[str, re.Pattern]] = [
    # Generic "green" / "eco" claims
    ("Eco-friendly / milieuvriendelijk", re.compile(r"\b(milieuvriendelijk|eco(\s|-)?friendly|eco(\s|-)?vriendelijk)\b", re.I)),
    ("Duurzaam / sustainable", re.compile(r"\b(duurzaam(heid)?|sustainab(le|ility))\b", re.I)),
    ("Groen / green", re.compile(r"\b(groen(e)?|green)\b", re.I)),
    ("Planet friendly", re.compile(r"\bplanet(\s|-)?friendly\b", re.I)),
    ("Environmentally friendly", re.compile(r"\benvironmentally(\s|-)?friendly\b", re.I)),

    # Carbon / climate
    ("CO2-neutraal / carbon neutral", re.compile(r"\b(co2(\s|-)?neutraal|carbon(\s|-)?neutral|klimaat(\s|-)?neutraal|climate(\s|-)?neutral)\b", re.I)),
    ("Net zero", re.compile(r"\bnet(\s|-)?zero\b", re.I)),
    ("Emissievrij / zero emissions", re.compile(r"\b(emissie(\s|-)?vrij|zero(\s|-)?emissions?)\b", re.I)),
    ("Low carbon / lage uitstoot", re.compile(r"\b(low(\s|-)?carbon|lage(\s|-)?uitstoot|reduced(\s|-)?emissions?)\b", re.I)),
    ("CO2-compensatie / offsetting", re.compile(r"\b(compensat(ie|ies)|offset(s|ting)?|carbon(\s|-)?offset)\b", re.I)),

    # Energy
    ("100% hernieuwbaar / 100% renewable", re.compile(r"\b(100%\s*(hernieuwbaar|renewable)|volledig\s*(hernieuwbaar|renewable))\b", re.I)),
    ("Groene energie / green energy", re.compile(r"\b(groene(\s|-)?energie|green(\s|-)?energy)\b", re.I)),
    ("Energieneutraal", re.compile(r"\b(energie(\s|-)?neutraal|energy(\s|-)?neutral)\b", re.I)),
    ("Energiezuinig / energy efficient", re.compile(r"\b(energie(\s|-)?zuinig|energy(\s|-)?efficient|energy(\s|-)?saving)\b", re.I)),

    # Circularity / waste
    ("Recycleerbaar / recyclable", re.compile(r"\b(recycle(er)?baar|recyclable)\b", re.I)),
    ("Gerecycled / recycled content", re.compile(r"\b(gerecycl(ed|de)?|recycled(\s|-)?content)\b", re.I)),
    ("Plasticvrij / plastic free", re.compile(r"\b(plastic(\s|-)?vrij|plastic(\s|-)?free)\b", re.I)),
    ("Zero waste / afvalvrij", re.compile(r"\b(zero(\s|-)?waste|afval(\s|-)?vrij)\b", re.I)),
    ("Circulair / circular", re.compile(r"\b(circulair|circular(ity)?)\b", re.I)),
    ("Biologisch afbreekbaar / biodegradable", re.compile(r"\b(biologisch(\s|-)?afbreekbaar|biodegradab(le|ility))\b", re.I)),
    ("Composteerbaar / compostable", re.compile(r"\b(composteerbaar|compostable)\b", re.I)),

    # Nature / biodiversity
    ("Natuurvriendelijk / nature positive", re.compile(r"\b(natuurvriendelijk|nature(\s|-)?positive)\b", re.I)),
    ("Biodiversiteit", re.compile(r"\b(biodiversiteit|biodiversity)\b", re.I)),
    ("Ontbossingsvrij / deforestation-free", re.compile(r"\b(ontbossings(\s|-)?vrij|deforestation(\s|-)?free)\b", re.I)),

    # Materials / chemicals
    ("Niet-toxisch / non-toxic", re.compile(r"\b(niet(\s|-)?toxisch|non(\s|-)?toxic)\b", re.I)),
    ("Vrij van schadelijke stoffen", re.compile(r"\b(vrij(\s|-)?van(\s|-)?schadelijke(\s|-)?stoffen|free(\s|-)?from(\s|-)?harmful(\s|-)?chemicals?)\b", re.I)),
    ("PFAS-vrij", re.compile(r"\bpfas(\s|-)?vrij\b", re.I)),

    # “Claims of certification” (often misused / needs proof)
    ("Gecertificeerd / certified", re.compile(r"\b(gecertificeerd|certified|certification)\b", re.I)),
    ("ISO 14001", re.compile(r"\biso(\s|-)?14001\b", re.I)),
    ("B Corp / B-Corp", re.compile(r"\b(b(\s|-)?corp)\b", re.I)),
    ("FSC", re.compile(r"\bfsc\b", re.I)),
    ("PEFC", re.compile(r"\bpefc\b", re.I)),
    ("EU Ecolabel", re.compile(r"\b(eu(\s|-)?ecolabel|european(\s|-)?ecolabel)\b", re.I)),
    ("Fairtrade", re.compile(r"\bfair(\s|-)?trade\b", re.I)),
]


# Some “supporting evidence” hints (if present => lower risk)
EVIDENCE_HINTS = [
    r"\b(lca|life\s*cycle\s*assessment|levenscyclusanalyse)\b",
    r"\b(scope\s*[123]|ghg\s*protocol|greenhouse\s*gas)\b",
    r"\b(rapport|report|methodology|methodologie|assumptions|aannames)\b",
    r"\b(audit|verified|assurance|third(\s|-)?party|derde\s*partij)\b",
    r"\b(iso\s*14001|iso\s*14064|iso\s*14067)\b",
    r"\b(ecolabel|fsc|pefc|b\s*corp|fairtrade)\b",
    r"\b(certificaat|certificate|certification)\b",
    r"\b(data|dataset|figures|metrics|kpi|indicator)\b",
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
        "User-Agent": "GreenCheckBot/1.1 (+https://durably.eu) requests",
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
        href = a.get("href", "").strip()
        if not href:
            continue
        # Skip mailto/tel/javascript/anchors
        if href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:") or href.startswith("javascript:"):
            continue
        absolute = urljoin(base_url, href)
        # Drop URL fragments
        absolute = absolute.split("#")[0]
        links.append(absolute)
    return links


def page_text_and_sentences(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    # remove non-content
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator=" ")
    text = re.sub(r"\s+", " ", text).strip()

    # Split into “sentences-ish”
    # (good enough for a beta scanner)
    parts = re.split(r"(?<=[\.\!\?])\s+|[\n\r]+", text)
    parts = [p.strip() for p in parts if p and len(p.strip()) >= 25]
    return parts


def build_snippet(sentence: str, max_len: int = 220) -> str:
    s = sentence.strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1].rstrip() + "…"


def severity_score(label: str, sentence: str) -> Tuple[str, str]:
    """
    Heuristic severity:
    - high: absolute claims like 100%, zero, carbon neutral, net zero, emissions free, etc. without evidence hints
    - medium: general sustainable/eco claims without evidence hints
    - low: has evidence hints nearby
    """
    abs_claim = bool(re.search(r"\b(100%|zero|net(\s|-)?zero|co2(\s|-)?neutraal|carbon(\s|-)?neutral|emissie(\s|-)?vrij|zero(\s|-)?emissions?)\b", sentence, re.I))
    has_evidence = bool(EVIDENCE_REGEX.search(sentence))

    if has_evidence:
        return "low", "Claim lijkt (deels) ondersteund door verwijzing naar methodologie/certificering/data."
    if abs_claim:
        return "high", "Absolute klimaat/CO2-claim zonder directe onderbouwing in dezelfde context."
    return "medium", "Algemene duurzaamheidsclaim zonder directe onderbouwing in dezelfde context."


def scan_site(start_url: str, max_pages: int = 10) -> Tuple[int, List[Finding]]:
    start_url = normalize_url(start_url)
    if not start_url:
        return 0, []

    session = requests.Session()

    visited: Set[str] = set()
    queue: deque[str] = deque([start_url])

    findings: List[Finding] = []

    # crawl
    while queue and len(visited) < max_pages:
        url = queue.popleft()
        if url in visited:
            continue

        # Only same domain
        if not same_domain(start_url, url):
            continue

        html = fetch_html(url, session=session)
        visited.add(url)

        if not html:
            continue

        # collect more links
        for link in extract_links(url, html):
            if link not in visited and same_domain(start_url, link):
                queue.append(link)

        # analyze text
        sentences = page_text_and_sentences(html)
        for sentence in sentences:
            for label, pattern in GREEN_CLAIMS_PATTERNS:
                if pattern.search(sentence):
                    sev, notes = severity_score(label, sentence)
                    findings.append(
                        Finding(
                            label=label,
                            page_url=url,
                            snippet=build_snippet(sentence),
                            severity=sev,
                            notes=notes,
                        )
                    )

        # small politeness delay
        time.sleep(0.15)

    return len(visited), findings


def calc_risk_score(findings: List[Finding]) -> int:
    """
    Simple risk score (0-100) based on severity counts.
    """
    high = sum(1 for f in findings if f.severity == "high")
    med = sum(1 for f in findings if f.severity == "medium")
    low = sum(1 for f in findings if f.severity == "low")

    score = high * 12 + med * 6 + low * 2
    return max(0, min(100, score))


# ---------------------------
# Routes (keep branding!)
# ---------------------------
@app.get("/")
async def home(request: Request):
    # IMPORTANT: this keeps your Durably branding (templates + css)
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/scan")
async def scan(request: Request, url: str = Form(...), max_pages: int = Form(10)):
    target = normalize_url(url)
    try:
        max_pages_int = int(max_pages)
    except Exception:
        max_pages_int = 10

    max_pages_int = max(1, min(50, max_pages_int))  # safety cap

    if not target:
        return RedirectResponse(url="/", status_code=303)

    pages_scanned, findings = scan_site(target, max_pages=max_pages_int)
    risk = calc_risk_score(findings)

    # group findings by severity for nicer report
    grouped: Dict[str, List[Finding]] = {"high": [], "medium": [], "low": []}
    for f in findings:
        grouped.setdefault(f.severity, []).append(f)

    # sort: high first, then medium, then low
    grouped["high"].sort(key=lambda x: x.page_url)
    grouped["medium"].sort(key=lambda x: x.page_url)
    grouped["low"].sort(key=lambda x: x.page_url)

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

    # IMPORTANT: this keeps your Durably branding (templates + css)
    return templates.TemplateResponse("report.html", context)


# Optional: health check
@app.get("/health")
async def health():
    return {"ok": True}
