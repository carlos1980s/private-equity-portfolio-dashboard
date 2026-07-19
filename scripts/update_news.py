#!/usr/bin/env python3
"""Refresh public O/G/V signals without changing approved valuation assumptions."""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "news.json"
VALID_TICKERS = {"O", "G", "V"}
VALID_SOURCE_LABELS = {"Official release", "Regulatory filing", "Credible press"}
GENERIC_SOURCE_PATHS = {
    "",
    "/blog",
    "/media",
    "/news",
    "/news/company-announcements",
    "/newsroom",
    "/press",
    "/search",
}


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
                    "source_label": {"type": "string", "enum": sorted(VALID_SOURCE_LABELS)},
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
        aliases = {name}
        if len(name.split()) > 1:
            aliases.add(name.split()[0])
        for alias in sorted(aliases, key=len, reverse=True):
            cleaned = re.sub(rf"\b{re.escape(alias)}\b", ticker, cleaned, flags=re.IGNORECASE)
    return cleaned


def normalize_direct_source_url(raw_url: str) -> str | None:
    """Return a canonical article URL, or None for generic/non-web sources."""
    try:
        parsed = urlsplit(raw_url.strip())
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    path = re.sub(r"/+", "/", parsed.path).rstrip("/").lower()
    if path in GENERIC_SOURCE_PATHS:
        return None
    if any(marker in path for marker in ("/category/", "/search/", "/tag/")):
        return None
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), parsed.query, ""))


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
Today is {today}. Research material news from the last 45 days for this private watchlist:
{json.dumps(mapping)}

Return at most four genuinely material items per ticker. Prioritize official company releases,
regulatory filings, financing or valuation evidence, capacity or revenue evidence, major products,
IPO signals, and credible press. Use only O, G, or V in every visible field; never write the full
company names in headlines, summaries, or source labels. Keep each summary under 42 words.

Classify impact conservatively. Positive or negative labels feed a bounded rules-based simulation
overlay; neutral and mixed items remain visible but do not move valuations. Set review_status to
pending only when a reasonable investor should consider changing a base assumption. Include a direct
article URL, never a landing, category, homepage, or search page. Use exactly one generic source label:
Official release, Regulatory filing, or Credible press.
"""
        from openai import OpenAI

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
        seen_urls = set()
        today_date = date.fromisoformat(today)
        for item in payload.get("items", []):
            if item.get("company") not in VALID_TICKERS:
                continue
            try:
                item_date = date.fromisoformat(str(item["date"]))
            except (KeyError, TypeError, ValueError):
                continue
            if (today_date - item_date).days not in range(46):
                continue
            source_url = normalize_direct_source_url(str(item.get("source_url", "")))
            if not source_url or source_url in seen_urls:
                continue
            if item.get("source_label") not in VALID_SOURCE_LABELS:
                continue
            item["source_url"] = source_url
            seen_urls.add(source_url)
            for field in ("headline", "summary", "source_label"):
                item[field] = clean_text(str(item[field]), mapping)
            items.append(item)
        items.sort(key=lambda item: (item.get("date", ""), item.get("company", "")), reverse=True)
        output = {
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "last_attempt_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "refresh_status": "current",
            "policy": "Verified positive and negative news feeds a bounded, time-decaying simulation overlay; base assumptions remain owner-controlled.",
            "items": items[:12],
        }
        OUTPUT_PATH.write_text(json.dumps(output, indent=2) + "\n")
        print(f"News refresh completed with {len(output['items'])} material signals.")
    except Exception as exc:
        write_status("error", "Automated refresh was delayed; the previous digest remains visible.")
        print(f"News refresh failed safely: {type(exc).__name__}")


if __name__ == "__main__":
    main()
