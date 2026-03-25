let aqiChart = null;
let compareChart = null;

function $(id) {
  return document.getElementById(id);
}

async function fetchJSON(url) {
  const r = await fetch(url);
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || "Request failed");
  return data;
}

async function fetchJSONWithTimeout(url, options = {}, timeoutMs = 30000) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const r = await fetch(url, { ...options, signal: controller.signal });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || "Request failed");
    return data;
  } catch (err) {
    if (err && err.name === "AbortError") {
      throw new Error("Request timed out. Please try fewer cities or fewer days.");
    }
    throw err;
  } finally {
    clearTimeout(timeoutId);
  }
}

function colorForAQI(aqi) {
  if (aqi === null || aqi === undefined) return "#999";
  if (aqi <= 50) return "#00e400";
  if (aqi <= 100) return "#ffff00";
  if (aqi <= 150) return "#ff7e00";
  if (aqi <= 200) return "#ff0000";
  if (aqi <= 300) return "#8f3f97";
  return "#7e0023";
}

function setError(message) {
  const box = $("errorBox");
  if (!message) {
    box.hidden = true;
    box.textContent = "";
    return;
  }
  box.hidden = false;
  box.textContent = message;
}

function parseCompareCities(inputValue) {
  return inputValue
    .split(",")
    .map((city) => city.trim())
    .filter((city) => city.length > 0);
}

function parseCompareInput(inputValue) {
  const value = inputValue.trim();
  if (!value) return [];

  if (value.startsWith("[") || value.startsWith("{")) {
    try {
      const parsed = JSON.parse(value);
      if (Array.isArray(parsed)) {
        return parsed.map((city) => String(city).trim()).filter((city) => city.length > 0);
      }
      if (parsed && Array.isArray(parsed.cities)) {
        return parsed.cities
          .map((city) => String(city).trim())
          .filter((city) => city.length > 0);
      }
    } catch {
      // Fall back to comma-separated parsing when input is not valid JSON.
    }
  }

  return parseCompareCities(value);
}

async function loadCities() {
  const data = await fetchJSON("/api/cities");
  const cityInput = $("city");
  const cityList = $("cityList");
  cityList.innerHTML = "";
  data.cities.forEach((c) => {
    const opt = document.createElement("option");
    opt.value = c;
    cityList.appendChild(opt);
  });

  if (!cityInput.value && data.cities.length > 0) {
    cityInput.value = data.cities[0];
  }
}

function renderCurrent(payload) {
  const overall = payload.aqi?.overall ?? null;
  const dominant = payload.aqi?.dominant ?? "—";
  const sub = payload.aqi?.subindices ?? {};

  $("aqiValue").textContent = overall ?? "—";
  $("aqiValue").style.background = colorForAQI(overall);

  $("aqiMeta").textContent = payload.measurements
    ? `Updated: ${Object.values(payload.measurements)[0]?.lastUpdated ?? "—"}`
    : "—";

  $("dominant").textContent = dominant;
  $("subindices").textContent =
    `PM2.5: ${sub.pm25 ?? "—"} | PM10: ${sub.pm10 ?? "—"} | NO2: ${sub.no2 ?? "—"}`;
}

function renderAQIHistory(series) {
  const labels = series.map((p) => p.day);
  const values = series.map((p) => p.aqi.overall);

  const ctx = $("aqiChart").getContext("2d");
  if (aqiChart) aqiChart.destroy();

  aqiChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "AQI",
          data: values,
          borderColor: "#1f77b4",
          backgroundColor: "rgba(31, 119, 180, 0.15)",
          tension: 0.25,
          fill: true,
        },
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      scales: { y: { beginAtZero: true, suggestedMax: 200 } },
    },
  });
}

function renderCompare(compareData) {
  const cities = compareData.cities;
  const seriesByCity = compareData.data;

  const allDays = new Set();
  cities.forEach((c) => (seriesByCity[c] || []).forEach((p) => allDays.add(p.day)));
  const labels = Array.from(allDays).sort();

  const palette = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"];

  const datasets = cities.map((c, idx) => {
    const map = new Map((seriesByCity[c] || []).map((p) => [p.day, p.aqi.overall]));
    return {
      label: c,
      data: labels.map((d) => map.get(d) ?? null),
      borderColor: palette[idx % palette.length],
      tension: 0.25,
      spanGaps: true,
    };
  });

  const ctx = $("compareChart").getContext("2d");
  if (compareChart) compareChart.destroy();

  compareChart = new Chart(ctx, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      scales: { y: { beginAtZero: true, suggestedMax: 200 } },
    },
  });
}

function renderExtremes(payload) {
  const cleanest = payload?.cleanest;
  const worst = payload?.worst;

  if (!cleanest || !worst) {
    $("cleanestCity").textContent = "Cleanest: —";
    $("worstCity").textContent = "Worst: —";
    return;
  }

  $("cleanestCity").textContent = `Cleanest: ${cleanest.city} (AQI ${cleanest.aqi})`;
  $("worstCity").textContent = `Worst: ${worst.city} (AQI ${worst.aqi})`;
}

async function refresh() {
  setError("");

  const city = $("city").value.trim();
  const days = $("days").value;
  const compare = $("compare").value.trim();

  if (!city) {
    setError("Please enter a city name.");
    return;
  }

  try {
    const currentPromise = fetchJSONWithTimeout(
      `/api/current?city=${encodeURIComponent(city)}`,
      {},
      15000
    );

    const historyPromise = fetchJSONWithTimeout(
      `/api/history?city=${encodeURIComponent(city)}&days=${encodeURIComponent(days)}`,
      {},
      25000
    );

    const extremesPromise = fetchJSONWithTimeout("/api/extremes", {}, 30000).catch(() => null);

    const [current, history, extremes] = await Promise.all([
      currentPromise,
      historyPromise,
      extremesPromise,
    ]);
    renderCurrent(current);
    renderAQIHistory(history.series);
    if (extremes) renderExtremes(extremes);

    if (compare.length > 0) {
      const compareCities = [city, ...parseCompareInput(compare)].filter(
        (c, index, arr) => arr.indexOf(c) === index
      );

      if (compareCities.length < 2) {
        setError("Please add at least one additional city to compare.");
        return;
      }

      if (compareCities.length > 10) {
        setError("Please compare up to 10 cities at a time.");
        return;
      }

      const cmp = await fetchJSONWithTimeout(
        "/api/compare",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ cities: compareCities, days: Number(days) }),
        },
        30000
      );
      renderCompare(cmp);
    }
  } catch (err) {
    setError(err?.message || "Failed to load AQI data. Please try again.");
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  await loadCities();
  $("refresh").addEventListener("click", () => refresh());
  $("city").addEventListener("change", () => refresh());
  $("days").addEventListener("change", () => refresh());
  refresh();
});