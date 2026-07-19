#!/usr/bin/env python3
"""Regenerate the public full-portfolio model from approved assumptions and public NAVs."""

from __future__ import annotations

import csv
import io
import json
import math
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
ASSUMPTIONS_PATH = ROOT / "model" / "assumptions.json"
FUNDS_PATH = ROOT / "data" / "funds.json"
OUTPUT_PATH = ROOT / "data" / "model.json"
HORIZONS = ["YE 2026", "Q1 2027", "Q2 2027", "Q3 2027", "YE 2027"]
HORIZON_DATES = [date(2026, 12, 31), date(2027, 3, 31), date(2027, 6, 30), date(2027, 9, 30), date(2027, 12, 31)]
PRIVATE_TIMES = np.array([0.0, 0.25, 0.5, 0.75, 1.0])


def triangular_ppf(uniform: np.ndarray, left: float, mode: float, right: float) -> np.ndarray:
    split = (mode - left) / (right - left)
    return np.where(
        uniform < split,
        left + np.sqrt(uniform * (right - left) * (mode - left)),
        right - np.sqrt((1 - uniform) * (right - left) * (right - mode)),
    )


def after_carry(gross: np.ndarray, basis: float, carry: float) -> np.ndarray:
    return np.where(gross <= basis, gross, basis + (1 - carry) * (gross - basis))


def interpolate_triangle(start: list[float], end: list[float], time: float) -> tuple[float, float, float]:
    return tuple((1 - time) * start[index] + time * end[index] for index in range(3))


def current_fx(base: str, fallback: float) -> tuple[float, str]:
    try:
        request = urllib.request.Request(
            f"https://api.frankfurter.app/latest?from={base}&to=SGD",
            headers={"User-Agent": "portfolio-probability-monitor/2.0"},
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.load(response)
        return float(payload["rates"]["SGD"]), str(payload.get("date", ""))
    except Exception:
        return fallback, "fallback"


def sp500_history(fallback_level: float, fallback_date: str) -> tuple[dict[date, float], str]:
    try:
        request = urllib.request.Request(
            "https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500",
            headers={"User-Agent": "portfolio-probability-monitor/2.0"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            text = response.read().decode("utf-8")
        history = {
            date.fromisoformat(row.get("observation_date") or row["DATE"]): float(row["SP500"])
            for row in csv.DictReader(io.StringIO(text))
            if row.get("SP500") not in (None, "", ".")
        }
        if not history:
            raise ValueError("S&P 500 history was empty")
        return history, "FRED"
    except Exception:
        return {date.fromisoformat(fallback_date): fallback_level}, "fallback"


def market_level_on_or_before(history: dict[date, float], target: date) -> tuple[date, float]:
    eligible = [observation_date for observation_date in history if observation_date <= target]
    observation_date = max(eligible) if eligible else min(history)
    return observation_date, history[observation_date]


def value_in_usd(amount: float | np.ndarray, currency: str, usd_sgd: float, gbp_sgd: float):
    if currency == "USD":
        return amount
    if currency == "SGD":
        return amount / usd_sgd
    if currency == "GBP":
        return amount * gbp_sgd / usd_sgd
    raise ValueError(f"Unsupported fund currency: {currency}")


def round_list(values: np.ndarray, digits: int = 4) -> list[float]:
    return np.round(values, digits).tolist()


def main() -> None:
    assumptions = json.loads(ASSUMPTIONS_PATH.read_text())
    funds_payload = json.loads(FUNDS_PATH.read_text())
    funds = funds_payload["items"]
    positions = assumptions["positions"]
    paths = int(assumptions["paths"])
    carry = float(assumptions["carry"])
    rng = np.random.default_rng(int(assumptions["seed"]))

    usd_sgd, usd_fx_date = current_fx("USD", float(assumptions["usd_sgd"]))
    gbp_sgd, gbp_fx_date = current_fx("GBP", float(assumptions["gbp_sgd"]))

    correlation = np.asarray(assumptions["correlation"], dtype=float)
    z = rng.standard_normal((paths, 3)) @ np.linalg.cholesky(correlation).T
    uniforms = {ticker: rng.random(paths) for ticker in ("O", "G", "V")}

    o = positions["O"]
    g = positions["G"]
    v = positions["V"]
    o_2026 = o["valuation_2026"]["median"] * np.exp(o["valuation_2026"]["sigma"] * z[:, 0])
    o_2027 = o["valuation_2027"]["median"] * np.exp(o["valuation_2027"]["sigma"] * z[:, 0])
    v_2026 = v["valuation_2026"]["median"] * np.exp(v["valuation_2026"]["sigma"] * z[:, 2])
    v_2027 = v["valuation_2027"]["median"] * np.exp(v["valuation_2027"]["sigma"] * z[:, 2])
    g_2026 = g["valuation_2026"]["median"] * np.exp(g["valuation_2026"]["sigma"] * z[:, 1])

    regime_draw = rng.random(paths)
    capacity = np.empty(paths)
    cumulative = 0.0
    for index, regime in enumerate(g["capacity_regimes"]):
        next_cumulative = cumulative + regime["probability"]
        mask = (regime_draw >= cumulative) & (regime_draw < next_cumulative if index < 2 else regime_draw <= 1)
        capacity[mask] = rng.triangular(*regime["triangle"], mask.sum())
        cumulative = next_cumulative
    utilization = rng.triangular(*g["utilization"], paths)
    revenue_per_mw = rng.triangular(*g["revenue_per_mw_millions"], paths) * 1e6
    multiple = g["revenue_multiple"]["median"] * np.exp(g["revenue_multiple"]["sigma"] * z[:, 1])
    operating_value = capacity * utilization * revenue_per_mw * multiple
    valuation_prior = g["valuation_prior"]["median"] * np.exp(g["valuation_prior"]["sigma"] * z[:, 1])
    blend = float(g["capacity_blend_weight"])
    g_2027 = np.exp(blend * np.log(operating_value) + (1 - blend) * np.log(valuation_prior))
    retention = rng.choice(g["retention"]["values"], paths, p=g["retention"]["probabilities"])

    liquid_model = assumptions["liquid_fund_model"]
    equity_market = liquid_model["equity_market"]
    market_history, market_source_status = sp500_history(
        float(equity_market["reference_level"]), str(equity_market["reference_date"])
    )
    market_reference_date = max(market_history)
    market_reference_level = market_history[market_reference_date]
    central_sp500 = np.exp(
        (1 - PRIVATE_TIMES) * np.log(float(equity_market["central_2026"]))
        + PRIVATE_TIMES * np.log(float(equity_market["central_2027"]))
    )
    market_volatility = float(equity_market["annual_volatility"])
    return_assumptions = liquid_model["return_assumptions"]
    market_rho = float(liquid_model["market_correlation_to_o"])
    market_z = market_rho * z[:, 0] + math.sqrt(1 - market_rho**2) * rng.standard_normal(paths)
    group_shocks: dict[str, np.ndarray] = {}
    for fund in funds:
        fund_assumption = return_assumptions[fund["id"]]
        group = fund_assumption["risk_group"]
        if group not in group_shocks:
            group_shocks[group] = rng.standard_normal(paths)

    nav_dates = [date.fromisoformat(fund["nav_as_of"]) for fund in funds]
    liquid_as_of = max(nav_dates)
    forecast_years = np.asarray([max((target - liquid_as_of).days / 365.25, 0.0) for target in HORIZON_DATES])
    liquid_values: dict[str, list[np.ndarray]] = {fund["id"]: [] for fund in funds}
    liquid_summaries: list[dict] = []

    for fund in funds:
        fund_assumption = return_assumptions[fund["id"]]
        nav_unit = float(fund["nav"]) * float(fund.get("nav_scale", 1.0))
        cost_unit = float(fund["cost_nav"]) * float(fund.get("nav_scale", 1.0))
        current_local = float(fund["shares"]) * nav_unit
        cost_local = float(fund["shares"]) * cost_unit
        current_usd = float(value_in_usd(current_local, fund["currency"], usd_sgd, gbp_sgd))
        cost_usd = float(value_in_usd(cost_local, fund["currency"], usd_sgd, gbp_sgd))
        beta = float(fund_assumption["equity_beta"])
        idiosyncratic_volatility = float(fund_assumption["idiosyncratic_volatility"])
        shock = group_shocks[fund_assumption["risk_group"]]
        fund_as_of = date.fromisoformat(fund["nav_as_of"])
        sp500_date, sp500_reference = market_level_on_or_before(market_history, fund_as_of)
        fund_forecast_years = [max((target - fund_as_of).days / 365.25, 0.0) for target in HORIZON_DATES]
        central_values = []
        for index, years in enumerate(fund_forecast_years):
            sp500_projected = central_sp500[index] * np.exp(market_volatility * math.sqrt(years) * market_z)
            projected = current_usd * (sp500_projected / sp500_reference) ** beta * np.exp(
                idiosyncratic_volatility * math.sqrt(years) * shock
            )
            liquid_values[fund["id"]].append(projected)
            central_values.append(current_usd * (central_sp500[index] / sp500_reference) ** beta)
        current_sgd = current_usd * usd_sgd
        cost_sgd = cost_usd * usd_sgd
        liquid_summaries.append({
            "id": fund["id"],
            "ticker": fund["ticker"],
            "name": fund["name"],
            "share_class": fund["share_class"],
            "isin": fund.get("isin", ""),
            "ownership": 1.0,
            "shares": fund["shares"],
            "cost_nav": fund["cost_nav"],
            "nav": fund["nav"],
            "nav_scale": fund.get("nav_scale", 1.0),
            "currency": fund["currency"],
            "nav_as_of": fund["nav_as_of"],
            "current_value_usd": round(current_usd, 2),
            "current_value_sgd": round(current_sgd, 2),
            "cost_value_sgd": round(cost_sgd, 2),
            "unrealized_gain_sgd": round(current_sgd - cost_sgd, 2),
            "unrealized_gain_percent": round(current_sgd / cost_sgd - 1, 6),
            "equity_beta_assumption": beta,
            "annual_volatility_assumption": round(
                math.sqrt((beta * market_volatility) ** 2 + idiosyncratic_volatility**2), 6
            ),
            "sp500_reference_level": round(sp500_reference, 2),
            "sp500_reference_date": sp500_date.isoformat(),
            "central_2026_usd": round(float(central_values[0]), 2),
            "central_2027_usd": round(float(central_values[-1]), 2),
            "expected_2026_usd": round(float(liquid_values[fund["id"]][0].mean()), 2),
            "expected_2027_usd": round(float(liquid_values[fund["id"]][-1].mean()), 2),
            "source_label": fund.get("source_label", ""),
            "source_url": fund.get("source_url", ""),
        })

    portfolio_values: list[np.ndarray] = []
    company_values: dict[str, list[np.ndarray]] = {"O": [], "G": [], "V": []}
    valuation_paths: dict[str, list[np.ndarray]] = {"O": [], "G": [], "V": []}
    rows = []

    for index, (time, label) in enumerate(zip(PRIVATE_TIMES, HORIZONS)):
        o_value = np.exp((1 - time) * np.log(o_2026) + time * np.log(o_2027))
        g_value = np.exp((1 - time) * np.log(g_2026) + time * np.log(g_2027))
        v_value = np.exp((1 - time) * np.log(v_2026) + time * np.log(v_2027))
        valuation_paths["O"].append(o_value)
        valuation_paths["G"].append(g_value)
        valuation_paths["V"].append(v_value)

        o_dilution = triangular_ppf(uniforms["O"], *interpolate_triangle(o["dilution_2026"], o["dilution_2027"], time))
        g_dilution = triangular_ppf(uniforms["G"], *interpolate_triangle(g["dilution_2026"], g["dilution_2027"], time))
        v_dilution = triangular_ppf(uniforms["V"], *interpolate_triangle(v["dilution_2026"], v["dilution_2027"], time))

        o_net = after_carry(
            o["shares"] * o["reference_price"] * (o_value / o["reference_valuation"]) * (1 - o_dilution),
            o["basis"],
            carry,
        )
        g_net = (
            (1 - carry)
            * g["units"]
            * g["reference_price"]
            * (g_value / g["reference_valuation"])
            * (1 - g_dilution)
            * retention
        )
        v_net = after_carry(
            v["shares"] * v["reference_price"] * (v_value / v["reference_valuation"]) * (1 - v_dilution),
            v["basis"],
            carry,
        )
        company_values["O"].append(o_net)
        company_values["G"].append(g_net)
        company_values["V"].append(v_net)
        liquid_total = sum(liquid_values[fund["id"]][index] for fund in funds)
        total = assumptions["cash_distributions_usd"] + o_net + g_net + v_net + liquid_total
        portfolio_values.append(total)
        p10, median, p90 = np.quantile(total, [0.1, 0.5, 0.9])
        rows.append({
            "label": label,
            "forecast_years": round(float(forecast_years[index]), 4),
            "expected": round(float(total.mean()), 2),
            "median": round(float(median), 2),
            "p10": round(float(p10), 2),
            "p90": round(float(p90), 2),
            "components": {
                **{ticker: round(float(company_values[ticker][-1].mean()), 2) for ticker in ("O", "G", "V")},
                "FUNDS": round(float(liquid_total.mean()), 2),
            },
        })

    sample = np.concatenate([values[::10] for values in portfolio_values])
    lower, upper = np.quantile(sample, [0.0025, 0.9975])
    edges = np.linspace(lower, upper, 73)
    centers = (edges[:-1] + edges[1:]) / 2
    densities = []
    for values in portfolio_values:
        density, _ = np.histogram(values, bins=edges, density=True)
        densities.append(density * 100000)

    company_summary = []
    for ticker in ("O", "G", "V"):
        item = positions[ticker]
        company_summary.append({
            "ticker": ticker,
            "economic_interest": item["economic_interest"],
            "expected_2026": rows[0]["components"][ticker],
            "expected_2027": rows[-1]["components"][ticker],
            "valuation_median_2026": round(float(np.median(valuation_paths[ticker][0])), 2),
            "valuation_median_2027": round(float(np.median(valuation_paths[ticker][-1])), 2),
        })

    liquid_current_usd = sum(fund["current_value_usd"] for fund in liquid_summaries)
    liquid_current_sgd = sum(fund["current_value_sgd"] for fund in liquid_summaries)
    liquid_cost_sgd = sum(fund["cost_value_sgd"] for fund in liquid_summaries)
    output = {
        "model_version": assumptions["version"],
        "model_policy": "News and public NAVs refresh automatically; valuation assumptions, share counts and cost basis change only after owner approval.",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "currency": {
            "usd_sgd": round(usd_sgd, 6),
            "gbp_sgd": round(gbp_sgd, 6),
            "as_of": max(usd_fx_date, gbp_fx_date) if "fallback" not in (usd_fx_date, gbp_fx_date) else "fallback",
        },
        "cash_distributions_usd": assumptions["cash_distributions_usd"],
        "horizons": rows,
        "distribution": {
            "values_usd": round_list(centers, 2),
            "density_per_100k_usd": [round_list(np.asarray(values), 5) for values in densities],
        },
        "companies": company_summary,
        "equity_market": {
            "index": "S&P 500",
            "reference_level": round(float(market_reference_level), 2),
            "reference_date": market_reference_date.isoformat(),
            "central_2026": float(equity_market["central_2026"]),
            "central_2027": float(equity_market["central_2027"]),
            "upside_to_2026": round(float(equity_market["central_2026"]) / market_reference_level - 1, 6),
            "upside_to_2027": round(float(equity_market["central_2027"]) / market_reference_level - 1, 6),
            "annual_volatility": market_volatility,
            "central_horizons": round_list(central_sp500, 2),
            "source_status": market_source_status,
            "source_url": equity_market["source_url"],
        },
        "liquid_portfolio": {
            "as_of": liquid_as_of.isoformat(),
            "refresh_status": funds_payload.get("refresh_status", "unknown"),
            "current_value_usd": round(liquid_current_usd, 2),
            "current_value_sgd": round(liquid_current_sgd, 2),
            "cost_value_sgd": round(liquid_cost_sgd, 2),
            "unrealized_gain_sgd": round(liquid_current_sgd - liquid_cost_sgd, 2),
            "unrealized_gain_percent": round(liquid_current_sgd / liquid_cost_sgd - 1, 6),
            "expected_2026_usd": rows[0]["components"]["FUNDS"],
            "expected_2027_usd": rows[-1]["components"]["FUNDS"],
            "funds": liquid_summaries,
        },
        "capacity": {
            "ticker": "G",
            "target_mw": g["capacity_target_mw"],
            "expected_mw": round(float(capacity.mean()), 1),
            "median_mw": round(float(np.median(capacity)), 1),
            "probability_at_least_target": round(float((capacity >= g["capacity_target_mw"]).mean()), 4),
        },
        "targets": {
            "portfolio_sgd_500k_2026": round(float((portfolio_values[0] * usd_sgd >= 500000).mean()), 4),
            "portfolio_sgd_500k_2027": round(float((portfolio_values[-1] * usd_sgd >= 500000).mean()), 4),
            "portfolio_sgd_1m_2027": round(float((portfolio_values[-1] * usd_sgd >= 1000000).mean()), 4),
        },
        "method": {
            "paths": paths,
            "seed": assumptions["seed"],
            "central_surface_probability": 0.995,
            "liquid_fund_method": "Fund returns are beta-linked to the owner-approved S&P 500 central path, with correlated market and fund-specific residual risk. The two Allianz AI share classes share one underlying factor.",
        },
    }
    OUTPUT_PATH.write_text(json.dumps(output, indent=2) + "\n")
    print(f"Updated {OUTPUT_PATH.name} with model {assumptions['version']}")


if __name__ == "__main__":
    main()
