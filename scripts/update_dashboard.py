#!/usr/bin/env python3
"""Regenerate the public model data from the last approved assumptions."""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
ASSUMPTIONS_PATH = ROOT / "model" / "assumptions.json"
OUTPUT_PATH = ROOT / "data" / "model.json"
HORIZONS = ["YE 2026", "Q1 2027", "Q2 2027", "Q3 2027", "YE 2027"]
TIMES = np.array([0.0, 0.25, 0.5, 0.75, 1.0])


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


def current_fx(fallback: float) -> tuple[float, str]:
    try:
        request = urllib.request.Request(
            "https://api.frankfurter.app/latest?from=USD&to=SGD",
            headers={"User-Agent": "portfolio-probability-monitor/1.0"},
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.load(response)
        return float(payload["rates"]["SGD"]), str(payload.get("date", ""))
    except Exception:
        return fallback, "fallback"


def round_list(values: np.ndarray, digits: int = 4) -> list[float]:
    return np.round(values, digits).tolist()


def main() -> None:
    assumptions = json.loads(ASSUMPTIONS_PATH.read_text())
    positions = assumptions["positions"]
    paths = int(assumptions["paths"])
    carry = float(assumptions["carry"])
    rng = np.random.default_rng(int(assumptions["seed"]))

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

    portfolio_values: list[np.ndarray] = []
    company_values: dict[str, list[np.ndarray]] = {"O": [], "G": [], "V": []}
    valuation_paths: dict[str, list[np.ndarray]] = {"O": [], "G": [], "V": []}
    rows = []

    for time, label in zip(TIMES, HORIZONS):
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
        total = assumptions["cash_distributions_usd"] + o_net + g_net + v_net
        portfolio_values.append(total)
        p10, median, p90 = np.quantile(total, [0.1, 0.5, 0.9])
        rows.append({
            "label": label,
            "expected": round(float(total.mean()), 2),
            "median": round(float(median), 2),
            "p10": round(float(p10), 2),
            "p90": round(float(p90), 2),
            "components": {
                ticker: round(float(company_values[ticker][-1].mean()), 2) for ticker in ("O", "G", "V")
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

    fx, fx_date = current_fx(float(assumptions["usd_sgd"]))
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

    output = {
        "model_version": assumptions["version"],
        "model_policy": "News refreshes automatically; valuation assumptions change only after owner approval.",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "currency": {"usd_sgd": round(fx, 6), "as_of": fx_date},
        "cash_distributions_usd": assumptions["cash_distributions_usd"],
        "horizons": rows,
        "distribution": {
            "values_usd": round_list(centers, 2),
            "density_per_100k_usd": [round_list(np.asarray(values), 5) for values in densities],
        },
        "companies": company_summary,
        "capacity": {
            "ticker": "G",
            "target_mw": g["capacity_target_mw"],
            "expected_mw": round(float(capacity.mean()), 1),
            "median_mw": round(float(np.median(capacity)), 1),
            "probability_at_least_target": round(float((capacity >= g["capacity_target_mw"]).mean()), 4),
        },
        "targets": {
            "portfolio_sgd_500k_2026": round(float((portfolio_values[0] * fx >= 500000).mean()), 4),
            "portfolio_sgd_500k_2027": round(float((portfolio_values[-1] * fx >= 500000).mean()), 4),
            "portfolio_sgd_1m_2027": round(float((portfolio_values[-1] * fx >= 1000000).mean()), 4),
        },
        "method": {
            "paths": paths,
            "seed": assumptions["seed"],
            "central_surface_probability": 0.995,
        },
    }
    OUTPUT_PATH.write_text(json.dumps(output, indent=2) + "\n")
    print(f"Updated {OUTPUT_PATH.name} with model {assumptions['version']}")


if __name__ == "__main__":
    main()
