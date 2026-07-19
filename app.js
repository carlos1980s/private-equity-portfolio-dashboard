(() => {
  const state = { model: null, news: null, currency: "USD" };
  const plot = document.getElementById("portfolio-surface");
  const initialCamera = { eye: { x: 1.58, y: 1.48, z: 1.14 } };

  const money = (value, currency = state.currency, compact = false) => {
    const amount = currency === "SGD" ? value * state.model.currency.usd_sgd : value;
    return new Intl.NumberFormat("en-SG", {
      style: "currency",
      currency,
      notation: compact ? "compact" : "standard",
      maximumFractionDigits: compact ? 1 : 0,
    }).format(amount);
  };

  const percent = value => new Intl.NumberFormat("en-SG", {
    style: "percent",
    maximumFractionDigits: 1,
  }).format(value);

  const formatDate = value => {
    if (!value || value === "fallback") return "fallback rate";
    const date = new Date(value.length === 10 ? `${value}T00:00:00Z` : value);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat("en-SG", { dateStyle: "medium", timeZone: "Asia/Singapore" }).format(date);
  };

  const setText = (id, value) => { document.getElementById(id).textContent = value; };
  const escapeHtml = value => String(value).replace(/[&<>'"]/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
  const safeSourceUrl = value => {
    try {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol) ? url.href : "";
    } catch {
      return "";
    }
  };

  function renderMetrics() {
    const first = state.model.horizons[0];
    const last = state.model.horizons.at(-1);
    const secondary = state.currency === "USD" ? "SGD" : "USD";
    setText("expected-2026", money(first.expected));
    setText("expected-2026-secondary", `${money(first.expected, secondary)} in ${secondary}`);
    setText("expected-2027", money(last.expected));
    setText("expected-2027-secondary", `${money(last.expected, secondary)} in ${secondary}`);
    setText("range-2027", `${money(last.p10, state.currency, true)} – ${money(last.p90, state.currency, true)}`);
    setText("median-2027", `Median ${money(last.median)}`);
    setText("cash-distributions", money(state.model.cash_distributions_usd));
  }

  function nearestDensity(density, values, amount) {
    let nearest = 0;
    for (let index = 1; index < values.length; index += 1) {
      if (Math.abs(values[index] - amount) < Math.abs(values[nearest] - amount)) nearest = index;
    }
    return density[nearest];
  }

  function renderPlot() {
    const factor = state.currency === "SGD" ? state.model.currency.usd_sgd : 1;
    const densityFactor = state.currency === "SGD" ? 1 / state.model.currency.usd_sgd : 1;
    const x = state.model.distribution.values_usd.map(value => value * factor / 1000);
    const density = state.model.distribution.density_per_100k_usd.map(row => row.map(value => value * densityFactor));
    const dates = state.model.horizons.map(row => row.label);
    const time = dates.map((_, index) => index);
    const axisCurrency = state.currency === "USD" ? "US$" : "S$";

    const surface = {
      type: "surface",
      x: density.map(() => x),
      y: density,
      z: time.map(value => x.map(() => value)),
      surfacecolor: time.map(value => x.map(() => value)),
      colorscale: [[0, "#234f86"], [.55, "#2d87a8"], [1, "#35d3c9"]],
      opacity: .54,
      showscale: false,
      text: dates.map(date => x.map(() => date)),
      hovertemplate: `%{text}<br>Total value: ${axisCurrency}%{x:,.0f}k<br>Density: %{y:.3f} per ${axisCurrency}100k<extra></extra>`,
      name: "Probability surface",
    };

    const ridges = dates.map((date, index) => ({
      type: "scatter3d",
      mode: "lines",
      x,
      y: density[index],
      z: x.map(() => index),
      line: { color: index === dates.length - 1 ? "#55e0d5" : "#58a6ff", width: 4 },
      opacity: .9,
      name: date,
      showlegend: false,
      hovertemplate: `${date}<br>Total value: ${axisCurrency}%{x:,.0f}k<br>Density: %{y:.3f} per ${axisCurrency}100k<extra></extra>`,
    }));

    const pathTrace = (key, name, color, dash, showlegend) => {
      const amounts = state.model.horizons.map(row => row[key] * factor / 1000);
      const trace = {
        type: "scatter3d",
        mode: "lines",
        x: amounts,
        y: amounts.map((amount, index) => nearestDensity(density[index], x, amount)),
        z: time,
        line: { color, width: key === "expected" ? 8 : 4, dash },
        name,
        showlegend,
        customdata: state.model.horizons.map((row, index) => `${dates[index]} · ${axisCurrency}${Math.round(row[key] * factor / 1000)}k`),
        hovertemplate: "%{customdata}<extra></extra>",
      };
      if (key === "expected") {
        trace.mode = "lines+markers+text";
        trace.text = [`${axisCurrency}${amounts[0].toFixed(0)}k`, "", "", "", `${axisCurrency}${amounts.at(-1).toFixed(0)}k`];
        trace.textposition = "top center";
        trace.textfont = { color: "#f5f8ff", size: 12 };
        trace.marker = { color, size: 5 };
      }
      return trace;
    };

    const data = [
      surface,
      ...ridges,
      pathTrace("expected", "Expected value", "#f7b955", "solid", true),
      pathTrace("p10", "P10 / P90", "#c4cedd", "dash", true),
      pathTrace("p90", "P90", "#c4cedd", "dash", false),
    ];

    const layout = {
      autosize: true,
      margin: { l: 0, r: 0, t: 8, b: 0 },
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      font: { family: "Inter, ui-sans-serif, system-ui, sans-serif", color: "#dce6f5" },
      legend: { orientation: "h", x: .02, y: .98, bgcolor: "rgba(0,0,0,0)", font: { size: 11 } },
      scene: {
        bgcolor: "rgba(0,0,0,0)",
        dragmode: "orbit",
        aspectmode: "manual",
        aspectratio: { x: 1.62, y: .92, z: 1.03 },
        camera: initialCamera,
        xaxis: {
          title: { text: `Total portfolio value (${state.currency})` },
          tickprefix: axisCurrency,
          ticksuffix: "k",
          gridcolor: "rgba(155,175,205,.22)",
          zerolinecolor: "rgba(155,175,205,.28)",
          color: "#bcc8da",
        },
        yaxis: {
          title: { text: `Probability density / ${axisCurrency}100k` },
          gridcolor: "rgba(155,175,205,.22)",
          zerolinecolor: "rgba(155,175,205,.28)",
          color: "#bcc8da",
          rangemode: "tozero",
        },
        zaxis: {
          title: { text: "Forecast time" },
          tickvals: time,
          ticktext: dates,
          gridcolor: "rgba(155,175,205,.22)",
          zerolinecolor: "rgba(155,175,205,.28)",
          color: "#bcc8da",
          range: [-.1, 4.2],
        },
      },
    };

    Plotly.purge(plot);
    Plotly.newPlot(plot, data, layout, {
      responsive: true,
      displaylogo: false,
      scrollZoom: true,
      modeBarButtonsToRemove: ["toImage", "sendDataToCloud", "lasso2d", "select2d"],
    });
  }

  function renderCompanies() {
    const container = document.getElementById("company-rows");
    container.replaceChildren(...state.model.companies.map(company => {
      const row = document.createElement("div");
      row.className = "company-row";
      row.innerHTML = `
        <span class="ticker">${company.ticker}</span>
        <span class="company-copy">
          <strong>${percent(company.economic_interest)} economic interest</strong>
          <span>Company median ${money(company.valuation_median_2027, "USD", true)} at YE 2027</span>
        </span>
        <span class="company-value">
          <strong>${money(company.expected_2027)}</strong>
          <span>${money(company.expected_2026)} at YE 2026</span>
        </span>`;
      return row;
    }));
  }

  function renderCapacity() {
    const capacity = state.model.capacity;
    const angle = Math.round(capacity.probability_at_least_target * 360);
    document.getElementById("capacity-ring").style.setProperty("--capacity-angle", `${angle}deg`);
    setText("capacity-probability", percent(capacity.probability_at_least_target));
    setText("capacity-expected", `${capacity.expected_mw} MW`);
    setText("capacity-median", `${capacity.median_mw} MW`);
    setText("capacity-target", `${capacity.target_mw} MW`);
    setText("target-500-2026", percent(state.model.targets.portfolio_sgd_500k_2026));
    setText("target-500-2027", percent(state.model.targets.portfolio_sgd_500k_2027));
    setText("target-1m-2027", percent(state.model.targets.portfolio_sgd_1m_2027));
  }

  function renderNews() {
    const grid = document.getElementById("news-grid");
    setText("news-updated", state.news.updated_at ? `Updated ${formatDate(state.news.updated_at)}` : "Refresh pending");
    if (!state.news.items?.length) {
      grid.innerHTML = '<p class="empty-news">No material signals are currently waiting for review.</p>';
      return;
    }
    grid.replaceChildren(...state.news.items.map(item => {
      const card = document.createElement("article");
      card.className = "news-card";
      const sourceUrl = safeSourceUrl(item.source_url);
      const sourceLabel = escapeHtml(item.source_label);
      const link = sourceUrl ? `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer">${sourceLabel}</a>` : sourceLabel;
      card.innerHTML = `
        <div class="news-card-top">
          <span class="news-ticker">${escapeHtml(item.company)}</span>
          <span class="impact impact-${escapeHtml(item.impact)}">${escapeHtml(item.impact)}</span>
        </div>
        <h3>${escapeHtml(item.headline)}</h3>
        <p>${escapeHtml(item.summary)}</p>
        <div class="news-card-footer">
          <span>${escapeHtml(formatDate(item.date))} · ${link}</span>
          <span class="${item.review_status === "pending" ? "pending" : ""}">${item.review_status === "pending" ? "Review" : "Logged"}</span>
        </div>`;
      return card;
    }));
  }

  function renderMeta() {
    setText("model-version", state.model.model_version);
    const current = state.news.refresh_status === "current";
    setText("refresh-status", current ? "News current" : "News refresh pending");
    document.querySelector(".status-dot").style.background = current ? "var(--positive)" : "var(--amber)";
    setText("fx-label", `USD/SGD ${state.model.currency.usd_sgd.toFixed(4)} · ${formatDate(state.model.currency.as_of)}`);
    setText("path-count", `${new Intl.NumberFormat("en-SG").format(state.model.method.paths)} paths`);
    setText("generated-label", `Generated ${formatDate(state.model.generated_at)}`);
  }

  function renderAll() {
    renderMetrics();
    renderPlot();
    renderCompanies();
    renderCapacity();
    renderNews();
    renderMeta();
  }

  document.querySelectorAll("[data-currency]").forEach(button => {
    button.addEventListener("click", () => {
      state.currency = button.dataset.currency;
      document.querySelectorAll("[data-currency]").forEach(peer => {
        const active = peer === button;
        peer.classList.toggle("is-active", active);
        peer.setAttribute("aria-pressed", String(active));
      });
      renderMetrics();
      renderCompanies();
      renderPlot();
    });
  });

  document.getElementById("reset-camera").addEventListener("click", () => {
    Plotly.relayout(plot, { "scene.camera": initialCamera });
  });

  Promise.all([
    fetch("data/model.json", { cache: "no-store" }).then(response => {
      if (!response.ok) throw new Error("Model data unavailable");
      return response.json();
    }),
    fetch("data/news.json", { cache: "no-store" }).then(response => {
      if (!response.ok) throw new Error("News data unavailable");
      return response.json();
    }),
  ]).then(([model, news]) => {
    state.model = model;
    state.news = news;
    renderAll();
  }).catch(error => {
    plot.innerHTML = `<p class="empty-news">The dashboard data could not be loaded. ${error.message}</p>`;
    setText("refresh-status", "Data unavailable");
  });
})();
