from __future__ import annotations

import re
import time
import json
from collections import deque
from dataclasses import dataclass
from typing import List, Set, Tuple, Optional

import requests
from bs4 import BeautifulSoup

from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from urllib.parse import urlparse, urljoin


# ---------------------------
# App setup
# ---------------------------
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---------------------------
# EmpCo-focused rules (HIGH / MEDIUM)
# ---------------------------

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

FUTURE_TARGET = re.compile(
    r"(?is)\b("
    r"reduce|cut|lower|decrease|bring\s*down|"
    r"aim\s*to|target|commit|pledge|plan\s*to|will|shall"
    r")\b.{0,140}?\b("
    r"emissions?|greenhouse\s*gas|ghg|co2|co2e|carbon"
    r")\b.{0,160}?\b("
    r"\d{1,3}\s*(%|percent)"
    r")\b.{0,140}?\b("
    r"by|before|in"
    r")\s*(20\d{2})\b"
)

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
    severity: str  # "high" or "medium" (optioneel "low")
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


def _walk_json_collect_strings(obj, out: List[str], min_len: int = 20) -> None:
    if obj is None:
        return
    if isinstance(obj, str):
        s = obj.strip()
        if len(s) >= min_len:
            out.append(s)
        return
    if isinstance(obj, list):
        for x in obj:
            _walk_json_collect_strings(x, out, min_len=min_len)
        return
    if isinstance(obj, dict):
        for v in obj.values():
            _walk_json_collect_strings(v, out, min_len=min_len)
        return


def html_to_text(html: str) -> str:
    """
    Extract visible text + additional text from:
    - Next.js __NEXT_DATA__ (often where real content lives)
    - JSON-LD blocks
    This helps a lot on modern websites where HTML is a shell.
    """
    # Visible text
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    visible_text = soup.get_text(separator="\n")
    visible_text = re.sub(r"[ \t\r\f\v]+", " ", visible_text)
    visible_text = re.sub(r"\n{2,}", "\n", visible_text).strip()

    next_texts: List[str] = []
    ld_texts: List[str] = []

    # Next.js payload
    try:
        soup2 = BeautifulSoup(html, "html.parser")
        node = soup2.find("script", id="__NEXT_DATA__")
        if node and node.string:
            data = json.loads(node.string)
            _walk_json_collect_strings(data, next_texts, min_len=20)
    except Exception:
        pass

    # JSON-LD blocks
    try:
        soup3 = BeautifulSoup(html, "html.parser")
        for node in soup3.find_all("script", attrs={"type": "application/ld+json"}):
            if not node.string:
                continue
            try:
                data = json.loads(node.string)
                _walk_json_collect_strings(data, ld_texts, min_len=20)
            except Exception:
                continue
    except Exception:
        pass

    extra = "\n".join(next_texts + ld_texts).strip()
    merged = visible_text

    # If HTML is a thin shell, add more from JSON
    if len(merged) < 800 and extra:
        merged = merged + "\n" + extra
    elif extra:
        merged = merged + "\n" + extra[:5000]

    merged = re.sub(r"\n{2,}", "\n", merged).strip()
    return merged


def make_chunks(text: str) -> List[str]:
    """
    ESG-friendly chunking:
    - keeps short bullet lines (targets are often short, e.g. "55% by 2030")
    - creates sliding windows across full text so multi-line targets match
    """
    if not text:
        return []

    t = (
        text.replace("•", "\n• ")
            .replace("·", "\n· ")
            .replace("–", "-")
            .replace("—", "-")
    )

    parts: List[str] = []
    for line in t.split("\n"):
        line = line.strip()
        if not line:
            continue

        sub = re.split(r"(?<=[\.\!\?])\s+|;\s+|\|\s+| - ", line)
        for s in sub:
            s = s.strip()
            if not s:
                continue
            if len(s) >= 8:
                parts.append(s)

    full = re.sub(r"\s+", " ", t).strip()
    windows: List[str] = []
    step = 250
    win = 900
    for i in range(0, len(full), step):
        w = full[i:i + win].strip()
        if w:
            windows.append(w)

    return parts + windows


def clip(s: str, n: int = 280) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"


def page_has_plan(text: str) -> bool:
    return bool(PLAN_KEYWORDS.search(text or ""))


# ---------------------------
# Rule evaluation
# ---------------------------
 
  def find_issues_on_page(page_url: str, text: str) -> List[Finding]:
    issues: List[Finding] = []
    has_plan = page_has_plan(text)
    chunks = make_chunks(text)

    # Heuristics to avoid over-flagging nav/headings
    HEADING_LIKE = re.compile(
        r"^(our|about|esg|environment|social|governance|sustainability|ambitions?|strategy|policy|report|reports?)\b",
        re.I,
    )
    CLAIM_VERB = re.compile(
        r"\b(aim|target|commit|pledge|will|shall|plan|reduce|cut|lower|decrease|improve|support|work\s+towards)\b",
        re.I,
    )

    def is_noise(ch: str) -> bool:
        c = (ch or "").strip()
        if len(c) < 35:
            return True
        # Pure headings / titles (no verbs)
        if HEADING_LIKE.match(c) and not CLAIM_VERB.search(c):
            return True
        # Too “menu-ish”
        if c.count(" | ") >= 2:
            return True
        return False

    # Per-page caps (Greenguard-style: few representative issues)
    cap = {
        "FUTURE_TARGET": 1,
        "ABSOLUTE_CLAIM": 1,
        "GENERIC_ENVIRONMENTAL_CLAIM": 2,
        "ENVIRONMENTAL_FRAMING": 2,
    }
    count = {k: 0 for k in cap.keys()}

    # Dedup within page
    seen: Set[Tuple[str, str]] = set()  # (category, normalized_claim)

    def add_issue(category: str, severity: str, message: str, evidence: str, how_to_fix: str):
        nonlocal issues
        if category in cap and count[category] >= cap[category]:
            return

        ev = clip(evidence, 280)
        norm = re.sub(r"\s+", " ", ev.lower()).strip()
        key = (category, norm)
        if key in seen:
            return
        seen.add(key)

        issues.append(
            Finding(
                category=category,
                url=page_url,
                message=message,
                evidence=ev,
                severity=severity,
                how_to_fix=how_to_fix,
            )
        )
        if category in count:
            count[category] += 1

    # 1) FUTURE TARGET (High/Medium)
    for ch in chunks:
        if is_noise(ch):
            continue
        m = FUTURE_TARGET.search(ch)
        if not m:
            continue

        pct = m.group(3)
        year = m.group(6)

        if has_plan:
            sev = "medium"
            msg = f"Future target mentioned ({pct} by {year}) — check if plan details are concrete and verifiable."
        else:
            sev = "high"
            msg = f"Future climate/emissions target ({pct} by {year}) without clear implementation plan context."

        add_issue(
            category="FUTURE_TARGET",
            severity=sev,
            message=msg,
            evidence=ch,
            how_to_fix=(
                "Voeg baseline year, scope 1/2/3, methode/standaard, interim targets en bewijs/rapportage toe (liefst assurance)."
            ),
        )

    # 2) ABSOLUTE CLAIMS (High/Medium)
    for ch in chunks:
        if is_noise(ch):
            continue
        if not ABSOLUTE_CLAIMS.search(ch):
            continue

        if has_plan:
            sev = "medium"
            msg = "Absolute climate claim (net zero/carbon neutral/etc.) — verify scope and substantiation."
        else:
            sev = "high"
            msg = "Absolute climate claim (net zero/carbon neutral/etc.) without nearby substantiation."

        add_issue(
            category="ABSOLUTE_CLAIM",
            severity=sev,
            message=msg,
            evidence=ch,
            how_to_fix=(
                "Maak scope (1/2/3), boundary, methode, baselines en offsets (indien gebruikt) expliciet + link bewijs/assurance."
            ),
        )

    # 3) VAGUE CLAIMS — ONLY if performance-related
for ch in chunks:
    if is_heading_like(ch):
        continue

    # must contain performance language
    if not re.search(r"\b(reduce|cut|lower|decrease|improve|increase|achieve|deliver|become|reach|will|shall|target|commit)\b", ch, re.I):
        continue

    if not VAGUE_ENV_CLAIMS.search(ch):
        continue

    add(
        "GENERIC_ENVIRONMENTAL_CLAIM",
        "medium",
        "Environmental performance claim without clear measurable substantiation.",
        ch,
        "Maak de claim meetbaar: voeg baseline, scope, KPI’s, meetperiode en bewijslink toe.",
    )

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

        # Enqueue more links
        for link in extract_links(url, html):
            if link not in visited and same_domain(start_url, link):
                queue.append(link)

        text = html_to_text(html)
        findings.extend(find_issues_on_page(url, text))

        time.sleep(0.12)

    # Deduplicate
    uniq = {}
    for f in findings:
        key = (f.url, f.category, f.message, (f.evidence or "")[:120])
        uniq[key] = f
    findings = list(uniq.values())

    # Sort: high first then medium
    findings.sort(key=lambda x: (0 if x.severity == "high" else 1, x.url, x.category))
    return len(visited), findings


def calc_risk_score(findings: List[Finding]) -> int:
    high = sum(1 for f in findings if f.severity == "high")
    med = sum(1 for f in findings if f.severity == "medium")
    score = high * 25 + med * 10
    return max(0, min(100, score))


# ---------------------------
# Routes
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

    # Old schema (optional compatibility)
    findings = [
        {
            "category": f.category,
            "url": f.url,
            "message": f.message,
            "how_to_fix": f.how_to_fix,
            "evidence": f.evidence,
            "severity": f.severity,
        }
        for f in findings_obj
    ]

    # report.html schema
    def to_template_finding(f: Finding) -> dict:
        label = f.category.replace("_", " ").title()
        notes = f.message
        if f.how_to_fix:
            notes = f"{notes} | Fix: {clip(f.how_to_fix, 160)}"
        return {
            "label": label,
            "snippet": f.evidence,
            "page_url": f.url,
            "notes": notes,
        }

    findings_high = [to_template_finding(f) for f in findings_obj if f.severity == "high"]
    findings_medium = [to_template_finding(f) for f in findings_obj if f.severity == "medium"]
    findings_low = [to_template_finding(f) for f in findings_obj if f.severity == "low"]

    context = {
        "request": request,

        # Provide both to avoid empty URL field across template versions
        "target_url": target,
        "input_url": target,

        "pages_scanned": pages_scanned,
        "risk_score": risk,

        # Provide both schemas
        "findings": findings,
        "findings_high": findings_high,
        "findings_medium": findings_medium,
        "findings_low": findings_low,
    }

    return templates.TemplateResponse("report.html", context)


@app.get("/health")
async def health():
    return {"ok": True}
