from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

app = FastAPI()

# 🔍 Detectie lijsten
GREEN_CLAIMS = [
    "carbon neutral",
    "net zero",
    "climate positive",
    "eco-friendly",
    "sustainable",
    "green energy",
    "offset",
    "carbon footprint",
]

VAGUE_TERMS = [
    "committed",
    "working towards",
    "aim to",
    "leading",
    "green solutions",
]

ABSOLUTE_CLAIMS = [
    "100% green",
    "zero impact",
    "completely sustainable",
    "no environmental impact",
]

def fetch_pages(start_url, max_pages=5):
    visited = set()
    to_visit = [start_url]
    pages = []

    while to_visit and len(pages) < max_pages:
        url = to_visit.pop(0)
        if url in visited:
            continue

        try:
            res = requests.get(url, timeout=5)
            soup = BeautifulSoup(res.text, "html.parser")

            text = soup.get_text(" ", strip=True)
            pages.append((url, text))
            visited.add(url)

            # interne links verzamelen
            for link in soup.find_all("a", href=True):
                full = urljoin(url, link["href"])
                if urlparse(full).netloc == urlparse(start_url).netloc:
                    if full not in visited:
                        to_visit.append(full)

        except:
            continue

    return pages

def analyze_text(text):
    findings = []
    score_penalty = 0

    lower = text.lower()

    # 🌱 groene claims
    for claim in GREEN_CLAIMS:
        if claim in lower:
            findings.append(("Groene claim", claim, "info"))

    # ⚠️ vage termen
    for term in VAGUE_TERMS:
        if term in lower:
            findings.append(("Vage claim", term, "medium"))
            score_penalty += 10

    # 🚨 absolute claims
    for term in ABSOLUTE_CLAIMS:
        if term in lower:
            findings.append(("Absolute claim", term, "high"))
            score_penalty += 20

    # ❗ bewijs check
    if "source" not in lower and "report" not in lower and "data" not in lower:
        findings.append(("Geen bewijs", "geen bronvermelding", "medium"))
        score_penalty += 15

    return findings, score_penalty

@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <h2>EmpCo website scan (EU 2024/825)</h2>
    <form action="/scan" method="post">
        Website URL: <input name="url" value="https://example.com">
        Max pagina’s: <input name="max_pages" value="5">
        <button type="submit">Start scan</button>
    </form>
    """

@app.post("/scan", response_class=HTMLResponse)
def scan(url: str = Form(...), max_pages: int = Form(5)):
    pages = fetch_pages(url, int(max_pages))

    total_findings = []
    total_penalty = 0

    for page_url, text in pages:
        findings, penalty = analyze_text(text)
        total_penalty += penalty

        for f in findings:
            total_findings.append((page_url, *f))

    score = max(0, 100 - total_penalty)

    # HTML output
    html = f"<h2>Scanrapport</h2>"
    html += f"<p><b>URL:</b> {url}</p>"
    html += f"<p><b>Pagina’s gescand:</b> {len(pages)}</p>"
    html += f"<p><b>Risicoscore:</b> {score}/100</p>"

    html += "<h3>Bevindingen</h3>"
    if total_findings:
        for page, type_, term, level in total_findings:
            html += f"<p><b>{type_}</b> ({level})<br>Pagina: {page}<br>Term: {term}</p>"
    else:
        html += "<p>Geen risico’s gevonden.</p>"

    html += "<p><i>Deze scan is indicatief en vormt geen juridisch advies.</i></p>"
    html += '<p><a href="/">Nieuwe scan</a></p>'

    return html
