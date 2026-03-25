# AQI Visualization Tool (Flask + Chart.js)

## Setup
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Test
```bash
python -m pytest -q
```

## Run
```bash
python app.py
```

Open http://localhost:5000

## Endpoints
- `/api/cities`
- `/api/current?city=Los%20Angeles`
- `/api/history?city=Los%20Angeles&days=30`
- `/api/extremes` (cleanest and worst AQI city among configured default cities)
- `/api/compare?cities=Los%20Angeles,New%20York,Chicago,Houston&days=30` (up to 10 cities)
- `POST /api/compare` with JSON body: `{"cities": ["Los Angeles", "New York", "Chicago"], "days": 30}`

If the upstream provider is temporarily unreachable, the API now serves stale cached data when available:
- `/api/current` and `/api/history` return cached payloads with `stale: true` and a `warning`.
- `/api/compare` may return `partial: true` with `warnings` per city, or a stale cached comparison.

## AQI
- Uses US EPA-style breakpoint approach for PM2.5 and PM10 (demo).
- NO₂ conversion is approximate when values are provided as µg/m³.

## Data Source
- Uses Open-Meteo geocoding and air-quality APIs (no key required).
- Optional env vars:
- AIR_QUALITY_BASE_URL (default: https://air-quality-api.open-meteo.com/v1)
- GEOCODING_BASE_URL (default: https://geocoding-api.open-meteo.com/v1)
- PROVIDER_TIMEOUT_SECONDS (default: 30)
- OpenAQ client retries provider calls on transient errors (default retries: 2)
- CACHE_TTL_SECONDS (default: 900)
- DEFAULT_HISTORY_DAYS (default: 90)
- DEFAULT_CITIES (default includes popular India cities, Punjab cities, and global cities)
- Global reference cities included in defaults: Reykjavik (clean-air reference), Lahore (high-AQI reference)
- WARM_CACHE_ON_START (default: 1)
- WARM_CACHE_DAYS (default: 90)
- WARM_CACHE_CITIES (default: DEFAULT_CITIES)