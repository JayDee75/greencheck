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
uvicorn app.main:app --reload
```
