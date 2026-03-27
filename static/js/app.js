let aqiChart = null;
let compareChart = null;
let forecastChart = null;
let trendChart = null;
let cityMap = null;
let cityMapLayer = null;
let lastHistorySeries = [];
let lastCompareData = null;
let lastForecastSeries = [];
let lastTrendSeries = [];

const THEME_KEY = "aqi-theme";

function $(id) {
  return document.getElementById(id);
}

function getConfiguredApiKey() {
  const fromStorage = localStorage.getItem("aqi-api-key");
  if (fromStorage && fromStorage.trim()) return fromStorage.trim();
  const fromQuery = new URLSearchParams(window.location.search).get("api_key");
  return fromQuery ? fromQuery.trim() : "";
}

function withApiKeyHeaders(options = {}) {
  const apiKey = getConfiguredApiKey();
  if (!apiKey) return options;
  const headers = { ...(options.headers || {}), "X-API-Key": apiKey };
  return { ...options, headers };
}

function currentTheme() {
  return document.documentElement.getAttribute("data-theme") || "light";
}

function setTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem(THEME_KEY, theme);
  const label = $("themeToggleLabel");
  if (label) {
    label.textContent = theme === "dark" ? "Light mode" : "Dark mode";
  }
}

function initTheme() {
  const saved = localStorage.getItem(THEME_KEY);
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  setTheme(saved || (prefersDark ? "dark" : "light"));
}

function themePalette() {
  const rootStyle = getComputedStyle(document.documentElement);
  return {
    lineMain: rootStyle.getPropertyValue("--line-main").trim(),
    lineFill: rootStyle.getPropertyValue("--line-fill").trim(),
    grid: rootStyle.getPropertyValue("--chart-grid").trim(),
    axisText: rootStyle.getPropertyValue("--chart-text").trim(),
    text: rootStyle.getPropertyValue("--text").trim(),
  };
}

async function fetchJSON(url) {
  const r = await fetch(url, withApiKeyHeaders());
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || "Request failed");
  return data;
}

async function fetchJSONWithTimeout(url, options = {}, timeoutMs = 30000) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const r = await fetch(url, withApiKeyHeaders({ ...options, signal: controller.signal }));
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

function formatMapCity(point) {
  const country = point.country || point.country_code || "Not provided";
  const aqi = point.aqi ?? "—";
  const dominant = point.dominant || "—";
  return `<strong>${point.city}</strong><br/>Country: ${country}<br/>AQI: ${aqi}<br/>Dominant: ${dominant}`;
}

function initMapIfNeeded() {
  if (cityMap || typeof L === "undefined") return;

  cityMap = L.map("cityMap", {
    worldCopyJump: true,
    minZoom: 2,
  }).setView([18, 0], 2);

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  })
    .on("tileerror", () => {
      const warning = $("mapWarning");
      if (warning) {
        warning.hidden = false;
        warning.textContent =
          "Map tiles are blocked on this network. AQI city points are still loaded and clickable.";
      }
    })
    .addTo(cityMap);

  cityMapLayer = L.layerGroup().addTo(cityMap);
}

function renderCityMap(points) {
  initMapIfNeeded();
  if (!cityMap || !cityMapLayer) return;

  cityMapLayer.clearLayers();
  const validPoints = points.filter(
    (point) => point && Number.isFinite(point.latitude) && Number.isFinite(point.longitude)
  );

  validPoints.forEach((point) => {
    const color = colorForAQI(point.aqi);
    const marker = L.circleMarker([point.latitude, point.longitude], {
      radius: 6,
      color,
      fillColor: color,
      fillOpacity: 0.72,
      weight: 1,
    });
    marker.bindPopup(formatMapCity(point));
    marker.addTo(cityMapLayer);
  });

  const mapMeta = $("mapMeta");
  if (mapMeta) {
    mapMeta.textContent = `Mapped ${validPoints.length} cities by AQI.`;
  }

  const warning = $("mapWarning");
  if (warning && validPoints.length > 0 && warning.hidden === false) {
    warning.textContent =
      "Map tiles are blocked on this network. AQI city points are still loaded and clickable.";
  }

  if (validPoints.length > 1) {
    const bounds = L.latLngBounds(validPoints.map((point) => [point.latitude, point.longitude]));
    cityMap.fitBounds(bounds.pad(0.18));
  }
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

function renderAQIHistory(series, city = "", days = "") {
  lastHistorySeries = series;

  const historyTitle = $("historyTitle");
  if (historyTitle && city) {
    historyTitle.textContent = `AQI History - ${city} (last ${days} days)`;
  }

  const labels = series.map((p) => p.day);
  const values = series.map((p) => p.aqi.overall);
  const palette = themePalette();

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
          borderColor: palette.lineMain,
          backgroundColor: palette.lineFill,
          tension: 0.25,
          fill: true,
        },
      ],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { labels: { color: palette.text } },
      },
      interaction: { mode: "index", intersect: false },
      scales: {
        x: {
          ticks: { color: palette.axisText },
          grid: { color: palette.grid },
        },
        y: {
          beginAtZero: true,
          suggestedMax: 200,
          ticks: { color: palette.axisText },
          grid: { color: palette.grid },
        },
      },
    },
  });
}

function renderForecast(series) {
  lastForecastSeries = series;

  const labels = series.map((p) => p.day);
  const values = series.map((p) => p.aqi.overall);
  const palette = themePalette();

  const ctx = $("forecastChart").getContext("2d");
  if (forecastChart) forecastChart.destroy();

  forecastChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Forecast AQI",
          data: values,
          borderColor: "#ef6c00",
          backgroundColor: "rgba(239, 108, 0, 0.2)",
          borderDash: [7, 4],
          tension: 0.25,
          fill: true,
        },
      ],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { labels: { color: palette.text } },
      },
      interaction: { mode: "index", intersect: false },
      scales: {
        x: {
          ticks: { color: palette.axisText },
          grid: { color: palette.grid },
        },
        y: {
          beginAtZero: true,
          suggestedMax: 200,
          ticks: { color: palette.axisText },
          grid: { color: palette.grid },
        },
      },
    },
  });
}

function renderCompare(compareData) {
  lastCompareData = compareData;

  const cities = compareData.cities;
  const seriesByCity = compareData.data;

  const allDays = new Set();
  cities.forEach((c) => (seriesByCity[c] || []).forEach((p) => allDays.add(p.day)));
  const labels = Array.from(allDays).sort();

  const palette = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"];
  const theme = themePalette();

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
      plugins: {
        legend: { labels: { color: theme.text } },
      },
      interaction: { mode: "index", intersect: false },
      scales: {
        x: {
          ticks: { color: theme.axisText },
          grid: { color: theme.grid },
        },
        y: {
          beginAtZero: true,
          suggestedMax: 200,
          ticks: { color: theme.axisText },
          grid: { color: theme.grid },
        },
      },
    },
  });
}

function rerenderChartsForTheme() {
  if (lastHistorySeries.length > 0) {
    renderAQIHistory(lastHistorySeries);
  }
  if (lastCompareData) {
    renderCompare(lastCompareData);
  }
  if (lastForecastSeries.length > 0) {
    renderForecast(lastForecastSeries);
  }
  if (lastTrendSeries.length > 0) {
    renderTrend(lastTrendSeries);
  }
}

function renderTrend(series, city = "", days = "") {
  lastTrendSeries = series;

  const trendTitle = $("trendTitle");
  if (trendTitle && city) {
    trendTitle.textContent = `Trend - ${city} (last ${days} days)`;
  }

  const labels = series.map((p) => p.day);
  const aqiValues = series.map((p) => p.aqi);
  const movingAvg = series.map((p) => p.moving_avg_3d);
  const palette = themePalette();

  const ctx = $("trendChart").getContext("2d");
  if (trendChart) trendChart.destroy();

  trendChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "AQI",
          data: aqiValues,
          borderColor: "#6f42c1",
          backgroundColor: "rgba(111, 66, 193, 0.15)",
          tension: 0.2,
          spanGaps: true,
        },
        {
          label: "3-day moving avg",
          data: movingAvg,
          borderColor: "#00a36c",
          backgroundColor: "rgba(0, 163, 108, 0.15)",
          tension: 0.2,
          borderWidth: 2,
          spanGaps: true,
        },
      ],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { labels: { color: palette.text } },
      },
      interaction: { mode: "index", intersect: false },
      scales: {
        x: {
          ticks: { color: palette.axisText },
          grid: { color: palette.grid },
        },
        y: {
          beginAtZero: true,
          suggestedMax: 200,
          ticks: { color: palette.axisText },
          grid: { color: palette.grid },
        },
      },
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

    const forecastPromise = fetchJSONWithTimeout(
      `/api/forecast?city=${encodeURIComponent(city)}&days=5`,
      {},
      25000
    );

    const trendPromise = fetchJSONWithTimeout(
      `/api/trend?city=${encodeURIComponent(city)}&days=${encodeURIComponent(days)}`,
      {},
      25000
    );

    const extremesPromise = fetchJSONWithTimeout("/api/extremes", {}, 30000).catch(() => null);
    const mapPromise = fetchJSONWithTimeout("/api/map-cities?limit=60", {}, 30000).catch(() => null);

    const [current, history, forecast, trend, extremes, mapCities] = await Promise.all([
      currentPromise,
      historyPromise,
      forecastPromise,
      trendPromise,
      extremesPromise,
      mapPromise,
    ]);
    renderCurrent(current);
    renderAQIHistory(history.series, city, days);
    renderForecast(forecast.series || []);
    renderTrend(trend.series || [], city, days);
    if (extremes) renderExtremes(extremes);
    if (mapCities && Array.isArray(mapCities.cities)) {
      renderCityMap(mapCities.cities);
      if (mapCities.partial && mapCities.warnings) {
        const warning = $("mapWarning");
        if (warning) {
          warning.hidden = false;
          warning.textContent =
            "Some map city details are unavailable from provider right now. Try refresh in a minute.";
        }
      }
    }

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
  initTheme();

  $("themeToggle").addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    setTheme(next);
    rerenderChartsForTheme();
  });

  await loadCities();
  $("refresh").addEventListener("click", () => refresh());
  $("city").addEventListener("change", () => refresh());
  $("days").addEventListener("change", () => refresh());
  refresh();
});