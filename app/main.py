from __future__ import annotations

import html
import json
import logging
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
EXCLUDED_SECTION_HINT = re.compile(
    r"(related|read[-_\s]?next|read[-_\s]?more|more[-_\s]?articles?|suggested|recommended|"
    r"teaser|promo|footer|breadcrumb|newsletter|menu|navigation|cross[-_\s]?link|card|widget)",
    re.I,
)
EXCLUDED_HEADING_TEXT_HINT = re.compile(
    r"\b(related articles?|more articles?|read more|suggested content|recommended reading|"
    r"recommended|suggested|you may also like|more stories)\b",
    re.I,
)
MAIN_CONTAINER_HINT = re.compile(
    r"(article|blog|post|content|story|main|entry|body)",
    re.I,
)

HERO_SUSTAINABILITY_SIGNAL = re.compile(
    r"\b(sustainable|sustainability|responsible|responsibility|better\s+future|positive\s+impact|"
    r"future[-\s]?proof|duurza+am|milieuvriendelijk)\b",
    re.I,
)
HERO_SOLUTION_SIGNAL = re.compile(
    r"\b(solution|solutions|service|services|product|products|technology|innovation|digital|platform)\b",
    re.I,
)
HERO_BENEFIT_SIGNAL = re.compile(
    r"\b(proving|helping|improving|reducing|enabling|driving|building|advancing|transforming)\b",
    re.I,
)
HERO_ESG_SIGNAL = re.compile(
    r"\b(esg|environmental|environment|emissions?|climate|greener|lower[-\s]?impact|carbon)\b",
    re.I,
)
HERO_HARD_FALLBACK = re.compile(r"\b(sustainable|better\s+future|responsible|esg)\b", re.I)


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
    "Detect not only explicit environmental claims, but also broad sustainability-framed marketing language that states or "
    "implies environmental benefit, environmental responsibility, or reduced environmental harm.\n\n"
    "Treat wording such as 'sustainable components', 'better future', 'responsible innovation', 'sustainable solutions', or "
    "similar expressions as potential generic environmental claims when they imply environmental benefit without clear, specific, "
    "and verifiable substantiation.\n\n"
    "If multiple connected sentences are needed to preserve the meaning of a claim, return the full sentence block.\n\n"
    "Do not exclude a statement merely because it also refers to ESG, innovation, or future-oriented messaging."
)

MARKETING_OR_DESCRIPTIVE_CONTEXT = re.compile(
    r"\b(we|our|company|brand|product|service|solution|offerings?|deliver|embedding|"
    r"proving|designed|built|future|innovation|digital)\b",
    re.I,
)

SUSTAINABILITY_FALLBACK = re.compile(r"\b(sustainable|sustainability)\b", re.I)
SUSTAINABILITY_FRAMING = re.compile(
    r"\b(product|products|solution|solutions|service|services|innovation|future|esg|impact|responsib(?:le|ility))\b",
    re.I,
)

LOGGER = logging.getLogger(__name__)


def log_pipeline_event(stage: str, block: str, **details: object) -> None:
    compact = re.sub(r"\s+", " ", block).strip()
    if len(compact) > 220:
        compact = compact[:217] + "..."
    detail_text = ", ".join(f"{k}={v}" for k, v in details.items())
    LOGGER.warning("[pipeline:%s] %s | %s", stage, compact, detail_text)


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


def _node_hint_text(node) -> str:
    attrs: List[str] = []
    for key in ("id", "class", "role", "aria-label", "data-testid"):
        value = node.get(key)
        if isinstance(value, list):
            attrs.append(" ".join(v for v in value if isinstance(v, str)))
        elif isinstance(value, str):
            attrs.append(value)
    attrs.append(node.name or "")
    return " ".join(attrs)


def _is_excluded_container(node) -> bool:
    if node.name in {"nav", "footer", "header", "aside", "form"}:
        return True
    hint_text = _node_hint_text(node)
    if EXCLUDED_SECTION_HINT.search(hint_text):
        return True
    heading = node.find(["h1", "h2", "h3", "h4"], recursive=False)
    heading_text = heading.get_text(" ", strip=True) if heading else ""
    return bool(EXCLUDED_HEADING_TEXT_HINT.search(heading_text))


def _is_related_heading_node(node) -> bool:
    if not getattr(node, "name", None):
        return False
    if node.name not in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        return False
    return bool(EXCLUDED_HEADING_TEXT_HINT.search(node.get_text(" ", strip=True)))


def _extract_main_article_text(html_doc: str) -> Tuple[str, Dict[str, object]]:
    soup = BeautifulSoup(html_doc, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()

    excluded_nodes = soup.find_all(_is_excluded_container)
    related_excluded = any(
        re.search(
            r"(related|read[-_\s]?next|more[-_\s]?articles?|suggested|teaser|recommended|you may also like)",
            f"{_node_hint_text(node)} {node.get_text(' ', strip=True)[:180]}",
            re.I,
        )
        for node in excluded_nodes
    )
    for node in excluded_nodes:
        node.decompose()

    candidates = []
    for node in soup.find_all(["article", "main", "section", "div"]):
        hint = _node_hint_text(node)
        if node.name in {"article", "main"} or MAIN_CONTAINER_HINT.search(hint):
            text_len = len(node.get_text(" ", strip=True))
            heading_score = 250 if node.find(["h1", "h2"]) else 0
            para_score = 150 if node.find("p") else 0
            candidates.append((text_len + heading_score + para_score, node))

    best = max(candidates, key=lambda item: item[0])[1] if candidates else soup.body or soup

    related_sections_removed = 0
    for heading in list(best.find_all(_is_related_heading_node)):
        wrapper = heading.find_parent(["section", "div", "aside", "ul", "ol"]) or heading.parent
        if wrapper is None or wrapper == best:
            continue
        if best not in wrapper.parents:
            continue
        related_sections_removed += 1
        wrapper.decompose()

    title_node = best.find("h1") or soup.find("h1") or best.find("h2")
    title = _normalize_block_text(title_node.get_text(" ", strip=True)) if title_node else ""
    intro = ""
    first_intro_node = None
    short_intro_fallback = ""
    short_intro_node = None
    if title_node:
        for intro_node in title_node.find_all_next("p"):
            if best not in intro_node.parents:
                continue
            parent_hint = _node_hint_text(intro_node.parent or intro_node)
            if EXCLUDED_SECTION_HINT.search(parent_hint):
                continue
            intro_text = _normalize_block_text(intro_node.get_text(" ", strip=True))
            if len(intro_text) < 10:
                continue
            if len(intro_text) < 40:
                if not short_intro_fallback:
                    short_intro_fallback = intro_text
                    short_intro_node = intro_node
                continue
            intro = intro_text
            first_intro_node = intro_node
            break

    if not intro and short_intro_fallback:
        intro = short_intro_fallback
        first_intro_node = short_intro_node
    hero_paragraphs: List[str] = [intro] if intro else []
    hero_block = _normalize_block_text(" ".join(part for part in [title, intro] if part))

    blocks: List[str] = []
    for node in best.find_all(["h2", "h3", "p", "li"]):
        if _is_excluded_container(node):
            continue
        if any(_is_excluded_container(parent) for parent in node.parents if getattr(parent, "name", None)):
            continue
        if first_intro_node is not None and node == first_intro_node:
            continue
        text = node.get_text(" ", strip=True)
        if len(text) >= 25:
            blocks.append(text)

    ordered: List[str] = []
    seen: Set[str] = set()
    for block in [title, intro] + blocks:
        normalized = _normalize_block_text(block)
        if len(normalized) < 20:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(normalized)

    main_text = "\n".join(ordered).strip()
    main_lower = main_text.lower()
    debug = {
        "main_content_length": len(main_text),
        "main_title": title,
        "first_paragraph_after_title": intro,
        "hero_intro_paragraphs": hero_paragraphs,
        "hero_block": hero_block,
        "intro_captured": bool(intro),
        "found_sustainable_components": "sustainable components" in main_lower,
        "found_better_future": "better future" in main_lower,
        "related_articles_excluded": bool(related_excluded or related_sections_removed),
        "related_sections_removed": related_sections_removed,
    }
    return main_text, debug


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
            long_window = " ".join(sentences[idx : idx + 3]).strip()
            if long_window:
                blocks.append(long_window)
    for idx in range(len(paragraphs)):
        merged = " ".join(paragraphs[idx : idx + 3]).strip()
        if merged:
            blocks.append(merged)
    if not blocks and text.strip():
        blocks.append(text.strip())
    return blocks


def _normalize_block_text(block: str) -> str:
    return re.sub(r"\s+", " ", (block or "")).strip()


def hero_signal_groups(block: str) -> Dict[str, List[str]]:
    groups = {
        "A_sustainability_responsibility": sorted(set(m.group(0).lower() for m in HERO_SUSTAINABILITY_SIGNAL.finditer(block))),
        "B_solution_service_innovation": sorted(set(m.group(0).lower() for m in HERO_SOLUTION_SIGNAL.finditer(block))),
        "C_benefit_improvement": sorted(set(m.group(0).lower() for m in HERO_BENEFIT_SIGNAL.finditer(block))),
        "D_esg_environment_context": sorted(set(m.group(0).lower() for m in HERO_ESG_SIGNAL.finditer(block))),
    }
    return groups


def _candidate_blocks(text: str, hero_block: str = "") -> List[str]:
    raw_blocks = make_sentence_blocks(text)
    ordered: List[str] = []
    seen: Set[str] = set()
    normalized_hero_block = _normalize_block_text(hero_block)
    if len(normalized_hero_block) >= 20:
        ordered.append(normalized_hero_block)
        seen.add(normalized_hero_block.lower())
    for block in raw_blocks:
        normalized = _normalize_block_text(block)
        if len(normalized) < 25:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(normalized)
    forced_claim_block_added = bool(normalized_hero_block)
    log_pipeline_event(
        "candidate_block_build",
        ordered[0] if ordered else "",
        forced_claim_block_added=forced_claim_block_added,
        hero_block_present=bool(normalized_hero_block),
        candidate_count=len(ordered),
    )
    return ordered


def _claim_priority(block: str) -> int:
    lower = block.lower()
    if "sustainable components" in lower or "better future" in lower:
        return 0
    signal_groups = hero_signal_groups(block)
    if sum(1 for values in signal_groups.values() if values) >= 2:
        return 1
    if len(lower) > 140:
        return 2
    return 3


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
    trigger_llm = bool(tier1) or bool(tier2)
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


def find_issues_on_page(page_url: str, text: str, hero_block: str = "", extraction_debug: Optional[Dict[str, object]] = None) -> List[Finding]:
    chunks = make_chunks(text)
    normalized_hero_block = _normalize_block_text(hero_block)
    candidate_blocks = _candidate_blocks(text, hero_block=normalized_hero_block)
    if not chunks and not candidate_blocks:
        return []
    candidate_blocks = sorted(candidate_blocks, key=_claim_priority)
    block_signals = [(block, taxonomy_signal_for_block(block)) for block in candidate_blocks]
    for block, signal in block_signals:
        log_pipeline_event("block_extracted", block, tier2=sorted(signal["tier2"]))
    hero_signals = hero_signal_groups(normalized_hero_block) if normalized_hero_block else {}
    hero_group_count = sum(1 for values in hero_signals.values() if values) if normalized_hero_block else 0
    hero_hard_fallback_triggered = bool(normalized_hero_block and HERO_HARD_FALLBACK.search(normalized_hero_block))
    hero_candidate_created = bool(normalized_hero_block and (hero_group_count >= 2 or hero_hard_fallback_triggered))
    log_pipeline_event(
        "hero_block_analysis",
        normalized_hero_block,
        extracted_h1=(extraction_debug or {}).get("main_title", ""),
        first_paragraph=(extraction_debug or {}).get("first_paragraph_after_title", ""),
        combined_hero_block=normalized_hero_block,
        signal_groups_matched={k: v for k, v in hero_signals.items() if v},
        hard_fallback_triggered=hero_hard_fallback_triggered,
        candidate_created=hero_candidate_created,
    )

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

    generic_candidate_created = False
    generic_candidate_filtered = False

    prioritized_chunks = chunks[:]
    if normalized_hero_block:
        if normalized_hero_block not in prioritized_chunks:
            prioritized_chunks.insert(0, normalized_hero_block)
        else:
            prioritized_chunks = [normalized_hero_block] + [c for c in prioritized_chunks if c != normalized_hero_block]

    pre_filter_candidates: List[str] = []
    filtered_after_materiality: List[str] = []

    for chunk in prioritized_chunks:
        if is_asset_or_image_text(chunk):
            continue
        matching_blocks = [block for block, _ in block_signals if chunk in block or block in chunk]
        relevant_block = max(matching_blocks, key=len) if matching_blocks else _normalize_block_text(chunk)
        pre_filter_candidates.append(relevant_block)
        taxonomy_signal = taxonomy_signal_for_block(relevant_block)
        tier2_candidate = bool(taxonomy_signal["tier2"])
        matched_groups = hero_signal_groups(relevant_block)
        matched_group_count = sum(1 for values in matched_groups.values() if values)
        signal_candidate = matched_group_count >= 2
        is_hero_block = bool(normalized_hero_block and _normalize_block_text(relevant_block).lower() == normalized_hero_block.lower())
        hard_hero_fallback_candidate = bool(is_hero_block and HERO_HARD_FALLBACK.search(relevant_block))
        fallback_candidate = bool(
            signal_candidate
            or ("sustainable components" in relevant_block.lower() or "better future" in relevant_block.lower())
            or hard_hero_fallback_candidate
            or (SUSTAINABILITY_FALLBACK.search(relevant_block) and SUSTAINABILITY_FRAMING.search(relevant_block))
        )
        if materiality_score(chunk, page_url) < 3 and not tier2_candidate and not fallback_candidate and not is_hero_block:
            log_pipeline_event("filtered_materiality", relevant_block, tier2=tier2_candidate, fallback=fallback_candidate)
            filtered_after_materiality.append(relevant_block)
            continue
        should_trigger_llm = bool(taxonomy_signal["trigger_llm"] or fallback_candidate)
        log_pipeline_event(
            "candidate_selected",
            relevant_block,
            tier2=tier2_candidate,
            signal_groups={k: v for k, v in matched_groups.items() if v},
            signal_group_count=matched_group_count,
            fallback=fallback_candidate,
            trigger_llm=should_trigger_llm,
        )

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
        generic_gate = (generic_match or should_trigger_llm)
        claim_subject_gate = has_claim_subject or tier2_candidate or fallback_candidate
        blocked = (
            has_generic_substantiation
            or has_absolute_claim
            or is_third_party_context
            or (is_editorial_context and not commercial_context)
        )

        if generic_gate and claim_subject_gate and not blocked:
            generic_candidate_created = True
            log_pipeline_event("sent_to_llm", relevant_block, category="GENERIC_ENVIRONMENTAL_CLAIMS")
            claim_text = generic_match.group(0) if generic_match else "broad sustainability framing"
            if not generic_match and taxonomy_signal["tier2"]:
                claim_text = sorted(taxonomy_signal["tier2"])[0]
            severity = "medium"
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
        elif generic_gate:
            generic_candidate_filtered = True
            log_pipeline_event(
                "filtered_after_candidate",
                relevant_block,
                has_generic_substantiation=has_generic_substantiation,
                has_absolute_claim=has_absolute_claim,
                claim_subject_gate=claim_subject_gate,
                is_third_party_context=is_third_party_context,
                is_editorial_context=is_editorial_context,
                commercial_context=commercial_context,
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

    LOGGER.warning(
        "[pipeline:generic_claim_status] created=%s, filtered_out=%s",
        generic_candidate_created,
        generic_candidate_filtered,
    )
    final_rendered_issues = [clean_snippet(issue.evidence) for issue in issues]
    debug_payload = {
        "extracted_h1": (extraction_debug or {}).get("main_title", ""),
        "extracted_first_paragraph": (extraction_debug or {}).get("first_paragraph_after_title", ""),
        "hero_block": normalized_hero_block,
        "hero_candidate_created": hero_candidate_created,
        "hero_hard_fallback_triggered": hero_hard_fallback_triggered,
        "hero_raw_candidate": normalized_hero_block if hero_candidate_created else "",
        "candidates_before_filtering": pre_filter_candidates,
        "filtered_out_candidates_after_filtering": filtered_after_materiality,
        "final_rendered_issues": final_rendered_issues,
    }
    if extraction_debug is not None:
        extraction_debug["claim_pipeline_debug"] = debug_payload
    LOGGER.warning("[pipeline:debug] %s", json.dumps(debug_payload, ensure_ascii=False))
    return issues


def scan_site(start_url: str, max_pages: int = 10) -> Tuple[int, List[Finding], Dict[str, object]]:
    start_url = normalize_url(start_url)
    if not start_url:
        return 0, [], {}

    session = requests.Session()
    html = fetch_html(start_url, session=session)
    if not html:
        return 1, [], {}
    main_text, extraction_debug = _extract_main_article_text(html)
    if not main_text:
        main_text = html_to_text(html)
    LOGGER.warning(
        (
            "[pipeline:extraction] title=%s intro=%s main_content_length=%s intro_captured=%s "
            "found_sustainable_components=%s found_better_future=%s related_articles_excluded=%s"
        ),
        extraction_debug.get("main_title"),
        extraction_debug.get("first_paragraph_after_title"),
        extraction_debug.get("main_content_length"),
        extraction_debug.get("intro_captured"),
        extraction_debug.get("found_sustainable_components"),
        extraction_debug.get("found_better_future"),
        extraction_debug.get("related_articles_excluded"),
    )
    text = main_text
    all_findings: List[Finding] = find_issues_on_page(
        start_url,
        text,
        hero_block=str(extraction_debug.get("hero_block", "") or ""),
        extraction_debug=extraction_debug,
    )

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
    return 1, high_findings + medium_findings, extraction_debug


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

    pages_scanned, findings_obj, extraction_debug = scan_site(target, max_pages=max_pages_int)
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
            for phrase in ["responsible digital future", "better future", "sustainable components", "sustainable solutions"]:
                if phrase in f.evidence.lower():
                    detected_keywords.insert(0, phrase)
            if "sustainable components" in f.evidence.lower():
                detected_keywords.insert(0, "sustainable components")
            if "better future" in f.evidence.lower():
                detected_keywords.insert(0, "better future")
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
        "debug_data": extraction_debug.get("claim_pipeline_debug", {}),
    }

    return templates.TemplateResponse("report.html", context)


@app.get("/health")
async def health():
    return {"ok": True}
