#!/usr/bin/env python3
"""Refresh public fund NAVs without changing private holding quantities or cost basis."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
FUNDS_PATH = ROOT / "data" / "funds.json"

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "enum": ["NEB5CSA", "FTTASH1", "SLBGLEP", "ALGAATU", "AGATH2S"]},
                    "status": {"type": "string", "enum": ["found", "not_found"]},
                    "nav": {"type": "number"},
                    "currency": {"type": "string", "enum": ["SGD", "USD", "GBP"]},
                    "nav_as_of": {"type": "string"},
                    "source_label": {"type": "string"},
                    "source_url": {"type": "string"},
                },
                "required": ["id", "status", "nav", "currency", "nav_as_of", "source_label", "source_url"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def main() -> None:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        print("Fund NAV refresh skipped because OPENAI_API_KEY is not configured.")
        return

    from openai import OpenAI

    payload = json.loads(FUNDS_PATH.read_text())
    funds_by_id = {item["id"]: item for item in payload["items"]}
    public_watchlist = [
        {
            "id": item["id"],
            "ticker": item["ticker"],
            "name": item["name"],
            "share_class": item["share_class"],
            "isin": item.get("isin", ""),
            "currency": item["currency"],
            "last_nav": item["nav"],
            "last_nav_as_of": item["nav_as_of"],
            "preferred_source": item.get("source_url", ""),
        }
        for item in payload["items"]
    ]
    today = date.today()
    prompt = f"""
Today is {today.isoformat()}. Find the latest published daily NAV for each exact share class below:
{json.dumps(public_watchlist)}

Use issuer or fund-manager pages first. A reputable regulated distributor or major market-data page
is acceptable only when the manager has no accessible current value. Match by ISIN and ticker, not
just the fund name. Do not estimate or infer a NAV. Return not_found if the exact class and a dated NAV
cannot be verified. Never return a date earlier than last_nav_as_of.

Return NAV in the watchlist display unit. In particular SLBGLEP is quoted in GB pence, so a price near
470 must remain 470 rather than being converted to GBP 4.70; still label its currency GBP. For all other
classes return normal currency units. Use a direct HTTPS page URL.
"""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        response = OpenAI(api_key=key).responses.create(
            model=os.getenv("FUND_NAV_MODEL", "gpt-5.4-nano"),
            tools=[{"type": "web_search"}],
            input=prompt,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "fund_nav_refresh",
                    "strict": True,
                    "schema": SCHEMA,
                }
            },
        )
        refreshed = json.loads(response.output_text)
        accepted = 0
        for candidate in refreshed.get("items", []):
            existing = funds_by_id.get(candidate.get("id"))
            if not existing or candidate.get("status") != "found":
                continue
            try:
                candidate_date = date.fromisoformat(candidate["nav_as_of"])
                existing_date = date.fromisoformat(existing["nav_as_of"])
                nav = float(candidate["nav"])
            except (KeyError, TypeError, ValueError):
                continue
            if candidate["currency"] != existing["currency"]:
                continue
            if candidate_date < existing_date or candidate_date > today or (today - candidate_date).days > 21:
                continue
            days_advanced = (candidate_date - existing_date).days
            ratio = nav / float(existing["nav"])
            if existing.get("source_label") == "Owner statement" and days_advanced == 0:
                continue
            if days_advanced == 0 and not 0.995 <= ratio <= 1.005:
                continue
            daily_guard = 1.08 ** max(days_advanced, 1)
            if not 1 / daily_guard <= ratio <= daily_guard:
                continue
            source_url = str(candidate.get("source_url", ""))
            if not source_url.startswith("https://"):
                continue
            preferred_url = str(existing.get("source_url", ""))
            if preferred_url and urlparse(source_url).hostname != urlparse(preferred_url).hostname:
                continue
            existing.update({
                "nav": round(nav, 6),
                "nav_as_of": candidate_date.isoformat(),
                "source_label": str(candidate.get("source_label", "Public NAV"))[:80],
                "source_url": source_url,
            })
            accepted += 1
        payload["last_attempt_at"] = now
        if accepted:
            payload["updated_at"] = now
        ages = [(today - date.fromisoformat(item["nav_as_of"])).days for item in payload["items"]]
        payload["refresh_status"] = "current" if max(ages) <= 7 else "partial"
        payload.pop("status_message", None)
        FUNDS_PATH.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"Fund NAV refresh accepted {accepted} verified updates.")
    except Exception as exc:
        payload["last_attempt_at"] = now
        payload["refresh_status"] = "error"
        payload["status_message"] = "NAV refresh was delayed; the last verified prices remain in use."
        FUNDS_PATH.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"Fund NAV refresh failed safely: {type(exc).__name__}")


if __name__ == "__main__":
    main()
