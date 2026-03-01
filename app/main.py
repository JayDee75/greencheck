from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass
from typing import List, Dict, Set, Tuple, Optional
from urllib.parse import urlparse, urljoin, urldefrag

import requests
from bs4 import BeautifulSoup

from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


# ---------------------------
# App setup (branding via templates + static)
# ---------------------------
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---------------------------
# Claim patterns (focus on “risky wording”)
# Severity model:
#   RED    = absolute / net-zero / carbon-neutral / “100%” etc. without nearby proof
#   YELLOW = vague “sustainable future / greener / environmentally friendly / responsible” without specifics
#   LOW    = claim but with nearby evidence hints (report, methodology, standard, KPI, etc.)
# ---------------------------

# Evidence hints: if these appear near the claim, we downgrade to LOW
EVIDENCE_HINTS = [
    r"\b(gri|csrd|esrs|sasb|tcfd|tnfd|cdp)\b",
    r"\b(sbti|science(\s|-)?based\s*targets?)\b",
    r"\b(lca|life\s*cycle\s*assessment|levenscyclusanalyse)\b",
    r"\b(scope\s*[123]|ghg\s*protocol|greenhouse\s*gas)\b",
    r"\b(rapport|report|methodology|methodologie|assumptions|aannames)\b",
    r"\b(audit|verified|verification|assurance|third(\s|-)?party|derde\s*partij|independent|extern)\b",
    r"\b(kpi|metric|metrics|indicator|indicatoren|data|dataset|figures|cijfers)\b",
    r"\b(iso\s*14001|iso\s*14064|iso\s*14067)\b",
    r"\b(ecolabel|eu(\s|-)?ecolabel|fsc|pefc|b\s*corp|fairtrade)\b",
    r"\b(certificaat|certificate|certification)\b",
    r"\b(footprint|co2(\s|-)?footprint|carbon(\s|-)?footprint)\b",
    r"\b(policy|beleid|governance|code\s*of\s*conduct)\b",
    r"\b(pdf)\b",
]
EVIDENCE_REGEX = re.compile("|".join(EVIDENCE_HINTS), re.I)

# Future/target language increases risk if no proof nearby
FUTURE_TARGET_REGEX = re.compile(
    r"\b("
    r"by\s*20\d{2}|tegen\s*20\d{2}|in\s*20\d{2}|"
    r"target|doelstelling|ambitie|aim|plan|pledge|commit|commitment|"
    r"will|we\s+will|we\s+aim|we\s+plan|"
    r"roadmap|trajectory|pathway|"
    r"net(\s|-)?zero(\s+by)?"
    r")\b",
    re.I,
)

# “Absolute” words often problematic without substantiation
ABSOLUTE_WORDS_REGEX = re.compile(
    r"\b(100%|zero|volledig|completely|entirely|no\s+impact|without\s+impact|impact\s*free)\b",
    re.I,
)

# Core claim patterns (labels + regex)
# Keep this list long and “semantic”; we want to catch wording like GreenGuard.
GREEN_CLAIMS_PATTERNS: List[Tuple[str, re.Pattern]] = [
    # Very common vague green marketing
    ("Vage milieukwalificatie (eco/green)", re.compile(r"\b(milieuvriendelijk|eco(\s|-)?friendly|eco(\s|-)?vriendelijk|green(er)?|groen(e)?)\b", re.I)),
    ("Duurzaam / sustainability claim", re.compile(r"\b(duurzaam(heid)?|sustainab(le|ility)|sustainable\s+future|future\s+.*sustainab)\b", re.I)),
    ("Environment(ally) friendly claim", re.compile(r"\b(environmentally(\s|-)?friendly|planet(\s|-)?friendly|good\s+for\s+the\s+planet|better\s+for\s+the\s+planet)\b", re.I)),
    ("Responsible/ethical claim", re.compile(r"\b(responsible|verantwoord|ethical|ethisch|responsible\s+sourcing|duurzame\s+inkoop)\b", re.I)),
    ("ESG framing (marketing)", re.compile(r"\b(esg|environment(al)?\s*,?\s*social\s*(and|&)\s*governance)\b", re.I)),

    # Carbon/climate (high-risk)
    ("CO2-neutraal / carbon neutral", re.compile(r"\b(co2(\s|-)?neutraal|carbon(\s|-)?neutral|klimaat(\s|-)?neutraal|climate(\s|-)?neutral)\b", re.I)),
    ("Net zero claim/target", re.compile(r"\bnet(\s|-)?zero\b", re.I)),
    ("Emissievrij / zero emissions", re.compile(r"\b(emissie(\s|-)?vrij|zero(\s|-)?emissions?)\b", re.I)),
    ("Low carbon / reduced emissions", re.compile(r"\b(low(\s|-)?carbon|lage(\s|-)?uitstoot|reduced(\s|-)?emissions?|reduction\s+in\s+emissions?)\b", re.I)),
    ("CO2 compensatie / offsetting", re.compile(r"\b(compensat(ie|ies)|offset(s|ting)?|carbon(\s|-)?offset|climate(\s|-)?compensation)\b", re.I)),
    ("Climate positive", re.compile(r"\bclimate(\s|-)?positive\b", re.I)),

    # Energy
    ("100% hernieuwbaar / 100% renewable", re.compile(r"\b(100%\s*(hernieuwbaar|renewable)|volledig\s*(hernieuwbaar|renewable)|powered\s+by\s+100%\s+renewable)\b", re.I)),
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
    ("Regenerative", re.compile(r"\b(regeneratief|regenerative)\b", re.I)),

    # Nature / biodiversity
    ("Nature positive / natuurpositief", re.compile(r"\b(natuurvriendelijk|nature(\s|-)?positive|biodiversity|biodiversiteit)\b", re.I)),
    ("Ontbossingsvrij / deforestation-free", re.compile(r"\b(ontbossings(\s|-)?vrij|deforestation(\s|-)?free)\b", re.I)),

    # Chemicals/materials
    ("Niet-toxisch / non-toxic", re.compile(r"\b(niet(\s|-)?toxisch|non(\s|-)?toxic)\b", re.I)),
    ("Vrij van schadelijke stoffen", re.compile(r"\b(vrij(\s|-)?van(\s|-)?schadelijke(\s|-)?stoffen|free(\s|-)?from(\s|-)?harmful(\s|-)?chemicals?)\b", re.I)),
    ("PFAS-vrij", re.compile(r"\bpfas(\s|-)?vrij\b", re.I)),

    # Certifications/labels often misused if not precise
    ("Gecertificeerd / certified (algemeen)", re.compile(r"\b(gecertificeerd|certified|certification)\b", re.I)),
    ("EU Ecolabel", re.compile(r"\b(eu(\s|-)?ecolabel|european(\s|-)?ecolabel)\b", re.I)),
    ("FSC/PEFC/Fairtrade/B Corp", re.compile(r"\b(fsc|pefc|fair(\s|-)?trade|b(\s|-)?corp)\b", re.I)),
]


@dataclass
class Finding:
    category: str
    severity: str  # "red" | "yellow" | "low"
    url: str
    message: str
    how_to_fix: str
    evidence: str


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


def canonicalize_url(u: str) -> str:
    # remove fragment and trim
    u = (u or "").strip()
    if not u:
        return u
    u, _frag = urldefrag(u)
    return u


def fetch_html(url: str, session: requests.Session, timeout: int = 20) -> Optional[str]:
    headers = {
        "User-Agent": "DurablyGreenCheck/1.2 (+https://durably.eu) requests",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,nl;q=0.8",
    }
    try:
        r = session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        if r.status_code >= 400:
            return None
        ct = (r.headers.get("content-type") or "").lower()
        # allow html + sometimes mislabelled responses
        if ("text/html" not in ct) and ("application/xhtml" not in ct) and ("text/plain" not in ct):
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
        absolute = urljoin(base_url, href)
        absolute = canonicalize_url(absolute)
        links.append(absolute)
    return links


def extract_text_blocks(html: str) -> List[str]:
    """
    Extract meaningful “blocks” (headings, paragraphs, list items, title/meta)
    Much better than sentence-splitting only.
    """
    soup = BeautifulSoup(html, "html.parser")

    # remove non-content
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    blocks: List[str] = []

    # title + meta description
    if soup.title and soup.title.string:
        blocks.append(soup.title.string.strip())

    for meta in soup.find_all("meta"):
        name = (meta.get("name") or "").lower()
        prop = (meta.get("property") or "").lower()
        if name in ("description", "og:description") or prop in ("og:description",):
            content = (meta.get("content") or "").strip()
            if content:
                blocks.append(content)

    # headings, paragraphs, li
    for el in soup.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
        txt = " ".join(el.stripped_strings)
        txt = re.sub(r"\s+", " ", txt).strip()
        if txt and len(txt) >= 20:
            blocks.append(txt)

    # dedupe while keeping order
    seen: Set[str] = set()
    out: List[str] = []
    for b in blocks:
        key = b.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
    return out


def build_snippet(text: str, max_len: int = 260) -> str:
    t = (text or "").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 1].rstrip() + "…"


def classify_severity(category: str, block: str, neighbor_text: str) -> Tuple[str, str]:
    """
    Decide red/yellow/low with heuristics.
    neighbor_text is block + nearby concatenated (context window).
    """
    has_evidence = bool(EVIDENCE_REGEX.search(neighbor_text))
    has_future = bool(FUTURE_TARGET_REGEX.search(block))
    has_absolute = bool(ABSOLUTE_WORDS_REGEX.search(block))

    # “Hard” high-risk categories
    high_risk_cat = any(k in category.lower() for k in ["net zero", "carbon", "co2", "emissie", "climate positive"])

    if has_evidence:
        return "low", "Claim met indicatie van onderbouwing (standaard/rapport/methodologie/KPI) in de nabije context."

    # RED if high-risk + no evidence, or absolute/future target wording without specifics
    if high_risk_cat and (has_future or has_absolute or True):
        return "red", "Hoog risico: klimaat/CO2/Net Zero claim of target zonder onderbouwing in dezelfde context."

    if has_future and not has_evidence:
        return "yellow", "Let op: future target/ambitie zonder directe scope, baseline, timing of verificatie in dezelfde context."

    if has_absolute and not has_evidence:
        return "yellow", "Let op: absolute bewoordingen zonder directe onderbouwing in dezelfde context."

    return "yellow", "Let op: vage duurzaamheidsclaim zonder directe onderbouwing (scope/meetmethode/bewijs) in dezelfde context."


def scan_site(start_url: str, max_pages: int = 10) -> Tuple[int, List[Finding]]:
    start_url = normalize_url(start_url)
    if not start_url:
        return 0, []

    session = requests.Session()

    visited: Set[str] = set()
    queue: deque[str] = deque([start_url])

    findings: List[Finding] = []

    while queue and len(visited) < max_pages:
        url = canonicalize_url(queue.popleft())
        if not url or url in visited:
            continue
        if not same_domain(start_url, url):
            continue

        html = fetch_html(url, session=session)
        visited.add(url)

        if not html:
            continue

        # crawl more links
        for link in extract_links(url, html):
            if link not in visited and same_domain(start_url, link):
                queue.append(link)

        blocks = extract_text_blocks(html)

        # context window: include neighbor blocks so evidence nearby counts
        for i, block in enumerate(blocks):
            neighbor = " ".join(
                blocks[max(0, i - 1): min(len(blocks), i + 2)]
            )

            for category, pattern in GREEN_CLAIMS_PATTERNS:
                if pattern.search(block):
                    severity, why = classify_severity(category, block, neighbor)

                    # Create “how_to_fix” guidance depending on severity/category
                    if severity == "red":
                        fix = "Voeg concrete onderbouwing toe: scope (wat omvat het), baseline, meetmethode (bv. GHG Protocol/LCA), periode, en onafhankelijke verificatie waar mogelijk."
                    elif severity == "yellow":
                        fix = "Maak de claim specifieker: definieer scope/criteria en verwijs naar rapport/KPI/methodologie. Vermijd vage termen zonder uitleg."
                    else:
                        fix = "Ter info: er lijkt onderbouwing in de buurt te staan. Controleer of die onderbouwing de claim echt dekt (scope, actualiteit, verificatie)."

                    findings.append(
                        Finding(
                            category=category,
                            severity=severity,
                            url=url,
                            message=build_snippet(block),
                            how_to_fix=fix,
                            evidence=f"Context: {build_snippet(neighbor)}\nWaarom: {why}",
                        )
                    )

        time.sleep(0.12)

    # Dedupe findings (same url + same message)
    uniq: Dict[Tuple[str, str], Finding] = {}
    for f in findings:
        key = (f.url, f.message)
        if key not in uniq:
            uniq[key] = f

    return len(visited), list(uniq.values())


def calc_risk_score(findings: List[Finding]) -> int:
    red = sum(1 for f in findings if f.severity == "red")
    yellow = sum(1 for f in findings if f.severity == "yellow")
    low = sum(1 for f in findings if f.severity == "low")

    # Weighting similar to “interestingness”
    score = red * 18 + yellow * 7 + low * 1
    return max(0, min(100, score))


# ---------------------------
# Routes (branding preserved via templates)
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

    # Sort: RED first, then YELLOW, then LOW
    order = {"red": 0, "yellow": 1, "low": 2}
    findings.sort(key=lambda f: (order.get(f.severity, 9), f.url))

    # Optional: hide LOW by default (you can flip this to True if you want)
    SHOW_LOWS = False
    if not SHOW_LOWS:
        findings = [f for f in findings if f.severity in ("red", "yellow")]

    risk = calc_risk_score(findings)

    # IMPORTANT: match template/report.html variables exactly
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
