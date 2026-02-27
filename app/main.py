
from __future__ import annotations

import hashlib
import re
from collections import deque
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


# ----------------------------
# App setup
# ----------------------------
app = FastAPI(title="Durably GreenCheck (Beta)")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

DISCLAIMER = (
    "Disclaimer: Deze scan is een geautomatiseerde, indicatieve analyse van publiek beschikbare webinhoud. "
    "De resultaten vormen geen juridisch advies en garanderen geen volledige naleving van Richtlijn (EU) 2024/825 "
    "of andere toepasselijke wetgeving."
)

UA = "DurablyGreenCheckBot/0.1 (+https://durably.eu)"
TIMEOUT = 15


# ----------------------------
# Finding model
# ----------------------------
@dataclass
class Finding:
    category: str           # EmpCo-style category label (practical)
    severity: str           # low | medium | high
    message: str            # what we detected
    how_to_fix: str         # what to check / improve
    evidence: str           # snippet
    url: str                # page URL


# ----------------------------
# Helpers
# ----------------------------
def normalize_url(u: str) -> str:
    u = (u or "").strip()
    if not u:
        return ""
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    return u


def same_host(seed: str, candidate: str) -> bool:
    try:
        return urlparse(seed).netloc.lower() == urlparse(candidate).netloc.lower()
    except Exception:
        return False


def is_probably_html_response(resp: requests.Response) -> bool:
    ctype = (resp.headers.get("content-type") or "").lower()
    return ("text/html" in ctype) or ("application/xhtml" in ctype)


def safe_get(url: str) -> Optional[str]:
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code >= 400:
            return None
        if not is_probably_html_response(r):
            return None
        return r.text
    except Exception:
        return None


def extract_text_links(base_url: str, html: str) -> Tuple[str, List[str]]:
    soup = BeautifulSoup(html, "lxml")

    # remove non-content
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    text = " ".join(soup.get_text(" ").split())

    links: List[str] = []
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href:
            continue

        # skip non-http(s)
        if href.startswith(("mailto:", "tel:", "javascript:")):
            continue

        nxt = urljoin(base_url, href)
        p = urlparse(nxt)
        if p.scheme not in ("http", "https"):
            continue

        # remove fragments
        nxt = p._replace(fragment="").geturl()

        # skip obvious non-html files
        lower = nxt.lower()
        if any(lower.endswith(ext) for ext in [".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".zip", ".mp4", ".mov", ".avi", ".mp3"]):
            continue

        links.append(nxt)

    return text, links


def snippet(text: str, start: int, end: int, window: int = 140) -> str:
    s = max(0, start - window)
    e = min(len(text), end + window)
    return " ".join(text[s:e].split())


def dedupe_key(f: Finding) -> str:
    # Keep dedupe stable, avoid spamming same finding across same url/snippet
    base = f"{f.category}|{f.severity}|{f.url}|{f.message}|{f.evidence[:180]}"
    return hashlib.sha1(base.encode("utf-8", errors="ignore")).hexdigest()


# ----------------------------
# EmpCo beta rules (v1.1)
# ----------------------------
# 1) Vague/Generic environmental claims (NL/EN)
VAGUE_GREEN = [
    r"\bduurzaam\b",
    r"\bmilieuvriendelijk\b",
    r"\beco\b",
    r"\begroen\b",
    r"\bgreen\b",
    r"\benvironmentally friendly\b",
    r"\bplanet[- ]?friendly\b",
    r"\bresponsible (choice|product|sourcing)\b",
    r"\bconscious (choice|product)\b",
]

# 2) Absolute claims (often high risk if unqualified)
ABSOLUTE_CLAIMS = [
    r"\b100%\s*(duurzaam|sustainable|eco|green)\b",
    r"\bvolledig\s*(duurzaam|groen|eco)\b",
    r"\bcompletely\s*(sustainable|green|eco)\b",
    r"\bzero\s*(impact|emissions?)\b",
    r"\bno\s*(impact|emissions?)\b",
    r"\bzonder\s*impact\b",
    r"\bimpact[- ]?free\b",
]

# 3) Climate neutrality / Net zero
CLIMATE_NEUTRAL = [
    r"\bklimaatneutraal\b",
    r"\bco2[- ]?neutraal\b",
    r"\bcarbon neutral\b",
    r"\bclimate neutral\b",
    r"\bnet[- ]?zero\b",
    r"\bzero carbon\b",
]

# 4) Offsetting / compensation
OFFSETS = [
    r"\bcompens(atie|eren)\b",
    r"\boffset(s|ting)?\b",
    r"\bcarbon credits?\b",
    r"\bverified offsets?\b",
]

# 5) Comparative claims (needs basis/method)
COMPARISONS = [
    r"\b(\d{1,3}\s*%)\s*(minder|less|lower)\s*(co2|emissies|emissions?)\b",
    r"\b(\d{1,3}\s*%)\s*(beter|better|greener)\b",
    r"\b(more|much more|significantly)\s*(sustainable|eco|green)\b",
    r"\b(duurzaamste|greenest|most sustainable)\b",
    r"\b(beter\s+voor\s+het\s+milieu|better\s+for\s+the\s+environment)\b",
]

# 6) Future claims (plans/targets)
FUTURE = [
    r"\bby\s*20\d{2}\b.*\b(net[- ]?zero|carbon neutral|climate neutral)\b",
    r"\btegen\s*20\d{2}\b.*\b(klimaatneutraal|co2[- ]?neutraal)\b",
    r"\bwe\s+(will|aim to|plan to)\b.*\b(net[- ]?zero|carbon neutral)\b",
    r"\bdoel(stelling)?\b.*\b20\d{2}\b",
    r"\btarget\b.*\b20\d{2}\b",
]

# 7) Labels / certifications (heuristic)
LABEL_TERMS = [
    r"\bFSC\b", r"\bPEFC\b", r"\bEU\s*Ecolabel\b", r"\bFairtrade\b", r"\bB\s*Corp\b",
    r"\bISO\s*14001\b", r"\bEMAS\b", r"\bCradle\s*to\s*Cradle\b", r"\bEnergy\s*Star\b",
    r"\bGOTS\b", r"\bOEKO[- ]?TEX\b", r"\bRainforest\s*Alliance\b",
    r"\bcertificat(e|ie)\b", r"\bgecertificeerd\b", r"\bcertified\b",
]

# “Proof-ish” words / links suggest on-page substantiation exists
PROOF_HINTS = [
    r"\bmethod(iek|ology)\b",
    r"\bLCA\b",
    r"\blife cycle\b",
    r"\bEPD\b",
    r"\bfootprint\b",
    r"\bscope\s*[123]\b",
    r"\bverificatie\b",
    r"\bindependent\b",
    r"\bthird[- ]party\b",
    r"\baudit\b",
    r"\breport\b",
    r"\bdownload\b",
    r"\bpolicy\b",
    r"\bdata\b",
]


def has_proof_hints(page_text: str) -> bool:
    pat = re.compile("|".join(PROOF_HINTS), re.IGNORECASE)
    return bool(pat.search(page_text))


def run_rules(page_url: str, page_text: str) -> List[Finding]:
    findings: List[Finding] = []

    proof_on_page = has_proof_hints(page_text)

    def add_many(patterns: List[str], category: str, severity_if_no_proof: str, severity_if_proof: str,
                 message: str, how_to_fix: str):
        pat = re.compile("|".join(patterns), re.IGNORECASE)
        for m in pat.finditer(page_text):
            sev = severity_if_proof if proof_on_page else severity_if_no_proof
            findings.append(Finding(
                category=category,
                severity=sev,
                message=message,
                how_to_fix=how_to_fix,
                evidence=snippet(page_text, m.start(), m.end()),
                url=page_url
            ))

    # Vague green claims
    add_many(
        VAGUE_GREEN,
        category="Algemene milieuclaim (vaag)",
        severity_if_no_proof="high",
        severity_if_proof="medium",
        message="Mogelijk vage algemene milieuclaim gedetecteerd.",
        how_to_fix="Check of de claim concreet en onderbouwd is (wat precies, welke scope, bewijs/rapport, datum, methode)."
    )

    # Absolute claims
    add_many(
        ABSOLUTE_CLAIMS,
        category="Absolute milieuclaim",
        severity_if_no_proof="high",
        severity_if_proof="high",
        message="Absolute claim gedetecteerd (bv. 100%, zero impact).",
        how_to_fix="Absolute claims zijn risicovol: zorg voor strikte afbakening, aantoonbaar bewijs en vermijd overstatement."
    )

    # Climate neutrality
    add_many(
        CLIMATE_NEUTRAL,
        category="Klimaat-/CO₂-neutraal of Net Zero",
        severity_if_no_proof="high",
        severity_if_proof="high",
        message="Klimaat-/CO₂-neutraal of Net Zero claim gedetecteerd.",
        how_to_fix="Check: reductieplan, scope 1-3 dekking, meetmethode, basisjaar, onafhankelijke verificatie en transparantie rond compensatie."
    )

    # Offsets / compensation
    add_many(
        OFFSETS,
        category="Compensatie/offsetting",
        severity_if_no_proof="high",
        severity_if_proof="high",
        message="Compensatie/offsetting-gerelateerde tekst gedetecteerd.",
        how_to_fix="Check: is compensatie duidelijk als aanvullend (na reductie), welke credits, kwaliteit, registry, en claimtaal (geen 'neutral' zonder context)."
    )

    # Comparative claims
    add_many(
        COMPARISONS,
        category="Vergelijkende milieuclaim",
        severity_if_no_proof="high",
        severity_if_proof="medium",
        message="Vergelijkende claim gedetecteerd (beter/meer duurzaam/% minder).",
        how_to_fix="Check: vergelijkingsbasis, methode, referentieproduct, assumpties, scope en datumbereik moeten duidelijk zijn."
    )

    # Future claims
    add_many(
        FUTURE,
        category="Toekomstige prestatie-/doelclaim",
        severity_if_no_proof="high",
        severity_if_proof="medium",
        message="Toekomstige doelstelling/claim gedetecteerd (bv. tegen 2030).",
        how_to_fix="Check: concreet plan, milestones, governance, budget, meetmethode, scope en voortgangsrapportage."
    )

    # Labels/certifications heuristic:
    # If label terms appear but no proof hints, flag medium (needs transparency)
    label_pat = re.compile("|".join(LABEL_TERMS), re.IGNORECASE)
    for m in label_pat.finditer(page_text):
        findings.append(Finding(
            category="Label/keurmerk (heuristiek)",
            severity="medium" if proof_on_page else "high",
            message="Mogelijk duurzaamheidslabel/certificering genoemd. Controleer transparantie.",
            how_to_fix="Check: wie certificeert, criteriaset, scope, geldigheid, link naar certificaat/registry en onafhankelijke controle.",
            evidence=snippet(page_text, m.start(), m.end()),
            url=page_url
        ))

    return findings


def compute_risk_score(findings: List[Finding]) -> int:
    # Score based on unique findings only (after dedupe), weighted by severity
    weights = {"low": 6, "medium": 14, "high": 26}
    total = 0
    for f in findings:
        total += weights.get(f.severity, 6)
    return min(100, int(total))


def dedupe_findings(findings: List[Finding], limit: int = 25) -> List[Finding]:
    seen: Set[str] = set()
    out: List[Finding] = []
    for f in findings:
        k = dedupe_key(f)
        if k in seen:
            continue
        seen.add(k)
        out.append(f)

    # Sort: high first, then medium, then low; keep stable
    sev_rank = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda x: (sev_rank.get(x.severity, 9), x.category))
    return out[:limit]


# ----------------------------
# Crawler (same-domain, lightweight)
# ----------------------------
def crawl(seed_url: str, max_pages: int = 12) -> Tuple[int, List[Finding]]:
    seed_url = normalize_url(seed_url)
    if not seed_url:
        return 0, []

    visited: Set[str] = set()
    q = deque([seed_url])

    pages_scanned = 0
    findings: List[Finding] = []

    while q and pages_scanned < max_pages:
        url = q.popleft()
        if url in visited:
            continue
        visited.add(url)

        html = safe_get(url)
        if not html:
            continue

        pages_scanned += 1
        text, links = extract_text_links(url, html)

        # run rules
        findings.extend(run_rules(url, text))

        # enqueue same-host links
        for nxt in links:
            if nxt in visited:
                continue
            if same_host(seed_url, nxt):
                q.append(nxt)

    # Dedupe and cap findings for report readability
    findings = dedupe_findings(findings, limit=30)
    return pages_scanned, findings


# ----------------------------
# Routes
# ----------------------------
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "disclaimer": DISCLAIMER,
    })


@app.post("/scan", response_class=HTMLResponse)
def scan(
    request: Request,
    url: str = Form(...),
    max_pages: int = Form(12),
):
    url = normalize_url(url)
    try:
        max_pages = int(max_pages)
    except Exception:
        max_pages = 12

    max_pages = max(1, min(max_pages, 30))

    pages_scanned, findings = crawl(url, max_pages=max_pages)
    risk = compute_risk_score(findings)

    return templates.TemplateResponse("report.html", {
        "request": request,
        "input_url": url,
        "pages_scanned": pages_scanned,
        "risk_score": risk,
        "findings": [asdict(f) for f in findings],
    })
