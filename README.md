# O / G / V Probability Monitor

Public GitHub Pages dashboard for an approved private-equity probability model.

## Update policy

- The approved valuation assumptions are stored in `model/assumptions.json`.
- The model and USD/SGD conversion regenerate every two days.
- News signals refresh automatically and are shown for review.
- News never changes valuation assumptions automatically.
- Company labels remain O, G, and V in the public interface.

## Required encrypted secret

Add a replacement API credential as the repository Actions secret `OPENAI_API_KEY`.
Never commit credentials to the repository or paste them into an issue, discussion, workflow file, or chat.

The private search-name watchlist is stored separately as `NEWS_WATCHLIST_JSON`.

## Local refresh

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/update_dashboard.py
python -m http.server 8000
```

Open `http://localhost:8000`.

## Disclaimer

This is a probabilistic private-market estimate, not financial advice, an appraisal, a fund NAV, or a liquidity forecast.
