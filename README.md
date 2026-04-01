# greencheck

ECD compliance checker met focus op milieuclaims en contextanalyse.

## Wat doet de scanner nu?
- Detecteert claims met hoge relevantie voor ECD-risico (bv. absolute klimaatclaims of concrete emissiedoelstellingen).
- Filtert lage-signaal tekst (navigatie, generieke ESG-teksten, blog/news context).
- Prioriteert pagina's met sustainability/ESG-context in de crawl.
- Labelt findings met Red/Amber/Green prioriteit.
- Detecteert brede risicotermen zoals "sustainable", "green", "carbon neutral", incl. meertalige varianten.
- Past eenvoudige context-check toe om bv. "green" als kleurgebruik minder risicovol te beoordelen.

## Starten
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```
