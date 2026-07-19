# O / G / V Probability Monitor

Public GitHub Pages dashboard for an approved full-portfolio probability model covering private positions O, G and V plus five liquid funds.

## Update policy

- The approved valuation assumptions are stored in `model/assumptions.json`.
- The model, fund NAVs and USD/SGD/GBP conversion regenerate every two days.
- News signals refresh automatically and are shown for review.
- News never changes valuation assumptions automatically; fund refreshes may update only verified public NAVs.
- Share counts, cost basis, private-company labels and subjective return assumptions remain owner-controlled.
- Company labels remain O, G, and V in the public interface.
- Liquid-fund paths are beta-linked to the approved S&P 500 central case: 8,150 at YE 2026 and 9,300 at YE 2027.
- The current S&P 500 reference close is refreshed from the Federal Reserve Economic Data series before each model run.

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

This is a probabilistic portfolio estimate, not financial advice, an appraisal, or a liquidity forecast. Fund NAVs can lag their managers and private-market indications may not be executable.
