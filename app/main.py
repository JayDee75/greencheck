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

SUSTAINABILITY_CONTEXT = re.compile(r"\b(sustainab(?:le|ility)|esg|environmental|duurza+am)\b", re.I)

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

GENERIC_SUSTAINABILITY_CLAIM = re.compile(
    r"\b("
    r"sustainable|conscious|responsible|environmentally\s+friendly|eco[-\s]?friendly|green|"
    r"carbon\s+friendly|energy\s+efficient|carbon\s+neutral|climate\s+neutral|"
    r"duurzaam|duurzame|milieuvriendelijk|ecologisch|groen|klimaatneutraal|co2[-\s]?neutraal|"
    r"nachhaltig|umweltfreundlich|klimaneutral|ressourcenschonend|"
    r"durable|responsable|respectueux\s+de\s+l[’']environnement|vert|neutre\s+en\s+carbone|"
    r"sostenible|respetuoso\s+con\s+el\s+medio\s+ambiente|ecol[oó]gico|verde|neutro\s+en\s+carbono"
    r")\b",
    re.I,
)

GENERIC_SUBSTANTIATION_HINT = re.compile(
    r"\b(iso\s*14001|eu\s*ecolabel|type\s*i\s*ecolabel|certifi(?:ed|cation)|"
    r"third[-\s]?party\s+verified|verified|lca|life\s*cycle|scope\s*1|scope\s*2|scope\s*3|"
    r"baseline\s*year|science[-\s]?based|sbti|ghg\s*protocol|\d{1,3}\s*(%|percent)\s*(by|before|in)\s*20\d{2})\b",
    re.I,
)

NON_MATERIAL_URL_HINT = re.compile(r"/(news|blog|events|careers|jobs|investors?)/", re.I)
COLOR_CONTEXT_HINT = re.compile(
    r"\b(color|colour|pantone|rgb|hex|palette|shirt|t-?shirt|dress|paint|"
    r"kleur|couleur|farbe|camiseta|vestido|farbe)\b",
    re.I,
)
CLAIM_ACTION_HINT = re.compile(
    r"\b(our|we|product|service|solution|packaging|company|brand|"
    r"100%|fully|made\s+with|designed\s+to|certified|verified|"
    r"ons|notre|nuestro|unser|wij|nous|somos|ist)\b",
    re.I,
)
CLAIM_SUBJECT_HINT = re.compile(
    r"\b(we|our|us|company|brand|business|group|product|products|service|services|solution|solutions|"
    r"offering|offerings|packaging|this\s+product|this\s+service|ons|notre|nuestro|unser|wij|nous)\b",
    re.I,
)
THIRD_PARTY_EXPLANATORY_CONTEXT = re.compile(
    r"\b(ecovadis|b\s*corp(?:oration)?|bcorp|un\s+global\s+compact|iso\s*\d{3,5}|"
    r"science\s+based\s+targets?\s+initiative|sbti|fairtrade|eu\s*ecolabel|"
    r"ratings?|rating\s+framework|framework|methodolog(?:y|ie)|evaluation|assess(?:ment|ed)|"
    r"third[-\s]?party|independent|certification\s+scheme)\b",
    re.I,
)


@dataclass
class Finding:
    category: str
    url: str
    message: str
    evidence: str
    severity: str
    how_to_fix: str


SELF_MADE_LABEL = re.compile(
    r"\b(our\s+(?:eco|green|sustainab(?:ility|le))\s*(?:label|seal|badge)|"
    r"internal\s+certification|own\s+certification|self[-\s]?certified|"
    r"proprietary\s+(?:eco|green)\s*(?:label|badge)|"
    r"eco\s*(?:label|seal|badge)\s+by\s+us)\b",
    re.I,
)

OFFICIAL_LABEL_HINT = re.compile(
    r"\b(eu\s*ecolabel|energy\s*star|blue\s*angel|nordic\s*swan|fsc|pefc|"
    r"fairtrade|rainforest\s*alliance|type\s*i\s*ecolabel|iso\s*14024)\b",
    re.I,
)

RULEBOOK = {
    "GENERIC_ENVIRONMENTAL_CLAIMS": (
        "Detected Rule Violation: This is a generic environmental claim used as a marketing-style statement "
        "without clear, specific, and verifiable environmental performance information."
    ),
    "CARBON_NEUTRALITY_CLAIMS": (
        "Detected Rule Violation: This is a product/service-level carbon neutrality style claim presented without a clear, "
        "auditable basis and boundaries, creating a high risk of misleading neutrality messaging."
    ),
    "FUTURE_NET_ZERO_TARGETS": (
        "Detected Rule Violation: This future net zero / emissions reduction target is presented without a concrete implementation "
        "plan, quantified milestones, or clear verification details."
    ),
    "SUSTAINABILITY_LABELS": (
        "Detected Rule Violation: This sustainability seal/label appears self-created or insufficiently linked to a recognised "
        "independent certification scheme."
    ),
}

CATEGORY_LABELS = {
    "GENERIC_ENVIRONMENTAL_CLAIMS": "Generic Environmental Claim",
    "CARBON_NEUTRALITY_CLAIMS": "Carbon Neutrality Claim",
    "FUTURE_NET_ZERO_TARGETS": "Future Net Zero Target",
    "SUSTAINABILITY_LABELS": "Sustainability Label",
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

    unique_lines: List[str] = []
    seen = set()
    for line in lines:
        key = re.sub(r"\s+", " ", line.lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        unique_lines.append(line)

    if unique_lines:
        return unique_lines

    fallback = re.sub(r"\s+", " ", normalized).strip()
    return [fallback] if len(fallback) >= 25 else []


def clip(s: str, n: int = 280) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"


def clean_snippet(s: str) -> str:
    snippet = re.sub(r"\s+", " ", (s or "")).strip()
    snippet = re.sub(r"[^\x20-\x7E\u00A0-\u024F\u2018-\u201F€£¥]", "", snippet)
    return snippet


def materiality_score(chunk: str, page_url: str) -> int:
    score = 0
    if CLIMATE_CONTEXT.search(chunk):
        score += 1
    if SUSTAINABILITY_CONTEXT.search(chunk):
        score += 1
    if MATERIAL_TARGET.search(chunk):
        score += 2
    if ABSOLUTE_CLAIMS.search(chunk):
        score += 2
    if GENERIC_SUSTAINABILITY_CLAIM.search(chunk):
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

    has_plan_substantiation = bool(PLAN_SUBSTANTIATION.search(text))
    has_generic_substantiation = bool(GENERIC_SUBSTANTIATION_HINT.search(text))
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
            severity = "medium" if has_plan_substantiation else "high"
            message = (
                f"Future emissions target ({pct} by {year}) lacks concrete implementation details, quantified milestones, "
                "or clear third-party validation."
                if severity == "high"
                else f"Future emissions target ({pct} by {year}) needs tighter implementation detail and verification evidence."
            )
            add_issue(
                category="FUTURE_NET_ZERO_TARGETS",
                severity=severity,
                message=message,
                evidence=clean_snippet(chunk),
                how_to_fix=(
                    "Recommendations and advice: Add a concrete transition plan with scope boundaries (Scopes 1, 2, and where relevant 3), "
                    "a baseline year, interim yearly or multi-year milestones, investment and execution measures, and a transparent "
                    "monitoring method with independent verification."
                ),
            )

        has_absolute_claim = bool(ABSOLUTE_CLAIMS.search(chunk))
        if has_absolute_claim:
            severity = "high" if OFFSET_HINT.search(chunk) else "medium"
            message = (
                "Carbon neutrality language is used at product/service level with offsetting-style context, which is high risk."
                if severity == "high"
                else "Carbon neutrality language is used and should be tightened with precise boundaries and verifiable substantiation."
            )
            add_issue(
                category="CARBON_NEUTRALITY_CLAIMS",
                severity=severity,
                message=message,
                evidence=clean_snippet(chunk),
                how_to_fix=(
                    "Recommendations and advice: Avoid absolute neutrality wording for products/services. Replace with precise "
                    "contribution statements, state emissions boundaries, disclose residual emissions, and present independently "
                    "verifiable methodology and assurance details."
                ),
            )

        generic_match = GENERIC_SUSTAINABILITY_CLAIM.search(chunk)
        if generic_match and COLOR_CONTEXT_HINT.search(chunk) and not CLAIM_ACTION_HINT.search(chunk):
            continue

        has_claim_subject = bool(CLAIM_SUBJECT_HINT.search(chunk) or CLAIM_ACTION_HINT.search(chunk))
        is_third_party_context = bool(THIRD_PARTY_EXPLANATORY_CONTEXT.search(chunk))
        if generic_match and not has_generic_substantiation and not has_absolute_claim and has_claim_subject and not is_third_party_context:
            claim_text = generic_match.group(0)
            commercial_context = bool(re.search(r"\b(offerings?|services?|producten?|solutions?)\b", chunk, re.I))
            severity = "high" if commercial_context else "medium"
            add_issue(
                category="GENERIC_ENVIRONMENTAL_CLAIMS",
                severity=severity,
                message=(
                    f"Generic environmental wording ('{claim_text}') is used without specific, measurable, and verifiable performance details."
                ),
                evidence=clean_snippet(chunk),
                how_to_fix=(
                    "Recommendations and advice: Replace the generic wording with specific, measurable and verifiable statements "
                    "(state the environmental impact addressed, baseline, scope, metric, and achieved result). If certification is "
                    "relied upon, reference a recognised independent scheme; otherwise clearly qualify the claim."
                ),
            )

        label_match = SELF_MADE_LABEL.search(chunk)
        if label_match and not OFFICIAL_LABEL_HINT.search(chunk):
            add_issue(
                category="SUSTAINABILITY_LABELS",
                severity="medium",
                message="A sustainability label or badge appears self-declared and not clearly tied to a recognised third-party scheme.",
                evidence=clean_snippet(chunk),
                how_to_fix=(
                    "Recommendations and advice: Replace self-made sustainability badges with recognised third-party certification "
                    "references where applicable, and provide clear criteria, governance, and verification details."
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

    severity_rank = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (severity_rank.get(f.severity, 3), f.category, f.url))

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
            "label": CATEGORY_LABELS.get(f.category, f.category.replace("_", " ").title()),
            "snippet": f.evidence,
            "page_url": f.url,
            "page_url_readable": readable_source,
            "page_url_label": (
                f.url if readable_source else "Source page URL unreadable (invalid or incoherent link in scan result)."
            ),
            "message": f.message,
            "rule": RULEBOOK.get(f.category, "Detected Rule Violation: The claim is not sufficiently clear, accurate, and verifiable."),
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
    }

    return templates.TemplateResponse("report.html", context)


@app.get("/health")
async def health():
    return {"ok": True}
