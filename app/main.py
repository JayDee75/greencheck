from __future__ import annotations

import json
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


CLIMATE_CONTEXT = re.compile(
    r"\b(emissions?|ghg|greenhouse\s*gas|co2|co2e|carbon|climate|net\s*zero|decarboni[sz]ation)\b",
    re.I,
)

PLAN_SUBSTANTIATION = re.compile(
    r"\b(baseline\s*year|base\s*year|basisjaar|referentiejaar|scope\s*1|scope\s*2|scope\s*3|"
    r"interim\s*target|milestone|roadmap|transition\s*plan|actieplan|capex|opex|investment|"
    r"verified|assurance|audit|methodology|methodologie|ghg\s*protocol|sbti|science[-\s]?based)\b",
    re.I,
)

MATERIAL_TARGET = re.compile(
    r"(?is)\b(aim\s*to|target|commit|pledge|plan\s*to|will|shall|reduce|cut|lower|decrease)\b"
    r".{0,180}?\b(emissions?|ghg|greenhouse\s*gas|co2|co2e|carbon)\b"
    r".{0,160}?(\d{1,3}\s*(%|percent))"
    r".{0,140}?\b(by|before|in)\s*(20\d{2})\b"
)

ABSOLUTE_CLAIMS = re.compile(
    r"\b(net\s*zero|carbon\s*neutral|climate\s*neutral|co2\s*-?\s*neutral|"
    r"zero\s*emissions?|emissie\s*-?\s*vrij|fully\s*decarboni[sz]ed)\b",
    re.I,
)

OFFSET_HINT = re.compile(r"\b(offset|compensat|certificate|credit)\b", re.I)

NON_MATERIAL_URL_HINT = re.compile(r"/(news|blog|events|careers|jobs|investors?)/", re.I)


@dataclass
class Finding:
    category: str
    url: str
    message: str
    evidence: str
    severity: str
    how_to_fix: str


RULEBOOK = {
    "MATERIAL_TARGET_CLAIM": "EmpCo Art. 5/6 — Een materiële klimaatdoelclaim moet onderbouwd zijn met scope, baseline, methodologie en voortgangsinformatie.",
    "MATERIAL_ABSOLUTE_CLAIM": "EmpCo Art. 5 — Absolute claims (bv. net zero/carbon neutral) moeten duidelijk afgebakend en verifieerbaar zijn.",
}


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
        "User-Agent": "Durably-GreenCheck/2.0 (+https://durably.eu)",
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
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        candidate = urljoin(base_url, href).split("#")[0].strip()
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        links.append(candidate)
    return links


def is_readable_http_url(url: str) -> bool:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    if any(c.isspace() for c in url):
        return False
    return len(url) <= 220


def _walk_json_collect_strings(obj, out: List[str], min_len: int = 20) -> None:
    if obj is None:
        return
    if isinstance(obj, str):
        s = obj.strip()
        if len(s) >= min_len:
            out.append(s)
        return
    if isinstance(obj, list):
        for item in obj:
            _walk_json_collect_strings(item, out, min_len=min_len)
        return
    if isinstance(obj, dict):
        for value in obj.values():
            _walk_json_collect_strings(value, out, min_len=min_len)


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    visible_text = soup.get_text(separator="\n")
    visible_text = re.sub(r"[ \t\r\f\v]+", " ", visible_text)
    visible_text = re.sub(r"\n{2,}", "\n", visible_text).strip()

    extra: List[str] = []

    try:
        payload_node = BeautifulSoup(html, "html.parser").find("script", id="__NEXT_DATA__")
        if payload_node and payload_node.string:
            data = json.loads(payload_node.string)
            _walk_json_collect_strings(data, extra)
    except Exception:
        pass

    try:
        soup_ld = BeautifulSoup(html, "html.parser")
        for node in soup_ld.find_all("script", attrs={"type": "application/ld+json"}):
            if not node.string:
                continue
            try:
                _walk_json_collect_strings(json.loads(node.string), extra)
            except Exception:
                continue
    except Exception:
        pass

    merged = visible_text
    if extra:
        merged += "\n" + "\n".join(extra[:300])

    return re.sub(r"\n{2,}", "\n", merged).strip()


def make_chunks(text: str) -> List[str]:
    if not text:
        return []

    normalized = (
        text.replace("•", "\n• ")
        .replace("·", "\n· ")
        .replace("–", "-")
        .replace("—", "-")
    )

    lines: List[str] = []
    for line in normalized.split("\n"):
        line = line.strip()
        if not line:
            continue
        for part in re.split(r"(?<=[\.!?])\s+|;\s+|\|\s+", line):
            part = part.strip()
            if len(part) >= 25:
                lines.append(part)

    full = re.sub(r"\s+", " ", normalized).strip()
    windows = [full[i : i + 900] for i in range(0, len(full), 280) if full[i : i + 900].strip()]

    return lines + windows


def clip(s: str, n: int = 280) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"


def materiality_score(chunk: str, page_url: str) -> int:
    score = 0
    if CLIMATE_CONTEXT.search(chunk):
        score += 1
    if MATERIAL_TARGET.search(chunk):
        score += 2
    if ABSOLUTE_CLAIMS.search(chunk):
        score += 2
    if len(chunk) > 90:
        score += 1
    if NON_MATERIAL_URL_HINT.search(page_url):
        score -= 1
    return score


def find_issues_on_page(page_url: str, text: str) -> List[Finding]:
    chunks = make_chunks(text)
    if not chunks:
        return []

    has_substantiation = bool(PLAN_SUBSTANTIATION.search(text))
    issues: List[Finding] = []
    seen: Set[Tuple[str, str]] = set()

    def add_issue(category: str, severity: str, message: str, evidence: str, how_to_fix: str) -> None:
        normalized = re.sub(r"\s+", " ", evidence.lower()).strip()
        key = (category, normalized)
        if key in seen:
            return
        seen.add(key)
        issues.append(
            Finding(
                category=category,
                url=page_url,
                message=message,
                evidence=clip(evidence),
                severity=severity,
                how_to_fix=how_to_fix,
            )
        )

    for chunk in chunks:
        if materiality_score(chunk, page_url) < 3:
            continue

        target_match = MATERIAL_TARGET.search(chunk)
        if target_match:
            pct = target_match.group(3)
            year = target_match.group(6)
            severity = "medium" if has_substantiation else "high"
            message = (
                f"Materiële klimaatclaim met target ({pct} tegen {year}) zonder concrete onderbouwing nabij de claim."
                if severity == "high"
                else f"Materiële klimaatclaim met target ({pct} tegen {year}) gevonden; verifieer volledigheid van onderbouwing."
            )
            add_issue(
                category="MATERIAL_TARGET_CLAIM",
                severity=severity,
                message=message,
                evidence=chunk,
                how_to_fix="Vermeld scope 1/2/3, baseline jaar, meetmethode, tussendoelen en publieke voortgangsrapportering.",
            )

        if ABSOLUTE_CLAIMS.search(chunk):
            severity = "medium"
            offset_note = " Vermeld expliciet de rol van offsets/certificaten." if OFFSET_HINT.search(chunk) else ""
            message = (
                "Materiële absolute claim (net zero/carbon neutral) zonder duidelijke afbakening of bewijs."
                if severity == "high"
                else "Materiële absolute claim (net zero/carbon neutral) gevonden; controleer afbakening en bewijs."
            )
            add_issue(
                category="MATERIAL_ABSOLUTE_CLAIM",
                severity=severity,
                message=message,
                evidence=chunk,
                how_to_fix=(
                    "Specifieer organisatorische en operationele scope, baseline, methodologie en onafhankelijke assurance."
                    + offset_note
                ),
            )

    return issues


def scan_site(start_url: str, max_pages: int = 10) -> Tuple[int, List[Finding]]:
    start_url = normalize_url(start_url)
    if not start_url:
        return 0, []

    visited: Set[str] = set()
    queue: deque[str] = deque([start_url])
    session = requests.Session()

    all_findings: List[Finding] = []

    while queue and len(visited) < max_pages:
        url = queue.popleft()
        if url in visited or not same_domain(start_url, url):
            continue

        html = fetch_html(url, session=session)
        visited.add(url)
        if not html:
            continue

        links = extract_links(url, html)
        prioritized = sorted(
            links,
            key=lambda link: 0 if re.search(r"sustain|esg|climate|environment|duurzaam|impact", link, re.I) else 1,
        )
        for link in prioritized:
            if link not in visited and same_domain(start_url, link):
                queue.append(link)

        text = html_to_text(html)
        all_findings.extend(find_issues_on_page(url, text))
        time.sleep(0.12)

    dedup = {}
    for finding in all_findings:
        normalized_evidence = re.sub(r"\s+", " ", finding.evidence.lower()).strip()
        key = (finding.category, normalized_evidence[:180])
        dedup[key] = finding
    findings = list(dedup.values())

    findings.sort(key=lambda f: (0 if f.severity == "high" else 1, f.category, f.url))

    high_findings = [f for f in findings if f.severity == "high"][:5]
    medium_findings = [f for f in findings if f.severity == "medium"][:8]

    return len(visited), high_findings + medium_findings


def calc_risk_score(findings: List[Finding]) -> int:
    high = sum(1 for f in findings if f.severity == "high")
    medium = sum(1 for f in findings if f.severity == "medium")
    return max(0, min(100, high * 25 + medium * 10))


@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/scan")
async def scan(request: Request, url: str = Form(...), max_pages: int = Form(10)):
    target = normalize_url(url)
    if not target:
        return RedirectResponse(url="/", status_code=303)

    try:
        max_pages_int = int(max_pages)
    except Exception:
        max_pages_int = 10
    max_pages_int = max(1, min(50, max_pages_int))

    pages_scanned, findings_obj = scan_site(target, max_pages=max_pages_int)
    risk = calc_risk_score(findings_obj)

    def to_template_finding(f: Finding) -> dict:
        readable_source = is_readable_http_url(f.url)
        return {
            "label": f.category.replace("_", " ").title(),
            "snippet": f.evidence,
            "page_url": f.url,
            "page_url_readable": readable_source,
            "page_url_label": (
                f.url if readable_source else "Bronpagina URL onleesbaar (ongeldige of incoherente link in scanresultaat)."
            ),
            "message": f.message,
            "rule": RULEBOOK.get(f.category, "EmpCo Art. 5 — Claim moet duidelijk, juist en verifieerbaar zijn."),
            "recommendation": f.how_to_fix,
            "severity": f.severity,
        }

    context = {
        "request": request,
        "target_url": target,
        "input_url": target,
        "pages_scanned": pages_scanned,
        "risk_score": risk,
        "findings": [to_template_finding(f) for f in findings_obj],
        "findings_high": [to_template_finding(f) for f in findings_obj if f.severity == "high"],
        "findings_medium": [to_template_finding(f) for f in findings_obj if f.severity == "medium"],
        "findings_low": [],
    }

    return templates.TemplateResponse("report.html", context)


@app.get("/health")
async def health():
    return {"ok": True}
