from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

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
    r"ratings?|rating\s+framework|framework|benchmark|methodolog(?:y|ie)|evaluation|assess(?:ment|ed)|"
    r"third[-\s]?party|independent|certification\s+scheme|recogni[sz]ed\s+authority|"
    r"sustainable\s+procurement|labou?r\s*(?:&|and)\s*human\s+rights)\b",
    re.I,
)
ASSET_PATH_HINT = re.compile(
    r"(?i)(?:/(?:sites/default/files|media|assets)/|"
    r"\.(?:jpg|jpeg|png|gif|webp|svg|avif)(?:\?[^ ]*)?$|"
    r"\b[a-z0-9][a-z0-9._-]{3,}\.(?:jpg|jpeg|png|gif|webp|svg|avif)\b)"
)
IMAGE_FILE_HINT = re.compile(
    r"(?i)\b(?:image|img|photo|picture|banner|hero|thumbnail|forest|grass|globe|"
    r"close[-_]?up)[-_a-z0-9]{0,80}\.(?:jpg|jpeg|png|gif|webp|svg|avif)\b"
)
INFO_EDITORIAL_URL_HINT = re.compile(r"/(press|newsroom|publications?|media|insights?)/", re.I)
INFO_EDITORIAL_TEXT_HINT = re.compile(
    r"\b(press|newsroom|publications?|latest\s+news|read\s+more|stay\s+informed|milestones?)\b",
    re.I,
)


def is_asset_or_image_text(chunk: str) -> bool:
    normalized = (chunk or "").strip()
    return bool(ASSET_PATH_HINT.search(normalized) or IMAGE_FILE_HINT.search(normalized))


@dataclass
class Finding:
    category: str
    url: str
    message: str
    evidence: str
    severity: str
    how_to_fix: str
    llm_prompt: str = ""


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
        "Detected Rule Violation: This is a generic environmental claim under Directive (EU) 2024/825. "
        "A generic environmental claim is a broad, non-specific environmental benefit statement that is not "
        "supported by clear, specific, and verifiable substantiation. The wording risks misleading consumers "
        "because the claim does not identify a concrete environmental aspect, metric, scope, or evidence base."
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

TAXONOMY_PATH = Path("standards/taxonomy.json")

LLM_TAXONOMY_PROMPT_GUIDANCE = (
    "Use the provided taxonomy as guidance for detecting environmental claims.\n\n"
    "The taxonomy is NOT exhaustive.\n"
    "You must also detect similar, equivalent, or derived expressions.\n\n"
    "Do not rely solely on exact keyword matches.\n"
    "Detect both explicit environmental claims and broader sustainability-framed claims that imply environmental benefit.\n\n"
    "If multiple connected sentences are needed to preserve the meaning of a claim, return the full sentence block.\n\n"
    "Do not exclude claims simply because they are framed as ESG, innovation, or future-oriented messaging."
)

MARKETING_OR_DESCRIPTIVE_CONTEXT = re.compile(
    r"\b(we|our|company|brand|product|service|solution|offerings?|deliver|embedding|"
    r"proving|designed|built|future|innovation|digital)\b",
    re.I,
)


def load_taxonomy(path: Path = TAXONOMY_PATH) -> Dict[str, List[str]]:
    default = {
        "tier1_direct_environmental": [],
        "tier2_broad_sustainability": [],
        "tier3_context": [],
        "tier4_substantiation": [],
        "tier5_false_positive": [],
    }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

    for key in default:
        values = payload.get(key, [])
        default[key] = [str(v).strip() for v in values if str(v).strip()]
    return default


def _compile_keyword_patterns(keywords: List[str]) -> List[Tuple[str, re.Pattern[str]]]:
    patterns: List[Tuple[str, re.Pattern[str]]] = []
    for keyword in keywords:
        escaped = re.escape(keyword)
        pattern = re.compile(rf"(?<!\w){escaped}(?!\w)", re.I)
        patterns.append((keyword.lower(), pattern))
    return patterns


TAXONOMY = load_taxonomy()
TAXONOMY_PATTERNS = {
    tier: _compile_keyword_patterns(words) for tier, words in TAXONOMY.items()
}


def normalize_url(u: str) -> str:
    u = (u or "").strip()
    if not u:
        return ""
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    return u


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
        if len(line) >= 25:
            lines.append(line)

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


def clean_snippet(s: str) -> str:
    parsed = BeautifulSoup(html.unescape(s or ""), "html.parser")
    snippet = parsed.get_text(separator=" ")
    snippet = re.sub(r"\s+", " ", snippet).strip()
    snippet = re.sub(r"\s+([:;,.!?])", r"\1", snippet)
    snippet = re.sub(r"[^\x20-\x7E\u00A0-\u024F\u2018-\u201F€£¥]", "", snippet)
    return snippet


def make_sentence_blocks(text: str) -> List[str]:
    paragraphs = [p.strip() for p in re.split(r"\n+", text or "") if p.strip()]
    blocks: List[str] = []
    for paragraph in paragraphs:
        blocks.append(paragraph)
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", paragraph) if s.strip()]
        if len(sentences) <= 1:
            continue
        for idx in range(len(sentences)):
            window = " ".join(sentences[idx : idx + 2]).strip()
            if window:
                blocks.append(window)
    if not blocks and text.strip():
        blocks.append(text.strip())
    return blocks


def _matched_taxonomy_keywords(text: str, tier: str) -> Set[str]:
    matches: Set[str] = set()
    for keyword, pattern in TAXONOMY_PATTERNS.get(tier, []):
        if pattern.search(text):
            matches.add(keyword)
    return matches


def taxonomy_signal_for_block(block: str) -> Dict[str, object]:
    normalized = (block or "").lower()
    tier1 = _matched_taxonomy_keywords(normalized, "tier1_direct_environmental")
    tier2 = _matched_taxonomy_keywords(normalized, "tier2_broad_sustainability")
    tier3 = _matched_taxonomy_keywords(normalized, "tier3_context")
    tier4 = _matched_taxonomy_keywords(normalized, "tier4_substantiation")
    tier5 = _matched_taxonomy_keywords(normalized, "tier5_false_positive")

    in_marketing_context = bool(MARKETING_OR_DESCRIPTIVE_CONTEXT.search(block))
    trigger_llm = bool(tier1) or (bool(tier2) and in_marketing_context)
    if tier5 and not tier1 and not tier2:
        trigger_llm = False

    confidence = 1 if trigger_llm else 0
    if (tier1 or tier2) and tier3:
        confidence += 1

    return {
        "trigger_llm": trigger_llm,
        "confidence": confidence,
        "tier1": tier1,
        "tier2": tier2,
        "tier3": tier3,
        "tier4": tier4,
        "tier5": tier5,
        "marketing_context": in_marketing_context,
    }


def build_llm_prompt(claim_block: str, signal: Dict[str, object]) -> str:
    return (
        f"{LLM_TAXONOMY_PROMPT_GUIDANCE}\n\n"
        f"Claim block:\n{claim_block}\n\n"
        f"Taxonomy context: "
        f"tier1={sorted(signal.get('tier1', []))}, "
        f"tier2={sorted(signal.get('tier2', []))}, "
        f"tier3={sorted(signal.get('tier3', []))}, "
        f"tier4={sorted(signal.get('tier4', []))}, "
        f"tier5={sorted(signal.get('tier5', []))}, "
        f"confidence={signal.get('confidence', 0)}."
    )


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
    sentence_blocks = make_sentence_blocks(text)
    block_signals = [(block, taxonomy_signal_for_block(block)) for block in sentence_blocks]

    has_plan_substantiation = bool(PLAN_SUBSTANTIATION.search(text))
    has_generic_substantiation = bool(GENERIC_SUBSTANTIATION_HINT.search(text))
    issues: List[Finding] = []
    seen: Set[Tuple[str, str]] = set()

    def add_issue(
        category: str,
        severity: str,
        message: str,
        evidence: str,
        how_to_fix: str,
        llm_prompt: str = "",
    ) -> None:
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
                evidence=evidence.strip(),
                severity=severity,
                how_to_fix=how_to_fix,
                llm_prompt=llm_prompt,
            )
        )

    for chunk in chunks:
        if is_asset_or_image_text(chunk):
            continue
        if materiality_score(chunk, page_url) < 3:
            continue
        matching_blocks = [block for block, _ in block_signals if chunk in block or block in chunk]
        relevant_block = max(matching_blocks, key=len) if matching_blocks else chunk
        taxonomy_signal = taxonomy_signal_for_block(relevant_block)
        should_trigger_llm = bool(taxonomy_signal["trigger_llm"])

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
                llm_prompt=build_llm_prompt(relevant_block, taxonomy_signal),
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
                llm_prompt=build_llm_prompt(relevant_block, taxonomy_signal),
            )

        generic_match = GENERIC_SUSTAINABILITY_CLAIM.search(chunk)
        if generic_match and COLOR_CONTEXT_HINT.search(chunk) and not CLAIM_ACTION_HINT.search(chunk):
            continue

        has_claim_subject = bool(CLAIM_SUBJECT_HINT.search(chunk) or CLAIM_ACTION_HINT.search(chunk))
        is_third_party_context = bool(THIRD_PARTY_EXPLANATORY_CONTEXT.search(chunk))
        commercial_context = bool(re.search(r"\b(offerings?|services?|producten?|solutions?)\b", chunk, re.I))
        is_editorial_context = bool(INFO_EDITORIAL_URL_HINT.search(page_url) or INFO_EDITORIAL_TEXT_HINT.search(chunk))
        if (
            (generic_match or should_trigger_llm)
            and not has_generic_substantiation
            and not has_absolute_claim
            and has_claim_subject
            and not is_third_party_context
            and not (is_editorial_context and not commercial_context)
        ):
            claim_text = generic_match.group(0) if generic_match else "broad sustainability framing"
            severity = "high" if commercial_context else "medium"
            substantiation_context = ""
            if taxonomy_signal["tier4"]:
                substantiation_context = (
                    f" Substantiation-related terms are present ({', '.join(sorted(taxonomy_signal['tier4']))}) "
                    "and are passed for legal interpretation rather than treated as automatic risk reduction."
                )
            add_issue(
                category="GENERIC_ENVIRONMENTAL_CLAIMS",
                severity=severity,
                message=(
                    f"Generic environmental wording ('{claim_text}') is used without specific, measurable, and verifiable performance details."
                    f"{substantiation_context}"
                ),
                evidence=clean_snippet(relevant_block),
                how_to_fix=(
                    "Recommendations and advice: Replace the generic wording with specific, measurable and verifiable statements "
                    "(state the environmental impact addressed, baseline, scope, metric, and achieved result). If certification is "
                    "relied upon, reference a recognised independent scheme, certification, or label (for example SBTi, relevant ISO "
                    "standards, EcoVadis, or EU Ecolabel). If no such substantiation exists, rewrite the claim to avoid broad "
                    "environmental benefit language."
                ),
                llm_prompt=build_llm_prompt(relevant_block, taxonomy_signal),
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
                llm_prompt=build_llm_prompt(relevant_block, taxonomy_signal),
            )

    return issues


def scan_site(start_url: str, max_pages: int = 10) -> Tuple[int, List[Finding]]:
    start_url = normalize_url(start_url)
    if not start_url:
        return 0, []

    session = requests.Session()
    html = fetch_html(start_url, session=session)
    if not html:
        return 1, []
    text = html_to_text(html)
    all_findings: List[Finding] = find_issues_on_page(start_url, text)

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
    return 1, high_findings + medium_findings


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
        rule_text = RULEBOOK.get(f.category, "Detected Rule Violation: The claim is not sufficiently clear, accurate, and verifiable.")
        if f.category == "GENERIC_ENVIRONMENTAL_CLAIMS":
            detected_keywords = sorted(
                {
                    match.group(0).strip().lower()
                    for match in GENERIC_SUSTAINABILITY_CLAIM.finditer(f.evidence)
                    if match.group(0).strip()
                }
            )
            detected_keywords.extend(
                sorted(
                    _matched_taxonomy_keywords(f.evidence.lower(), "tier2_broad_sustainability")
                    | _matched_taxonomy_keywords(f.evidence.lower(), "tier1_direct_environmental")
                )
            )
            if "sustainable components" in f.evidence.lower():
                detected_keywords.insert(0, "sustainable components")
            detected_keywords = sorted(dict.fromkeys(detected_keywords))
            if detected_keywords:
                repeated_keywords = ", ".join(detected_keywords)
                rule_text = (
                    f"{rule_text} Problematic keywords detected: \"{repeated_keywords}\". "
                    f"Repeat and verify these keywords with substantiation: \"{repeated_keywords}\"."
                )
        return {
            "label": CATEGORY_LABELS.get(f.category, f.category.replace("_", " ").title()),
            "snippet": f.evidence,
            "page_url": f.url,
            "page_url_readable": readable_source,
            "page_url_label": (
                f.url if readable_source else "Source page URL unreadable (invalid or incoherent link in scan result)."
            ),
            "message": f.message,
            "rule": rule_text,
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
