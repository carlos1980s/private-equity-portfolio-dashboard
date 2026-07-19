#!/usr/bin/env python3
"""Refresh public O/G/V signals without changing approved valuation assumptions."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "news.json"
VALID_TICKERS = {"O", "G", "V"}


SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "company": {"type": "string", "enum": ["O", "G", "V"]},
                    "date": {"type": "string"},
                    "headline": {"type": "string"},
                    "summary": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": ["funding", "valuation", "capacity", "product", "regulatory", "ipo", "other"],
                    },
                    "impact": {"type": "string", "enum": ["positive", "neutral", "negative", "mixed"]},
                    "review_status": {"type": "string", "enum": ["none", "pending"]},
                    "source_label": {"type": "string"},
                    "source_url": {"type": "string"}
                },
                "required": [
                    "company", "date", "headline", "summary", "category", "impact",
                    "review_status", "source_label", "source_url"
                ],
                "additionalProperties": False
            }
        }
    },
    "required": ["items"],
    "additionalProperties": False
}


def clean_text(text: str, mapping: dict[str, str]) -> str:
    cleaned = text.strip()
    for ticker, name in sorted(mapping.items(), key=lambda pair: len(pair[1]), reverse=True):
        cleaned = re.sub(re.escape(name), ticker, cleaned, flags=re.IGNORECASE)
    return cleaned


def write_status(status: str, error: str | None = None) -> None:
    existing = {"items": []}
    if OUTPUT_PATH.exists():
        existing = json.loads(OUTPUT_PATH.read_text())
    existing["last_attempt_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    existing["refresh_status"] = status
    if error:
        existing["status_message"] = error[:180]
    OUTPUT_PATH.write_text(json.dumps(existing, indent=2) + "\n")


def main() -> None:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    raw_watchlist = os.getenv("NEWS_WATCHLIST_JSON", "").strip()
    if not key or not raw_watchlist:
        write_status("waiting_for_secrets", "Add the replacement API key to enable the next news refresh.")
        print("News refresh skipped because required encrypted secrets are not configured.")
        return

    try:
        mapping = json.loads(raw_watchlist)
        if set(mapping) != VALID_TICKERS or not all(isinstance(value, str) and value.strip() for value in mapping.values()):
            raise ValueError("The private watchlist must map O, G and V to search names.")
        today = datetime.now(timezone.utc).date().isoformat()
        prompt = f"""
Today is {today}. Research material news from the last 14 days for this private watchlist:
{json.dumps(mapping)}

Return at most four genuinely material items per ticker. Prioritize official company releases,
regulatory filings, financing or valuation evidence, capacity or revenue evidence, major products,
IPO signals, and credible press. Use only O, G, or V in every visible field; never write the full
company names in headlines, summaries, or source labels. Keep each summary under 42 words.

This dashboard uses review-only mode: do not change valuations or model assumptions. Set
review_status to pending only when a reasonable investor should consider changing an assumption;
otherwise use none. Include a direct source URL and a generic source label such as Official release,
Regulatory filing, or Credible press.
"""
        client = OpenAI(api_key=key)
        response = client.responses.create(
            model=os.getenv("NEWS_MODEL", "gpt-5.4-nano"),
            tools=[{"type": "web_search"}],
            input=prompt,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "portfolio_news_digest",
                    "strict": True,
                    "schema": SCHEMA,
                }
            },
        )
        payload = json.loads(response.output_text)
        items = []
        for item in payload.get("items", []):
            if item.get("company") not in VALID_TICKERS:
                continue
            for field in ("headline", "summary", "source_label"):
                item[field] = clean_text(str(item[field]), mapping)
            items.append(item)
        items.sort(key=lambda item: (item.get("date", ""), item.get("company", "")), reverse=True)
        output = {
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "last_attempt_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "refresh_status": "current",
            "policy": "News only. Model changes require owner approval.",
            "items": items[:12],
        }
        OUTPUT_PATH.write_text(json.dumps(output, indent=2) + "\n")
        print(f"News refresh completed with {len(output['items'])} material signals.")
    except Exception as exc:
        write_status("error", "Automated refresh was delayed; the previous digest remains visible.")
        print(f"News refresh failed safely: {type(exc).__name__}")


if __name__ == "__main__":
    main()
