# greencheck

EmpCo compliance checker met focus op **materiële** claims rond klimaat/emissies.

## Wat doet de scanner nu?
- Detecteert alleen claims met hoge relevantie voor EmpCo-risico (bv. absolute klimaatclaims of concrete emissiedoelstellingen).
- Filtert lage-signaal tekst (navigatie, generieke ESG-teksten, blog/news context).
- Prioriteert pagina's met sustainability/ESG-context in de crawl.
- Beperkt output tot representatieve high/medium issues om false positives te reduceren.

## Starten
```bash
pip install -r requirements.txt
apt-get update && apt-get install -y chromium chromium-driver
uvicorn app.main:app --reload
```

## Deployment build stap
Gebruik tijdens build/deploy minimaal:
```bash
pip install -r requirements.txt
apt-get update && apt-get install -y chromium chromium-driver
```

## Productieplatform (greencheck.durably.eu)
- `greencheck.durably.eu` resolveert naar `216.24.57.7` en `216.24.57.251` (Render anycast ranges), dus deployment is Render-gebaseerd.
- Dit project bevat nu `render.yaml` met `env: docker` en `dockerfilePath: ./Dockerfile`, zodat Render expliciet de `Dockerfile` gebruikt (en dus Chromium installeert in de runtime image).
- Na merge: trigger in Render een **Manual Deploy → Deploy latest commit** (of wacht op Auto Deploy).

Als je host extra OS dependencies vereist:
```bash
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium uvicorn app.main:app --reload
```
