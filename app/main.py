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


# ============================================================
# GreenCheck (EmpCo / EU 2024/825) — CRITICAL MODE (B)
# Focus: potential infringements (RED/HIGH + YELLOW/MEDIUM)
# Branding is preserved via templates + static.
# ============================================================

# ---------------------------
# App setup (branding via templates + static)
# ---------------------------
app = FastAPI()

# Serve /static/style.css and your logo svg
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")


# ---------------------------
# EmpCo-focused claim types (NL + EN + variants)
# We scan "sentences-ish" and classify as HIGH (RED) or MEDIUM (YELLOW).
# We do NOT generate LOW by default (because you don't care about "good").
# ---------------------------

# Strong evidence markers (relevant for targets/absolute claims)
TARGET_EVIDENCE = re.compile(
    r"\b("
    r"baseline|base\s*year|referentiejaar|basisjaar|"
    r"scope\s*[123]|scope\s*1\s*/\s*2\s*/\s*3|"
    r"ghg\s*protocol|greenhouse\s+gas\s+protocol|"
    r"sbti|science\s*based\s+targets|"
    r"transition\s*plan|implementation\s*plan|roadmap|plan\s+van\s+aanpak|"
    r"assurance|verified|verifieer(d)?|third(\s|-)?party|derde\s*partij|independent|onafhankelijk"
    r")\b",
    re.I,
)

# Weak words that should NOT downgrade severity by themselves
WEAK_CONTEXT = re.compile(r"\b(report|rapport|policy|beleid|esg|sustainability\s+report)\b", re.I)

r"sustainable\s+future|"
r"sustainable\s+hr|"
r"sustainable\s+work|"
r"sustainable\s+growth|"
r"future\s+for\s+(employees|customers|society)|"
# Claim patterns -> (type_key, label, pattern)
CLAIM_TYPES: List[Tuple[str, str, re.Pattern]] = [
    
    # 1) FUTURE TARGETS (usually RED): reduction target with % and year and emissions context
    (
        "FUTURE_TARGET",
        "Future climate/emissions target (e.g., -55% by 2030)",
        re.compile(
            r"\b("
            r"reduce|cut|decrease|lower|halve|shrink|drop|bring\s+down|"
            r"verminder|verminderen|verlagen|verlaag|reduceren|terugdringen|afbouwen|dalen|"
            r"aim|target|commit|pledge|ambition|goal|"
            r"doel|doelstelling|ambitie|streven|commitment|engagement"
            r")\b"
            r".{0,120}?\b(\d{1,3}\s*%|\d{1,3}\s*percent)\b"
            r".{0,120}?\b(by|in|tegen|voor)\s*(20\d{2})\b"
            r".{0,220}?\b("
            r"emissions?|co2|carbon|ghg|greenhouse\s+gas|uitstoot|emissies|broeikasgassen"
            r")\b",
            re.I | re.S,
        ),
    ),
    # 2) ABSOLUTE CLAIMS (often RED): carbon neutral / net zero / zero emissions / 100% renewable
    (
        "ABSOLUTE",
        "Absolute claim (carbon neutral / net zero / zero emissions / 100% renewable)",
        re.compile(
            r"\b("
            r"co2(\s|-)?neutraal|carbon(\s|-)?neutral|klimaat(\s|-)?neutraal|climate(\s|-)?neutral|"
            r"net(\s|-)?zero|"
            r"zero(\s|-)?emissions?|emissie(\s|-)?vrij|emissievrij|"
            r"100%\s*(renewable|hernieuwbaar)|volledig\s*(renewable|hernieuwbaar)|"
            r"zero(\s|-)?carbon|carbon(\s|-)?free"
            r")\b",
            re.I,
        ),
    ),
    # 3) OFFSETTING / COMPENSATION (usually MEDIUM, can be RED with absolutes)
    (
        "OFFSET",
        "Offsetting / compensation claim",
        re.compile(
            r"\b("
            r"offset(s|ting)?|carbon(\s|-)?offset|compensat(ie|ies)|"
            r"klimaatcompensatie|co2(\s|-)?compensatie|"
            r"carbon\s+credits?|credits?\s+purchase|"
            r"vergroen(en)?\s+via\s+compensatie"
            r")\b",
            re.I,
        ),
    ),
    # 4) GENERIC / VAGUE ENVIRONMENTAL CLAIMS (usually YELLOW)
    (
        "VAGUE",
        "Vague environmental claim (green / sustainable / eco-friendly)",
        re.compile(
            r"\b("
            r"milieuvriendelijk|eco(\s|-)?friendly|eco(\s|-)?vriendelijk|"
            r"duurzaam(heid)?|sustainab(le|ility)|"
            r"groen(e)?|green|environmentally(\s|-)?friendly|planet(\s|-)?friendly|"
            r"nature(\s|-)?positive|natuurvriendelijk|"
            r"responsible|verantwoord|conscious|bewust|"
            r"better\s+for\s+the\s+planet|better\s+for\s+the\s+environment"
            r")\b",
            re.I,
        ),
    ),
    # 5) CIRCULARITY / WASTE CLAIMS (usually YELLOW; can be RED if absolute)
    (
        "CIRCULAR",
        "Circularity/waste claim (recyclable / recycled / plastic-free / zero waste)",
        re.compile(
            r"\b("
            r"recycle(er)?baar|recyclable|"
            r"gerecycl(ed|de)?|recycled(\s|-)?content|"
            r"plastic(\s|-)?vrij|plastic(\s|-)?free|"
            r"zero(\s|-)?waste|afval(\s|-)?vrij|"
            r"circulair|circular(ity)?|"
            r"biologisch(\s|-)?afbreekbaar|biodegradab(le|ility)|"
            r"composteerbaar|compostable"
            r")\b",
            re.I,
        ),
    ),
    # 6) CERTIFICATION / LABEL REFERENCES (usually YELLOW; can be RED if vague "certified" with no specifics)
    (
        "CERT",
        "Certification/label reference",
        re.compile(
            r"\b("
            r"gecertificeerd|certified|certification|"
            r"iso(\s|-)?14001|iso(\s|-)?14064|iso(\s|-)?14067|"
            r"b(\s|-)?corp|fsc|pefc|eu(\s|-)?ecolabel|fair(\s|-)?trade|"
            r"energy\s+star|cradle\s+to\s+cradle|c2c|"
            r"gots|oekotex|oeko(\s|-)?tex|bluesign"
            r")\b",
            re.I,
        ),
    ),
]


@dataclass
class Finding:
    category: str      # For your existing report.html (expects f.category)
    url: str           # For your existing report.html (expects f.url)
    message: str       # For your existing report.html (expects f.message)
    severity: str      # "high" or "medium"
    how_to_fix: str    # For your existing report.html (expects f.how_to_fix)
    evidence: str      # For your existing report.html (expects f.evidence)


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
        "User-Agent": "GreenCheckBot/1.2 (+https://durably.eu) requests",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        r = session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        if r.status_code >= 400:
            return None
        ct = (r.headers.get("content-type") or "").lower()
        # Some sites serve HTML with charset or unusual CT values; be tolerant
        if "text/html" not in ct and "application/xhtml" not in ct:
            # Sometimes servers forget content-type; still attempt if it looks like HTML
            if "<html" not in (r.text or "").lower():
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


def page_text_and_sentences(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator=" ")
    text = re.sub(r"\s+", " ", text).strip()

    # Split into "sentences-ish"
    parts = re.split(r"(?<=[\.\!\?])\s+|[\n\r]+", text)
    # Keep reasonably long segments so we have context
    parts = [p.strip() for p in parts if p and len(p.strip()) >= 35]
    return parts


def build_snippet(sentence: str, max_len: int = 380) -> str:
    s = sentence.strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1].rstrip() + "…"


def classify_claim(claim_type: str, sentence: str) -> Tuple[str, str, str]:
    """
    EmpCo-critical severity:
    - HIGH (RED): forward-looking targets with % + year OR absolute climate claims without strong evidence nearby
    - MEDIUM (YELLOW): vague green claims, certification refs, circularity, offsets (unless combined with absolute)
    Evidence only reduces HIGH -> MEDIUM if it includes strong markers (baseline/scope/GHG/SBTi/plan/verification).
    """
    s = sentence

    # Helper: strong evidence present?
    has_strong_evidence = bool(TARGET_EVIDENCE.search(s))
    has_only_weak_context = bool(WEAK_CONTEXT.search(s)) and not has_strong_evidence

    if claim_type == "FUTURE_TARGET":
        if has_strong_evidence:
            return (
                "medium",
                "YELLOW",
                "Forward-looking target gevonden; er is (enige) onderbouwing genoemd. Check volledigheid: baseline, scopes (1/2/3), plan/roadmap en onafhankelijke verificatie.",
            )
        # weak words like "report" shouldn't help
        if has_only_weak_context:
            pass
        return (
            "high",
            "RED",
            "FUTURE_TARGET: doel met % + jaartal over emissies/CO2 zonder duidelijke baseline/scope/plan/verificatie in dezelfde context.",
        )

    if claim_type == "ABSOLUTE":
        # If absolute claim + strong evidence nearby => medium, else high
        if has_strong_evidence:
            return (
                "medium",
                "YELLOW",
                "Absolute claim gevonden; er is onderbouwing genoemd. Check scope (wat valt eronder), methodologie (GHG/SBTi) en verificatie.",
            )
        return (
            "high",
            "RED",
            "ABSOLUTE_CLAIM: absolute klimaat-/milieuclaim zonder directe scope/method/verificatie in dezelfde context.",
        )

    if claim_type == "OFFSET":
        # Offsetting is often sensitive; if combined with absolute => high
        if re.search(r"\b(co2(\s|-)?neutraal|carbon(\s|-)?neutral|net(\s|-)?zero|zero(\s|-)?emissions?)\b", s, re.I):
            return (
                "high",
                "RED",
                "OFFSET+ABSOLUTE: compensatie/offsetting gecombineerd met absolute claim. Check transparantie: wat wordt gecompenseerd, criteria credits, additionality, scope en verificatie.",
            )
        return (
            "medium",
            "YELLOW",
            "OFFSETTING: claim rond compensatie/offsetting. Check details (scope, project/credits, additionality, verificatie) en vermijd dat het als 'neutral' wordt gepresenteerd zonder onderbouwing.",
        )

    if claim_type == "CERT":
        # "certified" without specific label/standard can be high
        if re.search(r"\b(gecertificeerd|certified)\b", s, re.I) and not re.search(
            r"\b(iso|fsc|pefc|ecolabel|fairtrade|b(\s|-)?corp|energy\s+star|cradle\s+to\s+cradle|c2c|gots|oeko(\s|-)?tex|bluesign)\b",
            s,
            re.I,
        ):
            return (
                "high",
                "RED",
                "CERTIFICATION: er wordt 'gecertificeerd/certified' gezegd zonder duidelijke standaard/label of scope. Risico op misleiding.",
            )
        return (
            "medium",
            "YELLOW",
            "CERTIFICATION/LABEL: verwijzing naar label/certificaat. Check of het correct is (scope, licentie/cert-ID, link naar bewijs) en niet te breed wordt voorgesteld.",
        )

    if claim_type in ("VAGUE", "CIRCULAR"):
        # These are usually medium; can be high if absolute language is used
        if re.search(r"\b(100%|altijd|volledig|compleet|zero(\s|-)?waste)\b", s, re.I):
            return (
                "high",
                "RED",
                "VAGE/ABSOLUTE FORMULERING: claim met absolute termen (100%/volledig/zero). Check definities, scope en bewijs.",
            )
        return (
            "medium",
            "YELLOW",
            "VAGE MILIEUCLAIM: algemene 'groen/duurzaam/eco' of circulariteitsclaim. EmpCo vereist duidelijke betekenis + verifieerbare onderbouwing.",
        )

    # fallback
    return "medium", "YELLOW", "Potentiële milieugerelateerde claim; controleer onderbouwing en scope."


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

        # Crawl more links
        for link in extract_links(url, html):
            if link not in visited and same_domain(start_url, link):
                queue.append(link)

        # Analyze
        sentences = page_text_and_sentences(html)

        for sentence in sentences:
            for claim_type, label, pattern in CLAIM_TYPES:
                if pattern.search(sentence):
                    sev, sev_badge, notes = classify_claim(claim_type, sentence)

                    # Build report fields compatible with your current templates/report.html
                    how_to_fix = ""
                    if claim_type == "FUTURE_TARGET":
                        how_to_fix = (
                            "Check of er een publiek en verifieerbaar implementatie-/transitieplan staat: "
                            "baseline jaar, scopes (1/2/3), tussentijdse mijlpalen, maatregelen, governance, "
                            "en onafhankelijke verificatie (bv. assurance/SBTi)."
                        )
                    elif claim_type == "ABSOLUTE":
                        how_to_fix = (
                            "Check scope en definities (wat valt onder 'carbon neutral/net zero'), methodologie "
                            "(GHG Protocol), grenzen (organisational boundary) en verificatie/assurance."
                        )
                    elif claim_type == "OFFSET":
                        how_to_fix = (
                            "Check transparantie rond carbon credits/offsets: scope, type credits, additionality, "
                            "permanence/leakage, verificatie, en vermijd 'neutral' framing zonder harde onderbouwing."
                        )
                    elif claim_type == "CERT":
                        how_to_fix = (
                            "Check of certificaat/label correct en specifiek is: standaard, scope, licentie/cert-ID "
                            "en link naar bewijs. Vermijd te brede interpretaties."
                        )
                    else:
                        how_to_fix = (
                            "Maak de claim concreet: wat betekent het exact, voor welk onderdeel, en welke meetbare "
                            "data/bewijs is publiek beschikbaar."
                        )

                    findings.append(
                        Finding(
                            category=f"{label}",
                            url=url,
                            message=f"{sev_badge}: {notes}",
                            severity=sev,  # 'high' or 'medium'
                            how_to_fix=how_to_fix,
                            evidence=build_snippet(sentence),
                        )
                    )

        # polite delay
        time.sleep(0.12)

    # De-duplicate: keep first occurrence per (category + evidence)
    dedup: List[Finding] = []
    seen: Set[Tuple[str, str]] = set()
    for f in findings:
        key = (f.category, f.evidence)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(f)

    return len(visited), dedup


def calc_risk_score(findings: List[Finding]) -> int:
    """
    Risk score (0-100) based ONLY on HIGH/MEDIUM.
    HIGH weighs much heavier. If no findings => 0.
    """
    high = sum(1 for f in findings if f.severity == "high")
    med = sum(1 for f in findings if f.severity == "medium")

    score = high * 20 + med * 8
    return max(0, min(100, score))


# ---------------------------
# Routes (keep branding!)
# IMPORTANT: We keep using your existing templates/index.html and templates/report.html
# The template expects: input_url, pages_scanned, risk_score, findings
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

    max_pages_int = max(1, min(50, max_pages_int))  # safety cap

    if not target:
        return RedirectResponse(url="/", status_code=303)

    pages_scanned, findings = scan_site(target, max_pages=max_pages_int)
    risk = calc_risk_score(findings)

    # Sort findings: high first, then medium
    findings.sort(key=lambda f: (0 if f.severity == "high" else 1, f.category, f.url))

    context = {
        "request": request,
        "input_url": target,
        "pages_scanned": pages_scanned,
        "risk_score": risk,
        "findings": findings,
    }

    return templates.TemplateResponse("report.html", context)


@app.get("/health")
async def health():
    return {"ok": True}
