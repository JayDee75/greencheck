import re
from collections import deque
from dataclasses import dataclass, asdict
from typing import List, Set
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Durably GreenCheck")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- EmpCo red flag patterns ---
VAGUE_GREEN = [r"\beco\b", r"\bduurzaam\b", r"\bmilieuvriendelijk\b", r"\bgreen\b"]
CARBON_NEUTRAL = [r"\bklimaatneutraal\b", r"\bco2[- ]?neutraal\b", r"\bcarbon neutral\b"]
OFFSETS = [r"\boffset\b", r"\bcompensatie\b", r"\bcarbon credits?\b"]

@dataclass
class Finding:
    category: str
    severity: str
    message: str
    evidence: str
    url: str

def fetch_html(url):
    try:
        r = requests.get(url, timeout=10)
        if "text/html" in r.headers.get("content-type",""):
            return r.text
    except:
        return None

def extract_text_and_links(base_url, html):
    soup = BeautifulSoup(html, "lxml")
    text = " ".join(soup.get_text().split())
    links = [urljoin(base_url, a.get("href")) for a in soup.select("a[href]")]
    return text, links

def run_rules(url, text):
    findings = []

    for pattern in VAGUE_GREEN:
        for m in re.finditer(pattern, text, re.I):
            findings.append(Finding("Vage claim", "medium", "Vage duurzaamheidsclaim", text[m.start():m.end()+60], url))

    for pattern in CARBON_NEUTRAL:
        for m in re.finditer(pattern, text, re.I):
            findings.append(Finding("Klimaatneutraal claim", "high", "Risicovolle claim", text[m.start():m.end()+60], url))

    for pattern in OFFSETS:
        for m in re.finditer(pattern, text, re.I):
            findings.append(Finding("Compensatie", "high", "Offsetting claim", text[m.start():m.end()+60], url))

    return findings

def crawl(url, max_pages=10):
    visited: Set[str] = set()
    queue = deque([url])
    findings = []
    pages = 0

    while queue and pages < max_pages:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)

        html = fetch_html(current)
        if not html:
            continue

        pages += 1
        text, links = extract_text_and_links(current, html)
        findings.extend(run_rules(current, text))

        for link in links:
            if urlparse(link).netloc == urlparse(url).netloc:
                queue.append(link)

    return pages, findings

def score(findings):
    weights = {"medium": 10, "high": 25}
    return min(100, sum(weights.get(f.severity, 0) for f in findings))

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/scan", response_class=HTMLResponse)
def scan(request: Request, url: str = Form(...)):
    pages, findings = crawl(url)
    return templates.TemplateResponse("report.html", {
        "request": request,
        "input_url": url,
        "pages_scanned": pages,
        "risk_score": score(findings),
        "findings": [asdict(f) for f in findings],
    })
