from __future__ import annotations

import html
import json
import asyncio
import inspect
import logging
import os
import re
import shutil
import subprocess
import string
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
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

MATERIAL_TARGET = re.compile(
    r"(?is)\b(aim\s*to|target|commit|pledge|plan\s*to|will|shall|reduce|cut|lower|decrease)\b"
    r".{0,180}?\b(emissions?|ghg|greenhouse\s*gas|co2|co2e|carbon)\b"
    r".{0,160}?(\d{1,3}\s*(%|percent))"
    r".{0,140}?\b(by|before|in)\s*(20\d{2})\b"
)

FORWARD_LOOKING_ENV_TARGET = re.compile(
    r"(?is)\b("
    r"(?:reduce|cut|lower|decrease)\s+(?:our\s+)?(?:ghg|greenhouse\s+gas|emissions?|co2|carbon).{0,90}?\d{1,3}\s*(?:%|percent).{0,50}?\bby\s*(20\d{2})\b|"
    r"(?:net\s*zero|climate\s*neutral|carbon\s*neutral).{0,40}?\bby\s*(20\d{2})\b|"
    r"(?:target|commit|pledge|aim).{0,100}?\b(?:net\s*zero|climate\s*neutral|carbon\s*neutral|"
    r"(?:\d{1,3}\s*(?:%|percent).{0,30}?(?:emissions?|ghg|co2|carbon))).{0,40}?\bby\s*(20\d{2})\b"
    r")"
)

ENV_TARGET_ACTION = re.compile(
    r"\b(reduce|cut|lower|decrease|achieve|reach|become|transition\s+to|align\s+with|decarboni[sz]e|eliminate|phase\s+out)\b",
    re.I,
)
ENV_TARGET_SUBJECT = re.compile(
    r"\b(greenhouse\s+gas\s+emissions?|ghg\s+emissions?|co2\s+emissions?|carbon\s+emissions?|carbon\s+footprint|"
    r"climate\s+impact|energy\s+use|renewable\s+(?:energy|electricity)|plastic|waste|water\s+use|"
    r"biodiversity\s+impact|packaging\s+impact|scope\s*1|scope\s*2|scope\s*3|emissions?)\b",
    re.I,
)
ENV_TARGET_QUANT_OR_ABS = re.compile(
    r"\b(\d{1,3}\s*(?:%|percent)|net\s*zero|carbon\s*neutral|climate\s*neutral|zero\s*waste|"
    r"100%\s*renewable|fully\s*recyclable|plastic[-\s]?free)\b",
    re.I,
)
ENV_TARGET_DEADLINE = re.compile(
    r"\b(?:by|before|in)\s+(?:\d{1,2}\s+[A-Za-z]+\s+)?(20\d{2})\b",
    re.I,
)

BASELINE_SIGNAL = re.compile(r"\b(baseline\s*year|base\s*year|reference\s*year|referentiejaar|basisjaar)\b", re.I)
PROGRESS_SIGNAL = re.compile(r"\b(progress|on\s+track|achieved|reduced\s+by|year[-\s]?on[-\s]?year)\b", re.I)
SCOPE_SIGNAL = re.compile(r"\b(scope\s*1|scope\s*2|scope\s*3|boundary|inventory\s+boundary)\b", re.I)
METHOD_SIGNAL = re.compile(
    r"\b(ghg\s*protocol|sbti|science[-\s]?based|iso\s*14064|lca|methodolog(?:y|ie)|standard|green\s*deal|environmental\s+regulations?)\b",
    re.I,
)
MILESTONE_SIGNAL = re.compile(r"\b(interim\s+target|milestone|roadmap|transition\s+plan|phase)\b", re.I)
IMPLEMENTATION_SIGNAL = re.compile(r"\b(implementation|measure|action\s+plan|capex|opex|investment|program(?:me)?)\b", re.I)
GOVERNANCE_SIGNAL = re.compile(r"\b(governance|board|owner|accountab(?:le|ility)|responsib(?:le|ility))\b", re.I)
CADENCE_SIGNAL = re.compile(r"\b(report(?:ing)?\s+(?:cadence|annually|quarterly)|annual\s+update|disclose)\b", re.I)
VERIFICATION_SIGNAL = re.compile(r"\b(independent\s+verification|assurance|audit|verified|certifi(?:ed|cation))\b", re.I)

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

NON_ENVIRONMENTAL_SUSTAINABLE_CONTEXT = re.compile(
    r"\b(career|hr|workforce|employment|people|worker)\b",
    re.I,
)

REPORT_REFERENCE_HINT = re.compile(
    r"\b(esg\s*report|sustainability\s*report|see\s*report|learn\s*more|download\s*report|annual\s*report)\b|\.pdf\b",
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
    r"sdgs?|sustainable\s+development\s+goals?|"
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
    r"teaser|promo|footer|breadcrumb|newsletter|menu|navigation|cross[-_\s]?link|widget)",
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
    "FUTURE_TARGET": (
        "FUTURE_TARGET: This is a forward-looking environmental target (emissions reduction by 2030). Under the EmpCo Directive, such claims must "
        "be supported by a clear, publicly available and verifiable implementation plan (e.g., defined baseline year and scope, interim milestones, "
        "measures/actions, governance, and independent verification such as SBTi validation). In the provided page text, the target is stated but no "
        "concrete, independently verified roadmap is included alongside the claim."
    ),
    "FUTURE_NET_ZERO_TARGETS": (
        "This is a forward-looking environmental performance target. Under EmpCo/ECGT rules, future environmental claims must "
        "be supported by a clear, publicly available and verifiable implementation plan. The claim includes an environmental target "
        "and a future deadline, but the scanned content does not provide sufficient substantiation such as baseline year, scope or "
        "boundary, methodology, interim milestones, implementation measures, governance, reporting cadence, or independent verification."
    ),
    "SUSTAINABILITY_LABELS": (
        "Detected Rule Violation: This sustainability seal/label appears self-created or insufficiently linked to a recognised "
        "independent certification scheme."
    ),
}

CATEGORY_LABELS = {
    "GENERIC_ENVIRONMENTAL_CLAIMS": "Generic Environmental Claim",
    "CARBON_NEUTRALITY_CLAIMS": "Carbon Neutrality Claim",
    "FUTURE_TARGET": "Forward-looking environmental performance target",
    "FUTURE_NET_ZERO_TARGETS": "Forward-looking environmental performance target",
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
PIPELINE_DEBUG_ENABLED = os.getenv("GREENCHECK_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
ADMIN_DEBUG_ENABLED = os.getenv("GREENCHECK_ADMIN_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
RENDER_WARNING = "Page could not be fully rendered due to access restrictions"

DEFAULT_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)
FALLBACK_BROWSER_USER_AGENTS = [
    DEFAULT_BROWSER_USER_AGENT,
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
    ),
]


def _classify_playwright_error(exc: Exception) -> str:
    message = str(exc).lower()
    if any(token in message for token in ("executable doesn't exist", "browser has not been found", "playwright install")):
        return "browser_not_found"
    if any(token in message for token in ("timeout", "timed out")):
        return "timeout"
    if any(token in message for token in ("permission denied", "eacces", "operation not permitted")):
        return "permission_issue"
    if any(
        token in message
        for token in (
            "error while loading shared libraries",
            "no such file or directory",
            "depends on",
            "libx",
            "libgtk",
            "libnss3",
            "libatk",
        )
    ):
        return "missing_dependency"
    if any(token in message for token in ("crash", "crashed", "target closed", "browser has been closed")):
        return "crash"
    return "unknown"


def _detect_chromium_executable() -> Optional[str]:
    explicit_path = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
    if explicit_path:
        return explicit_path
    for binary_name in ("chromium", "chromium-browser"):
        detected = shutil.which(binary_name)
        if detected:
            return detected
    return None


def get_git_commit_hash() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, timeout=2).strip()
    except Exception:
        return "unknown"


async def extract_rendered_text_with_playwright(
    url: str,
    timeout_ms: int = 18000,
    user_agents: Optional[List[str]] = None,
) -> Dict[str, Any]:
    LOGGER.warning("PLAYWRIGHT START")
    result: Dict[str, Any] = {
        "text": "",
        "playwright_used": True,
        "playwright_error": None,
        "chromium_path": None,
    }
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        error_type = _classify_playwright_error(exc)
        LOGGER.warning(
            "[extraction] mode=PLAYWRIGHT unavailable error_type=%s error=%s",
            error_type,
            exc,
        )
        result["playwright_error"] = f"{error_type}: {exc}"
        return result

    ua_pool = [ua for ua in (user_agents or FALLBACK_BROWSER_USER_AGENTS) if ua]
    if not ua_pool:
        ua_pool = [DEFAULT_BROWSER_USER_AGENT]

    try:
        async with async_playwright() as p:
            chromium_executable: Optional[str] = _detect_chromium_executable()
            result["chromium_path"] = chromium_executable
            launch_kwargs: Dict[str, Any] = {
                "headless": True,
                "args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
            }
            if chromium_executable:
                launch_kwargs["executable_path"] = chromium_executable
                LOGGER.warning("[extraction] mode=PLAYWRIGHT using_system_chromium=%s", chromium_executable)
            else:
                LOGGER.warning("[extraction] mode=PLAYWRIGHT using_system_chromium=not_found")
            browser = await p.chromium.launch(**launch_kwargs)
            LOGGER.warning("browser launched successfully")
            try:
                for attempt, ua in enumerate(ua_pool, start=1):
                    context = await browser.new_context(
                        user_agent=ua,
                        viewport={"width": 1280, "height": 800},
                        java_script_enabled=True,
                        locale="en-US",
                        extra_http_headers={
                            "Accept-Language": "en-US,en;q=0.9",
                            "Accept": "text/html,application/xhtml+xml",
                            "Connection": "keep-alive",
                        },
                    )
                    try:
                        page = await context.new_page()
                        goto_error: Optional[Exception] = None
                        try:
                            response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                            LOGGER.warning("page.goto success")
                            status = response.status if response else "unknown"
                            LOGGER.warning("[extraction] mode=PLAYWRIGHT status=%s ua_attempt=%s", status, attempt)
                        except Exception as exc:
                            goto_error = exc
                            error_type = _classify_playwright_error(exc)
                            LOGGER.warning(
                                "[extraction] mode=PLAYWRIGHT goto_timeout_or_failure ua_attempt=%s error_type=%s error=%s",
                                attempt,
                                error_type,
                                exc,
                            )
                        await page.wait_for_timeout(5000)
                        inner_text = await page.evaluate("document.body.innerText")
                        normalized = normalize_extracted_text(inner_text)
                        if normalized:
                            LOGGER.warning("PLAYWRIGHT SUCCESS")
                            LOGGER.warning("[extraction] extracted_text_length=%s", len(normalized))
                            LOGGER.warning("[extraction] extracted_text_preview_500=%s", normalized[:500])
                            result["text"] = normalized
                            result["playwright_error"] = None
                            return result
                        if goto_error is not None:
                            error_type = _classify_playwright_error(goto_error)
                            result["playwright_error"] = f"{error_type}: {goto_error}"
                    except Exception as exc:
                        error_type = _classify_playwright_error(exc)
                        LOGGER.warning("page extraction failure")
                        LOGGER.warning(
                            "[extraction] mode=PLAYWRIGHT failed ua_attempt=%s error_type=%s error=%s",
                            attempt,
                            error_type,
                            exc,
                        )
                        result["playwright_error"] = f"{error_type}: {exc}"
                    finally:
                        await context.close()
            finally:
                await browser.close()
    except Exception as exc:
        error_type = _classify_playwright_error(exc)
        LOGGER.warning(
            "[extraction] mode=PLAYWRIGHT crashed_before_extract=true error_type=%s error=%s",
            error_type,
            exc,
        )
        result["playwright_error"] = f"{error_type}: {exc}"
        return result
    LOGGER.warning("[extraction] mode=PLAYWRIGHT extracted_text_empty=true")
    return result


async def _invoke_playwright_extractor(start_url: str) -> object:
    maybe_result = extract_rendered_text_with_playwright(start_url)
    if inspect.isawaitable(maybe_result):
        return await maybe_result
    return maybe_result


def _coerce_playwright_result(playwright_result: object) -> Tuple[str, Optional[str]]:
    if isinstance(playwright_result, dict):
        text = str(playwright_result.get("text", "") or "")
        playwright_error = playwright_result.get("playwright_error")
        return text, str(playwright_error) if playwright_error else None
    if isinstance(playwright_result, str):
        return playwright_result, None
    return "", None


def build_future_target_debug(text: str) -> Dict[str, object]:
    windows = build_candidate_windows(text)
    matched_windows = [
        w for w in windows if (FORWARD_LOOKING_ENV_TARGET.search(w) or MATERIAL_TARGET.search(w) or is_forward_environmental_target(w))
    ]
    return {
        "git_commit_hash": get_git_commit_hash(),
        "extracted_text_length": len(text or ""),
        "extracted_text_preview": (text or "")[:3000],
        "contains_greenhouse_gas": "greenhouse gas" in (text or "").lower(),
        "contains_55_percent": bool(re.search(r"55\\s*%", text or "", re.I)),
        "contains_2030": "2030" in (text or ""),
        "contains_reduce_greenhouse_gas_emissions": "reduce greenhouse gas emissions" in (text or "").lower(),
        "future_target_candidate_windows": windows,
        "future_target_match_result": bool(matched_windows),
        "future_target_matched_windows": matched_windows,
    }


def log_pipeline_event(stage: str, block: str, **details: object) -> None:
    if not PIPELINE_DEBUG_ENABLED:
        return
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


def fetch_html(url: str, session: requests.Session, timeout: int = 18) -> Tuple[Optional[str], Optional[int]]:
    headers = {
        "User-Agent": DEFAULT_BROWSER_USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml",
        "Connection": "keep-alive",
    }
    try:
        r = session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        LOGGER.warning("[extraction] mode=STATIC status=%s", r.status_code)
        if r.status_code >= 400:
            return None, r.status_code
        ct = (r.headers.get("content-type") or "").lower()
        if "text/html" not in ct and "application/xhtml" not in ct:
            return None, r.status_code
        return r.text, r.status_code
    except Exception as exc:
        LOGGER.warning("[extraction] mode=STATIC status=request_error error=%s", exc)
        return None, None


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

    visible_tags = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "span", "li", "section", "article"}
    blocks: List[str] = []
    for node in best.find_all(list(visible_tags)):
        if _is_excluded_container(node):
            continue
        if any(_is_excluded_container(parent) for parent in node.parents if getattr(parent, "name", None)):
            continue
        if first_intro_node is not None and node == first_intro_node:
            continue
        if node.name in {"div", "section", "article"} and node.find(list(visible_tags)):
            continue
        if node.name == "span" and node.find(["span", "a", "strong", "em", "small"]):
            continue
        text = node.get_text(" ", strip=True)
        if len(text) >= 12:
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
        "visible_text_blocks": ordered,
    }
    if PIPELINE_DEBUG_ENABLED:
        LOGGER.warning("[pipeline:visible_text_blocks] %s", json.dumps(ordered, ensure_ascii=False))
    return main_text, debug


def make_chunks(text: str) -> List[str]:
    if not text:
        return []
    return [sentence for sentence in sentence_tokenize(text) if len(sentence) >= 25]


def clean_snippet(s: str) -> str:
    parsed = BeautifulSoup(html.unescape(s or ""), "html.parser")
    snippet = parsed.get_text(separator=" ")
    snippet = re.sub(r"\s+", " ", snippet).strip()
    snippet = re.sub(r"\s+([:;,.!?])", r"\1", snippet)
    snippet = re.sub(r"[^\x20-\x7E\u00A0-\u024F\u2018-\u201F€£¥]", "", snippet)
    return snippet


def _cleanup_claim_text(text: str) -> str:
    cleaned = clean_snippet(text)
    cleaned = re.sub(r"\b(Home|Menu|Search|Contact|Privacy|Cookie settings)\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    words = cleaned.split()
    compact_words: List[str] = []
    for word in words:
        if len(compact_words) >= 2 and compact_words[-1].lower() == word.lower() and compact_words[-2].lower() == word.lower():
            continue
        compact_words.append(word)
    cleaned = " ".join(compact_words)
    return cleaned


def _truncate_snippet_with_ellipses(text: str, *, max_len: int = 330) -> str:
    cleaned = _cleanup_claim_text(text)
    if len(cleaned) > max_len:
        cut = cleaned[:max_len].rsplit(" ", 1)[0].strip()
        cleaned = cut or cleaned[:max_len]
    return f"...{cleaned}..." if cleaned else ""


FUTURE_TARGET_DISPLAY_KEYWORDS = [
    "Our ESG Ambitions",
    "Reduce greenhouse gas emissions",
    "greenhouse gas emissions",
    "55%",
    "2030",
]


def make_display_snippet(text: str, keywords: List[str], max_len: int = 350) -> str:
    clean = re.sub(r"\s+", " ", clean_snippet(text or "")).strip()
    if not clean:
        return ""
    lower = clean.lower()
    hit = -1
    for kw in keywords:
        hit = lower.find(kw.lower())
        if hit >= 0:
            break
    if hit < 0:
        snippet = clean[:max_len]
    else:
        start = max(0, hit - 80)
        end = min(len(clean), hit + 260)
        snippet = clean[start:end]
    snippet = snippet.strip()
    if len(snippet) > max_len:
        snippet = snippet[:max_len].rsplit(" ", 1)[0] or snippet[:max_len]
    return f"...{snippet}..."


def _future_target_snippet_score(candidate: str) -> int:
    normalized = (candidate or "").lower()
    score = 0
    if "reduce greenhouse gas emissions" in normalized:
        score += 6
    if "55%" in normalized or "55 %" in normalized:
        score += 4
    if "2030" in normalized:
        score += 4
    if "greenhouse" in normalized and "emissions" in normalized:
        score += 2
    return score


def choose_issue_snippet(category: str, evidence: str, sentence_blocks: List[str]) -> str:
    if category in {"FUTURE_TARGET", "FUTURE_NET_ZERO_TARGETS"}:
        preferred_candidate = _cleanup_claim_text(evidence)
        evidence_hint = preferred_candidate.lower()
        has_future_target_hint = any(token in evidence_hint for token in ["greenhouse", "emissions", "55%", "55 %", "2030"])
        if has_future_target_hint:
            candidates = [block for block in sentence_blocks if len(_cleanup_claim_text(block)) >= 30]
            scored = sorted(
                candidates,
                key=lambda c: (_future_target_snippet_score(c), -abs(len(_cleanup_claim_text(c)) - 280)),
                reverse=True,
            )
            if scored and _future_target_snippet_score(scored[0]) >= 8:
                preferred_candidate = scored[0]
        return _truncate_snippet_with_ellipses(preferred_candidate)
    return _cleanup_claim_text(evidence)


def make_sentence_blocks(text: str) -> List[str]:
    normalized = (text or "").strip()
    paragraphs = [_normalize_block_text(p) for p in re.split(r"\n+", normalized) if _normalize_block_text(p)]
    blocks: List[str] = []
    seen: Set[str] = set()
    for paragraph in paragraphs:
        key = normalize_claim_text(paragraph)
        if key and key not in seen:
            seen.add(key)
            blocks.append(paragraph)
    merged_paragraphs = _normalize_block_text(" ".join(paragraphs))
    merged_key = normalize_claim_text(merged_paragraphs)
    if merged_paragraphs and merged_key and merged_key not in seen:
        seen.add(merged_key)
        blocks.append(merged_paragraphs)
    for sentence in sentence_tokenize(text):
        key = normalize_claim_text(sentence)
        if key and key not in seen:
            seen.add(key)
            blocks.append(sentence)
    if blocks:
        return blocks
    return [normalized] if normalized else []


def build_candidate_windows(text: str) -> List[str]:
    blocks = [_normalize_block_text(p) for p in re.split(r"\n+", text or "") if _normalize_block_text(p)]
    windows: List[str] = []
    seen: Set[str] = set()

    def add_window(value: str) -> None:
        normalized = _normalize_block_text(value)
        if len(normalized) < 20:
            return
        key = normalize_claim_text(normalized)
        if not key or key in seen:
            return
        seen.add(key)
        windows.append(normalized)

    for idx, block in enumerate(blocks):
        add_window(block)
        if idx + 1 < len(blocks):
            add_window(f"{block} {blocks[idx + 1]}")
        if idx > 0:
            add_window(f"{blocks[idx - 1]} {block}")
        if idx > 0 and idx + 1 < len(blocks):
            add_window(f"{blocks[idx - 1]} {block} {blocks[idx + 1]}")
    add_window(" ".join(blocks))
    for sentence in sentence_tokenize(text or ""):
        add_window(sentence)
    if not windows and text:
        add_window(text)
    return windows


def normalize_claim_text(text: str) -> str:
    normalized = (text or "").lower().strip()
    normalized = normalized.translate(str.maketrans("", "", string.punctuation))
    return re.sub(r"\s+", " ", normalized).strip()


def text_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize_claim_text(left), normalize_claim_text(right)).ratio()


def sentence_tokenize(text: str) -> List[str]:
    normalized = (
        (text or "")
        .replace("•", ". ")
        .replace("·", ". ")
        .replace("–", "-")
        .replace("—", "-")
    )
    paragraphs = [p.strip() for p in re.split(r"\n+", normalized) if p.strip()]
    sentences: List[str] = []
    seen: Set[str] = set()
    for paragraph in paragraphs:
        for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
            cleaned = _normalize_block_text(sentence)
            if len(cleaned) < 10:
                continue
            key = normalize_claim_text(cleaned)
            if not key or key in seen:
                continue
            seen.add(key)
            sentences.append(cleaned)
    return sentences


def _normalize_block_text(block: str) -> str:
    return re.sub(r"\s+", " ", (block or "")).strip()


def normalize_extracted_text(text: str) -> str:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [_normalize_block_text(line) for line in normalized.split("\n")]
    deduped_lines: List[str] = []
    seen: Set[str] = set()
    for line in lines:
        if not line:
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped_lines.append(line)
    return _normalize_block_text("\n".join(deduped_lines))


def has_environmental_keyword_signal(text: str) -> bool:
    normalized = (text or "").lower()
    keyword_patterns = [
        CLIMATE_CONTEXT,
        SUSTAINABILITY_CONTEXT,
        re.compile(r"\b(ghg|greenhouse\s*gas|emissions?|carbon|climate|net\s*zero)\b", re.I),
    ]
    return any(pattern.search(normalized) for pattern in keyword_patterns)


STRONG_FUTURE_TARGET_SIGNAL_PATTERNS = [
    re.compile(r"\bgreenhouse\s*gas\b", re.I),
    re.compile(r"\bghg\b", re.I),
    re.compile(r"\bemissions?\b", re.I),
    re.compile(r"\bco2\b", re.I),
    re.compile(r"\bco₂\b", re.I),
    re.compile(r"\bcarbon\b", re.I),
    re.compile(r"\b55\s*%\b", re.I),
    re.compile(r"\b2030\b", re.I),
    re.compile(r"\breduce\s+greenhouse\s+gas\s+emissions\b", re.I),
    re.compile(r"\bnet\s*zero\b", re.I),
    re.compile(r"\bclimate\s*neutral\b", re.I),
    re.compile(r"\bcarbon\s*neutral\b", re.I),
    re.compile(r"\brenewable\s+electricity\b", re.I),
    re.compile(r"\bplastic[-\s]?free\b", re.I),
    re.compile(r"\bzero\s+waste\b", re.I),
]


def has_strong_future_target_signal(text: str) -> bool:
    normalized = text or ""
    return any(pattern.search(normalized) for pattern in STRONG_FUTURE_TARGET_SIGNAL_PATTERNS)


def merge_extracted_text(static_text: str, rendered_text: str) -> str:
    merged_parts = [part for part in [static_text, rendered_text] if (part or "").strip()]
    if not merged_parts:
        return ""
    return normalize_extracted_text("\n".join(merged_parts))


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
    if FORWARD_LOOKING_ENV_TARGET.search(chunk):
        score += 2
    if is_forward_environmental_target(chunk):
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


def is_forward_environmental_target(text: str) -> bool:
    normalized = text or ""
    has_action = bool(ENV_TARGET_ACTION.search(normalized))
    has_subject = bool(ENV_TARGET_SUBJECT.search(normalized))
    has_quant_or_abs = bool(ENV_TARGET_QUANT_OR_ABS.search(normalized))
    has_deadline = bool(ENV_TARGET_DEADLINE.search(normalized))
    score = sum((has_action, has_subject, has_quant_or_abs, has_deadline))

    absolute_with_deadline = bool(
        re.search(
            r"\b(net\s*zero|carbon\s*neutral|climate\s*neutral|zero\s*waste|100%\s*renewable|fully\s*recyclable|plastic[-\s]?free)\b",
            normalized,
            re.I,
        )
        and has_deadline
    )

    return absolute_with_deadline or (has_deadline and score >= 3 and (has_subject or has_quant_or_abs))


def forward_target_substantiation_signal_count(text: str) -> int:
    patterns = [
        BASELINE_SIGNAL,
        PROGRESS_SIGNAL,
        SCOPE_SIGNAL,
        METHOD_SIGNAL,
        MILESTONE_SIGNAL,
        IMPLEMENTATION_SIGNAL,
        GOVERNANCE_SIGNAL,
        CADENCE_SIGNAL,
        VERIFICATION_SIGNAL,
    ]
    return sum(1 for pattern in patterns if pattern.search(text or ""))


def _target_signature(text: str) -> Tuple[str, str, str]:
    normalized = (text or "").lower()
    year_match = re.search(r"\b(20\d{2})\b", normalized)
    pct_match = re.search(r"\b(\d{1,3}\s*(?:%|percent))\b", normalized)
    subject_match = ENV_TARGET_SUBJECT.search(normalized)
    return (
        subject_match.group(0) if subject_match else "",
        year_match.group(1) if year_match else "",
        pct_match.group(1) if pct_match else "",
    )


def find_issues_on_page(page_url: str, text: str, hero_block: str = "", extraction_debug: Optional[Dict[str, object]] = None) -> List[Finding]:
    chunks = build_candidate_windows(text)
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

    sentence_blocks = build_candidate_windows(text)
    has_generic_substantiation = bool(GENERIC_SUBSTANTIATION_HINT.search(text))
    has_external_report_reference = bool(REPORT_REFERENCE_HINT.search(text))
    issues: List[Finding] = []
    seen: Set[Tuple[str, str]] = set()
    seen_claims_by_category: Dict[str, List[str]] = {}
    dedup_events: List[str] = []

    def nearby_forward_substantiation_count(chunk: str) -> int:
        chunk_key = normalize_claim_text(chunk)
        idx = next((i for i, block in enumerate(sentence_blocks) if normalize_claim_text(block) == chunk_key), -1)
        if idx < 0:
            return forward_target_substantiation_signal_count(chunk)
        window = " ".join(sentence_blocks[max(0, idx - 1) : idx + 3])
        return forward_target_substantiation_signal_count(window)

    def add_issue(
        category: str,
        severity: str,
        message: str,
        evidence: str,
        how_to_fix: str,
        llm_prompt: str = "",
    ) -> None:
        snippet = choose_issue_snippet(category, evidence, sentence_blocks)
        normalized = normalize_claim_text(snippet)
        key = (category, normalized)
        if key in seen:
            return
        for idx, existing_issue in enumerate(issues):
            if existing_issue.category != category:
                continue
            existing_norm = normalize_claim_text(existing_issue.evidence)
            is_similar = text_similarity(existing_issue.evidence, snippet) > 0.85
            has_overlap = normalized in existing_norm or existing_norm in normalized
            if category in {"FUTURE_TARGET", "FUTURE_NET_ZERO_TARGETS"}:
                new_sig = _target_signature(snippet)
                old_sig = _target_signature(existing_issue.evidence)
                comparable = new_sig == old_sig and any(new_sig)
                is_similar = text_similarity(existing_issue.evidence, snippet) > 0.93 if comparable else False
                has_overlap = has_overlap and comparable
            if is_similar or has_overlap:
                dedup_events.append(f"merged:{category}")
                if len(normalized) > len(existing_norm):
                    issues[idx] = Finding(
                        category=category,
                        url=page_url,
                        message=message,
                        evidence=snippet.strip(),
                        severity=severity,
                        how_to_fix=how_to_fix,
                        llm_prompt=llm_prompt,
                    )
                    seen.discard((category, existing_norm))
                    seen.add(key)
                return
        existing_claims = seen_claims_by_category.setdefault(category, [])
        if any(text_similarity(existing, evidence) > 0.85 for existing in existing_claims):
            dedup_events.append(f"suppressed_similarity:{category}")
            return
        seen.add(key)
        existing_claims.append(snippet)
        issues.append(
            Finding(
                category=category,
                url=page_url,
                message=message,
                evidence=snippet.strip(),
                severity=severity,
                how_to_fix=how_to_fix,
                llm_prompt=llm_prompt,
            )
        )

    generic_candidate_created = False
    generic_candidate_filtered = False

    prioritized_chunks = sorted(chunks[:], key=_claim_priority)
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
        if (
            materiality_score(chunk, page_url) < 3
            and not tier2_candidate
            and not fallback_candidate
            and not is_hero_block
            and not is_forward_environmental_target(chunk)
        ):
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

        target_match = FORWARD_LOOKING_ENV_TARGET.search(chunk) or MATERIAL_TARGET.search(chunk)
        if PIPELINE_DEBUG_ENABLED and CLIMATE_CONTEXT.search(chunk):
            LOGGER.warning("[pipeline:future_target_candidate] %s", clean_snippet(chunk))
        if target_match or is_forward_environmental_target(chunk):
            pct_match = re.search(r"(\d{1,3}\s*(?:%|percent))", chunk, re.I)
            pct = pct_match.group(1) if pct_match else "stated target"
            year_match = ENV_TARGET_DEADLINE.search(chunk)
            year = year_match.group(1) if year_match else "target year"
            substantiation_signals = max(
                nearby_forward_substantiation_count(chunk),
                forward_target_substantiation_signal_count(text),
            )
            severity = "medium" if substantiation_signals >= 1 else "high"
            message = RULEBOOK["FUTURE_TARGET"]
            add_issue(
                category="FUTURE_NET_ZERO_TARGETS",
                severity=severity,
                message=message,
                evidence=clean_snippet(chunk),
                how_to_fix=(
                    "Add a substantiated transition plan next to the target (or link prominently to it) including: "
                    "(1) baseline year and emissions scopes covered (Scope 1/2/3) and calculation methodology, "
                    "(2) interim targets (e.g., 2026/2028) and specific measures (energy procurement, fleet, travel, supplier engagement), "
                    "(3) progress reporting with KPIs, (4) third-party verification/assurance and/or SBTi validation. "
                    "If such a plan is not available, rephrase to a non-committal statement (e.g., 'we aim to reduce emissions') "
                    "and avoid presenting it as a defined target."
                ),
                llm_prompt=build_llm_prompt(relevant_block, taxonomy_signal),
            )
            if PIPELINE_DEBUG_ENABLED:
                LOGGER.warning("[pipeline:future_target_match] matched=True substantiation_signals=%s", substantiation_signals)
        elif PIPELINE_DEBUG_ENABLED and CLIMATE_CONTEXT.search(chunk):
            LOGGER.warning("[pipeline:future_target_match] matched=False")

        has_future_target = bool(FORWARD_LOOKING_ENV_TARGET.search(chunk) or MATERIAL_TARGET.search(chunk) or is_forward_environmental_target(chunk))
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
        non_environmental_sustainable = bool(
            generic_match
            and generic_match.group(0).lower().startswith("sustainab")
            and NON_ENVIRONMENTAL_SUSTAINABLE_CONTEXT.search(chunk)
        )
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
            or has_external_report_reference
            or has_absolute_claim
            or non_environmental_sustainable
            or is_third_party_context
            or (is_editorial_context and not commercial_context)
        ) and not has_future_target
        if PIPELINE_DEBUG_ENABLED and generic_gate:
            LOGGER.warning(
                "[pipeline:suppression_decision] blocked=%s has_report_ref=%s has_substantiation=%s third_party=%s",
                blocked,
                has_external_report_reference,
                has_generic_substantiation,
                is_third_party_context,
            )

        if generic_gate and claim_subject_gate and not blocked and not has_future_target:
            generic_candidate_created = True
            log_pipeline_event("sent_to_llm", relevant_block, category="GENERIC_ENVIRONMENTAL_CLAIMS")
            claim_text = generic_match.group(0) if generic_match else "broad sustainability framing"
            if not generic_match and taxonomy_signal["tier2"]:
                claim_text = sorted(taxonomy_signal["tier2"])[0]
            severity = "low"
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

    if PIPELINE_DEBUG_ENABLED:
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
        "suppressed_or_deduplicated": dedup_events,
    }
    if extraction_debug is not None:
        extraction_debug["claim_pipeline_debug"] = debug_payload
    if PIPELINE_DEBUG_ENABLED:
        LOGGER.warning("[pipeline:debug] %s", json.dumps(debug_payload, ensure_ascii=False))
    return issues


def scan_site(start_url: str, max_pages: int = 10) -> Tuple[int, List[Finding], Dict[str, object]]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(scan_site_async(start_url, max_pages=max_pages))
    raise RuntimeError("scan_site() cannot be used inside an active event loop; use await scan_site_async(...).")


async def scan_site_async(start_url: str, max_pages: int = 10) -> Tuple[int, List[Finding], Dict[str, object]]:
    start_url = normalize_url(start_url)
    if not start_url:
        return 0, [], {}

    session = requests.Session()
    LOGGER.warning("STATIC FETCH START")
    html, status_code = fetch_html(start_url, session=session)
    LOGGER.warning("HTTP status code=%s", status_code if status_code is not None else "request_error")
    extraction_warnings: List[str] = []
    extraction_mode = "STATIC"
    playwright_used = False
    playwright_error: Optional[str] = None
    chromium_path: Optional[str] = None
    rendered_text = ""
    if not html:
        LOGGER.warning("STATIC FAILED → switching to PLAYWRIGHT")
        extraction_mode = "PLAYWRIGHT"
        playwright_used = True
        if status_code == 403:
            LOGGER.warning("[extraction] fallback_triggered=true reason=static_http_403")
        elif status_code is not None and status_code != 200:
            LOGGER.warning("[extraction] fallback_triggered=true reason=static_non_200_status status=%s", status_code)
        else:
            LOGGER.warning("[extraction] fallback_triggered=true reason=static_fetch_failed")
        playwright_result = await _invoke_playwright_extractor(start_url)
        rendered_text, playwright_error = _coerce_playwright_result(playwright_result)
        chromium_path = playwright_result.get("chromium_path") if isinstance(playwright_result, dict) else None
        if not rendered_text:
            extraction_warnings.append(RENDER_WARNING)
            return 1, [], {
                "warnings": extraction_warnings,
                "http_status": status_code,
                "extraction_mode": extraction_mode,
                "playwright_used": playwright_used,
                "playwright_error": playwright_error,
                "chromium_path": chromium_path,
                "extracted_text_length": 0,
                "greenhouse_gas_in_text": False,
                "contains_55_percent_token": False,
                "contains_2030_token": False,
            }
        rendered_lower = rendered_text.lower()
        extraction_debug = {
            "rendered_fallback_used": True,
            "extraction_mode": extraction_mode,
            "rendered_text_length": len(rendered_text),
            "extracted_text_length": len(rendered_text),
            "greenhouse_gas_in_text": "greenhouse gas" in rendered_lower,
            "contains_55_percent_token": ("55%" in rendered_lower or "55 %" in rendered_lower),
            "contains_2030_token": "2030" in rendered_lower,
            "http_status": status_code,
            "playwright_used": playwright_used,
            "playwright_error": playwright_error,
            "chromium_path": chromium_path,
            "warnings": extraction_warnings,
        }
        LOGGER.warning(
            "[extraction] mode=%s extracted_text_length=%s greenhouse_gas_present=%s contains_55_percent=%s contains_2030=%s chromium_path=%s",
            extraction_debug["extraction_mode"],
            extraction_debug["extracted_text_length"],
            extraction_debug["greenhouse_gas_in_text"],
            extraction_debug["contains_55_percent_token"],
            extraction_debug["contains_2030_token"],
            extraction_debug["chromium_path"] or "not_found",
        )
        extraction_debug["future_target_trace"] = build_future_target_debug(rendered_text)
        findings = find_issues_on_page(start_url, rendered_text, extraction_debug=extraction_debug)
        return 1, findings, extraction_debug
    main_text, extraction_debug = _extract_main_article_text(html)
    if not main_text:
        main_text = html_to_text(html)
    main_text = normalize_extracted_text(main_text)
    fallback_reason: List[str] = []
    if status_code is not None and status_code != 200:
        fallback_reason.append(f"non_200_status_{status_code}")
    if len(main_text) < 2000:
        fallback_reason.append("short_static_text")
    if not has_environmental_keyword_signal(main_text):
        fallback_reason.append("missing_environmental_keywords")

    if not has_strong_future_target_signal(main_text):
        fallback_reason.append("missing_strong_future_target_signal")

    if fallback_reason:
        LOGGER.warning("STATIC FAILED → switching to PLAYWRIGHT")
        LOGGER.warning("[extraction] fallback_triggered=true reason=%s", ",".join(fallback_reason))
        extraction_mode = "PLAYWRIGHT_FALLBACK" if not main_text else "STATIC+PLAYWRIGHT"
        playwright_used = True
        playwright_result = await _invoke_playwright_extractor(start_url)
        rendered_text, playwright_error = _coerce_playwright_result(playwright_result)
        chromium_path = playwright_result.get("chromium_path") if isinstance(playwright_result, dict) else None
        if rendered_text:
            main_text = merge_extracted_text(main_text, rendered_text)
            extraction_debug["rendered_fallback_used"] = True
            extraction_debug["rendered_fallback_reason"] = fallback_reason
        else:
            extraction_warnings.append(RENDER_WARNING)
    extraction_debug["extraction_mode"] = extraction_mode
    extraction_debug["warnings"] = extraction_warnings
    extraction_debug["http_status"] = status_code
    extraction_debug["playwright_used"] = playwright_used
    extraction_debug["playwright_error"] = playwright_error
    extraction_debug["chromium_path"] = chromium_path
    extraction_debug["rendered_text_length"] = len(rendered_text or "")
    extraction_debug["extracted_text_length"] = len(main_text or "")
    extraction_debug["greenhouse_gas_in_text"] = "greenhouse gas" in (main_text or "").lower()
    lower_text = (main_text or "").lower()
    contains_greenhouse = "greenhouse" in lower_text
    contains_emissions = "emissions" in lower_text
    contains_55 = "55%" in lower_text or "55 %" in lower_text
    contains_2030 = "2030" in lower_text
    extraction_debug["contains_55_percent_token"] = contains_55
    extraction_debug["contains_2030_token"] = contains_2030
    extraction_debug["contains_target_claim"] = bool(contains_greenhouse and contains_emissions and contains_55 and contains_2030)
    LOGGER.warning(
        "[extraction] target_claim_tokens greenhouse=%s emissions=%s 55_percent=%s 2030=%s",
        contains_greenhouse,
        contains_emissions,
        contains_55,
        contains_2030,
    )
    LOGGER.warning("[extraction] extracted_text_preview_500=%s", (main_text or "")[:500])
    LOGGER.warning(
        "[extraction] mode=%s extracted_text_length=%s greenhouse_gas_present=%s contains_55_percent=%s contains_2030=%s chromium_path=%s fallback_reason=%s",
        extraction_debug["extraction_mode"],
        extraction_debug["extracted_text_length"],
        extraction_debug["greenhouse_gas_in_text"],
        extraction_debug["contains_55_percent_token"],
        extraction_debug["contains_2030_token"],
        extraction_debug["chromium_path"] or "not_found",
        ",".join(fallback_reason) if fallback_reason else "n/a",
    )
    if PIPELINE_DEBUG_ENABLED:
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
    future_debug = build_future_target_debug(text)
    extraction_debug["future_target_trace"] = future_debug
    LOGGER.warning(
        "[future-target-trace] commit=%s text_len=%s contains_greenhouse_gas=%s contains_55_percent=%s contains_2030=%s contains_reduce_phrase=%s matched=%s",
        future_debug["git_commit_hash"],
        future_debug["extracted_text_length"],
        future_debug["contains_greenhouse_gas"],
        future_debug["contains_55_percent"],
        future_debug["contains_2030"],
        future_debug["contains_reduce_greenhouse_gas_emissions"],
        future_debug["future_target_match_result"],
    )
    LOGGER.warning("[future-target-trace] extracted_preview=%s", future_debug["extracted_text_preview"])
    LOGGER.warning("[future-target-trace] candidate_windows=%s", json.dumps(future_debug["future_target_candidate_windows"], ensure_ascii=False))

    all_findings: List[Finding] = find_issues_on_page(
        start_url,
        text,
        hero_block=str(extraction_debug.get("hero_block", "") or ""),
        extraction_debug=extraction_debug,
    )

    dedup = {}
    dropped_by_dedup = []
    for finding in all_findings:
        normalized_evidence = re.sub(r"\s+", " ", finding.evidence.lower()).strip()
        key = (finding.category, normalized_evidence[:180])
        if key in dedup:
            dropped_by_dedup.append(f"{finding.category}:{finding.evidence[:80]}")
        dedup[key] = finding
    findings = list(dedup.values())
    future_findings = [f for f in findings if f.category in {"FUTURE_TARGET", "FUTURE_NET_ZERO_TARGETS"}]
    if not future_findings:
        candidate_future = [f for f in all_findings if f.category in {"FUTURE_TARGET", "FUTURE_NET_ZERO_TARGETS"}]
        if candidate_future:
            findings.append(candidate_future[0])
            LOGGER.warning("[future-target-trace] restored_future_target_after_dedup=true")
    extraction_debug["dedup_suppression"] = dropped_by_dedup
    LOGGER.warning("[future-target-trace] suppressed_or_deduplicated=%s", json.dumps(dropped_by_dedup, ensure_ascii=False))

    severity_rank = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (severity_rank.get(f.severity, 3), f.category, f.url))

    high_findings = [f for f in findings if f.severity == "high"][:5]
    medium_findings = [f for f in findings if f.severity == "medium"][:8]
    low_findings = [f for f in findings if f.severity == "low"][:4]
    return 1, high_findings + medium_findings + low_findings, extraction_debug


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

    pages_scanned, findings_obj, extraction_debug = await scan_site_async(target, max_pages=max_pages_int)
    risk = calc_risk_score(findings_obj)

    def to_template_finding(f: Finding) -> dict:
        readable_source = is_readable_http_url(f.url)
        rule_text = RULEBOOK.get(f.category, "Detected Rule Violation: The claim is not sufficiently clear, accurate, and verifiable.")
        display_keywords = FUTURE_TARGET_DISPLAY_KEYWORDS if f.category in {"FUTURE_TARGET", "FUTURE_NET_ZERO_TARGETS"} else []
        display_snippet = make_display_snippet(f.evidence, display_keywords, max_len=350)
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
            "title": CATEGORY_LABELS.get(f.category, f.category.replace("_", " ").title()),
            "severity": f.severity,
            "display_snippet": display_snippet,
            "page_url": f.url,
            "page_url_readable": readable_source,
            "page_url_label": (
                f.url if readable_source else "Source page URL unreadable (invalid or incoherent link in scan result)."
            ),
            "message": f.message,
            "rule": rule_text,
            "recommendation": f.how_to_fix,
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
        "warnings": extraction_debug.get("warnings", []),
    }

    return templates.TemplateResponse("report.html", context)


@app.get("/debug/scan-json")
async def debug_scan_json(url: str, max_pages: int = 10):
    if not ADMIN_DEBUG_ENABLED:
        return {"enabled": False}
    target = normalize_url(url)
    pages_scanned, findings_obj, extraction_debug = await scan_site_async(target, max_pages=max_pages)
    claim_debug = extraction_debug.get("claim_pipeline_debug", {}) if isinstance(extraction_debug, dict) else {}
    future_debug = extraction_debug.get("future_target_trace", {}) if isinstance(extraction_debug, dict) else {}
    return {
        "enabled": True,
        "extractionMode": extraction_debug.get("extraction_mode", "STATIC"),
        "httpStatus": extraction_debug.get("http_status"),
        "playwrightUsed": bool(extraction_debug.get("playwright_used", False)),
        "playwrightError": extraction_debug.get("playwright_error"),
        "extractedTextLength": int(extraction_debug.get("extracted_text_length", 0) or 0),
        "containsTargetClaim": bool(extraction_debug.get("contains_target_claim", False)),
        "pages_scanned": pages_scanned,
        "extractedText": future_debug.get("extracted_text_preview", ""),
        "candidateWindows": future_debug.get("future_target_candidate_windows", []),
        "matchedRules": [f.category for f in findings_obj],
        "suppressedRules": claim_debug.get("suppressed_or_deduplicated", []),
        "warnings": extraction_debug.get("warnings", []),
    }


@app.head("/")
async def home_head():
    return {"status": "ok"}


@app.get("/health")
async def health():
    return {"status": "ok"}
