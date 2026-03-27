import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from flask import Flask, jsonify, render_template, request

from services.aqi import aqi_from_concentrations
from services.cache import TTLCache
from services.openaq_client import OpenAQClient, OpenAQError


class FixedWindowRateLimiter:
    def __init__(self, limit_per_window: int, window_seconds: int = 60):
        self.limit_per_window = max(0, int(limit_per_window))
        self.window_seconds = max(1, int(window_seconds))
        self._lock = threading.Lock()
        self._windows: Dict[str, Dict[str, int]] = {}

    def check(self, key: str) -> tuple[bool, int]:
        if self.limit_per_window <= 0:
            return True, 0

        now = int(datetime.now(timezone.utc).timestamp())
        with self._lock:
            bucket = self._windows.get(key)
            if not bucket or now >= bucket["reset_at"]:
                self._windows[key] = {
                    "count": 1,
                    "reset_at": now + self.window_seconds,
                }
                return True, self.window_seconds

            if bucket["count"] >= self.limit_per_window:
                retry_after = max(1, bucket["reset_at"] - now)
                return False, retry_after

            bucket["count"] += 1
            retry_after = max(1, bucket["reset_at"] - now)
            return True, retry_after


def create_app() -> Flask:
    app = Flask(__name__)

    api_base_url = os.getenv("AIR_QUALITY_BASE_URL", "https://air-quality-api.open-meteo.com/v1")
    geocode_base_url = os.getenv("GEOCODING_BASE_URL", "https://geocoding-api.open-meteo.com/v1")
    provider_timeout_seconds = int(os.getenv("PROVIDER_TIMEOUT_SECONDS", "30"))
    default_days = int(os.getenv("DEFAULT_HISTORY_DAYS", "90"))
    default_cities = [
        c.strip()
        for c in os.getenv(
            "DEFAULT_CITIES",
            (
                "Delhi,Mumbai,Bengaluru,Hyderabad,Chennai,Kolkata,Pune,Ahmedabad,Jaipur,Lucknow,"
                "Chandigarh,Amritsar,Ludhiana,Jalandhar,Patiala,Bathinda,Mohali,"
                "New York,Los Angeles,London,Paris,Tokyo,Singapore,Dubai,Sydney,Toronto,"
                "Chicago,Houston,Phoenix,Philadelphia,San Antonio,San Diego,Dallas,San Jose,Austin,Seattle,"
                "San Francisco,Boston,Washington,Miami,Atlanta,Denver,Detroit,Minneapolis,Vancouver,Montreal,"
                "Ottawa,Mexico City,Sao Paulo,Rio de Janeiro,Buenos Aires,Lima,Bogota,Santiago,Madrid,Barcelona,"
                "Lisbon,Rome,Milan,Berlin,Munich,Hamburg,Amsterdam,Brussels,Zurich,Vienna,"
                "Prague,Warsaw,Budapest,Athens,Copenhagen,Stockholm,Oslo,Helsinki,Dublin,Edinburgh,"
                "Glasgow,Istanbul,Moscow,Saint Petersburg,Kyiv,Bucharest,Belgrade,Sofia,Zagreb,Ljubljana,"
                "Tallinn,Riga,Vilnius,Reykjavik,Cairo,Alexandria,Lagos,Nairobi,Johannesburg,Cape Town,"
                "Casablanca,Marrakech,Tunis,Algiers,Accra,Addis Ababa,Kigali,Doha,Abu Dhabi,Riyadh,"
                "Jeddah,Muscat,Kuwait City,Manama,Amman,Beirut,Jerusalem,Tel Aviv,Lahore,Bangkok,Kuala Lumpur,"
                "Jakarta,Manila,Ho Chi Minh City,Hanoi,Seoul,Busan,Taipei,Hong Kong,Shanghai,Beijing"
            ),
        ).split(",")
        if c.strip()
    ]
    warm_cache_enabled = os.getenv("WARM_CACHE_ON_START", "1") == "1"
    warm_cache_days = int(os.getenv("WARM_CACHE_DAYS", "90"))
    warm_cache_include_history = os.getenv("WARM_CACHE_INCLUDE_HISTORY", "0") == "1"
    warm_cache_max_cities = int(os.getenv("WARM_CACHE_MAX_CITIES", "25"))
    warm_cache_request_delay_seconds = float(os.getenv("WARM_CACHE_REQUEST_DELAY_SECONDS", "0.35"))
    warm_cache_abort_on_429 = os.getenv("WARM_CACHE_ABORT_ON_429", "1") == "1"
    warm_cache_cooldown_on_429_seconds = float(
        os.getenv("WARM_CACHE_COOLDOWN_ON_429_SECONDS", "20")
    )
    forecast_days_default = int(os.getenv("DEFAULT_FORECAST_DAYS", "5"))
    map_default_limit = int(os.getenv("MAP_DEFAULT_CITIES_LIMIT", "60"))
    map_max_limit = int(os.getenv("MAP_MAX_CITIES_LIMIT", "120"))
    warm_cache_cities = [
        c.strip()
        for c in os.getenv("WARM_CACHE_CITIES", ",".join(default_cities)).split(",")
        if c.strip()
    ]
    configured_api_keys = {
        key.strip()
        for key in (
            os.getenv("API_KEY", "") + "," + os.getenv("API_KEYS", "")
        ).split(",")
        if key.strip()
    }
    require_api_key = os.getenv("REQUIRE_API_KEY", "0") == "1"
    rate_limit_per_minute = int(os.getenv("RATE_LIMIT_PER_MINUTE", "120"))

    cache = TTLCache(default_ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "900")))
    rate_limiter = FixedWindowRateLimiter(limit_per_window=rate_limit_per_minute, window_seconds=60)
    client = OpenAQClient(
        base_url=api_base_url,
        geocode_url=geocode_base_url,
        timeout_seconds=provider_timeout_seconds,
    )

    @app.before_request
    def protect_api() -> Any:
        if not request.path.startswith("/api/"):
            return None

        api_key = request.headers.get("X-API-Key", "").strip()
        if not api_key:
            api_key = request.args.get("api_key", "").strip()

        if (require_api_key or configured_api_keys) and not api_key:
            return jsonify({"error": "missing API key"}), 401
        if configured_api_keys and api_key not in configured_api_keys:
            return jsonify({"error": "invalid API key"}), 401

        principal = api_key
        if not principal:
            fwd_for = request.headers.get("X-Forwarded-For", "")
            principal = (fwd_for.split(",")[0].strip() if fwd_for else request.remote_addr) or "unknown"

        allowed, retry_after = rate_limiter.check(f"api:{principal}")
        if not allowed:
            response = jsonify(
                {
                    "error": "rate limit exceeded",
                    "retry_after_seconds": retry_after,
                    "limit_per_minute": rate_limit_per_minute,
                }
            )
            response.status_code = 429
            response.headers["Retry-After"] = str(retry_after)
            return response
        return None

    def build_daily_series(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        # Daily buckets: last value per day per pollutant
        buckets: Dict[str, Dict[str, float]] = {}
        units_by_day: Dict[str, Dict[str, str]] = {}
        for pt in points:
            day = pt["date"][:10]
            buckets.setdefault(day, {})
            units_by_day.setdefault(day, {})
            buckets[day][pt["parameter"]] = pt["value"]
            units_by_day[day][pt["parameter"]] = pt.get("unit") or ""

        series: List[Dict[str, Any]] = []
        for day in sorted(buckets.keys()):
            conc = buckets[day]
            units = units_by_day.get(day, {})
            series.append(
                {"day": day, "concentrations": conc, "aqi": aqi_from_concentrations(conc, units=units)}
            )
        return series

    def stale_response(payload: Dict[str, Any], warning: str) -> Dict[str, Any]:
        out = dict(payload)
        out["stale"] = True
        out["warning"] = warning
        return out

    def get_current_payload(city: str) -> Dict[str, Any]:
        latest = client.get_latest_city_measurements(
            city=city, parameters=["pm25", "pm10", "no2"]
        )
        concentrations = {
            p: latest[p]["value"]
            for p in latest.keys()
            if latest[p].get("value") is not None
        }
        units = {p: (latest[p].get("unit") or "") for p in latest.keys()}
        return {
            "city": city,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "measurements": latest,
            "concentrations": concentrations,
            "aqi": aqi_from_concentrations(concentrations, units=units),
        }

    def get_history_payload(city: str, days_n: int) -> Dict[str, Any]:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days_n)
        points = client.get_city_time_series(
            city=city,
            parameters=["pm25", "pm10", "no2"],
            date_from=start,
            date_to=end,
        )
        return {
            "city": city,
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
            "days": days_n,
            "series": build_daily_series(points),
        }

    def get_forecast_payload(city: str, days_n: int) -> Dict[str, Any]:
        points = client.get_city_forecast(city=city, parameters=["pm25", "pm10", "no2"], days=days_n)
        series = build_daily_series(points)
        return {
            "city": city,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "days": days_n,
            "series": [
                {
                    "day": p["day"],
                    "concentrations": p["concentrations"],
                    "aqi": p["aqi"],
                }
                for p in series[:days_n]
            ],
        }

    def get_trend_payload(city: str, days_n: int) -> Dict[str, Any]:
        history_payload = get_history_payload(city, days_n)
        series = history_payload["series"]

        overall_values: List[int] = [
            int(item["aqi"]["overall"])
            for item in series
            if item.get("aqi") and item["aqi"].get("overall") is not None
        ]

        trend_points: List[Dict[str, Any]] = []
        rolling_values: List[int] = []
        for item in series:
            overall = item.get("aqi", {}).get("overall")
            if overall is not None:
                rolling_values.append(int(overall))
            window = rolling_values[-3:]
            moving_avg_3d = round(sum(window) / len(window), 1) if window else None
            trend_points.append({"day": item["day"], "aqi": overall, "moving_avg_3d": moving_avg_3d})

        change = None
        slope_per_day = None
        direction = "stable"
        if len(overall_values) >= 2:
            change = overall_values[-1] - overall_values[0]
            slope_per_day = round(change / max(1, len(overall_values) - 1), 2)
            if slope_per_day > 3:
                direction = "worsening"
            elif slope_per_day < -3:
                direction = "improving"

        return {
            "city": city,
            "days": days_n,
            "summary": {
                "direction": direction,
                "change": change,
                "slope_per_day": slope_per_day,
            },
            "series": trend_points,
        }

    @app.get("/")
    def index():
        return render_template("index.html", default_days=default_days)

    @app.get("/api/cities")
    def cities():
        return jsonify({"cities": default_cities})

    @app.get("/api/current")
    def current():
        city = request.args.get("city", "").strip()
        if not city:
            return jsonify({"error": "city is required"}), 400

        cache_key = f"current:{city.lower()}"
        cached = cache.get(cache_key)
        if cached:
            return jsonify(cached)

        try:
            payload = get_current_payload(city)
        except OpenAQError as e:
            stale = cache.get_stale(cache_key)
            if stale:
                return jsonify(stale_response(stale, f"Live provider unavailable: {str(e)}"))
            return jsonify({"error": str(e)}), 502

        cache.set(cache_key, payload)
        return jsonify(payload)

    @app.get("/api/extremes")
    def extremes():
        try:
            limit = max(2, min(len(default_cities), int(request.args.get("limit", str(len(default_cities))))))
        except ValueError:
            return jsonify({"error": "limit must be an integer"}), 400

        target_cities = default_cities[:limit]
        cache_key = f"extremes:{'|'.join([c.lower() for c in target_cities])}"
        cached = cache.get(cache_key)
        if cached:
            return jsonify(cached)

        scores: List[Dict[str, Any]] = []
        warnings: Dict[str, str] = {}

        def resolve_city_aqi(city: str) -> Dict[str, Any]:
            city_cache_key = f"current:{city.lower()}"
            current_payload = cache.get(city_cache_key)
            if not current_payload:
                current_payload = get_current_payload(city)
                cache.set(city_cache_key, current_payload)
            return current_payload

        with ThreadPoolExecutor(max_workers=min(8, len(target_cities))) as executor:
            futures = {executor.submit(resolve_city_aqi, city): city for city in target_cities}
            for future in as_completed(futures):
                city = futures[future]
                try:
                    current_payload = future.result()
                    overall = (current_payload.get("aqi") or {}).get("overall")
                    if overall is None:
                        warnings[city] = "AQI overall is unavailable"
                        continue
                    scores.append(
                        {
                            "city": city,
                            "aqi": overall,
                            "dominant": (current_payload.get("aqi") or {}).get("dominant"),
                            "timestamp": current_payload.get("timestamp"),
                        }
                    )
                except OpenAQError as e:
                    warnings[city] = str(e)

        if len(scores) < 2:
            return jsonify({"error": "insufficient AQI data to compute city extremes", "warnings": warnings}), 502

        scores.sort(key=lambda item: item["aqi"])
        payload: Dict[str, Any] = {
            "cities_evaluated": len(target_cities),
            "successful": len(scores),
            "cleanest": scores[0],
            "worst": scores[-1],
        }
        if warnings:
            payload["partial"] = True
            payload["warnings"] = warnings

        cache.set(cache_key, payload)
        return jsonify(payload)

    @app.get("/api/history")
    def history():
        city = request.args.get("city", "").strip()
        if not city:
            return jsonify({"error": "city is required"}), 400

        try:
            days_n = max(1, min(365, int(request.args.get("days", str(default_days)))))
        except ValueError:
            return jsonify({"error": "days must be an integer"}), 400

        cache_key = f"history:{city.lower()}:{days_n}"
        cached = cache.get(cache_key)
        if cached:
            return jsonify(cached)

        try:
            payload = get_history_payload(city, days_n)
        except OpenAQError as e:
            stale = cache.get_stale(cache_key)
            if stale:
                return jsonify(stale_response(stale, f"Live provider unavailable: {str(e)}"))
            return jsonify({"error": str(e)}), 502

        cache.set(cache_key, payload)
        return jsonify(payload)

    @app.get("/api/forecast")
    def forecast():
        city = request.args.get("city", "").strip()
        if not city:
            return jsonify({"error": "city is required"}), 400

        try:
            days_n = max(1, min(10, int(request.args.get("days", str(forecast_days_default)))))
        except ValueError:
            return jsonify({"error": "days must be an integer"}), 400

        cache_key = f"forecast:{city.lower()}:{days_n}"
        cached = cache.get(cache_key)
        if cached:
            return jsonify(cached)

        try:
            payload = get_forecast_payload(city, days_n)
        except OpenAQError as e:
            stale = cache.get_stale(cache_key)
            if stale:
                return jsonify(stale_response(stale, f"Live provider unavailable: {str(e)}"))
            return jsonify({"error": str(e)}), 502

        cache.set(cache_key, payload)
        return jsonify(payload)

    @app.get("/api/trend")
    def trend():
        city = request.args.get("city", "").strip()
        if not city:
            return jsonify({"error": "city is required"}), 400

        try:
            days_n = max(3, min(365, int(request.args.get("days", str(default_days)))))
        except ValueError:
            return jsonify({"error": "days must be an integer"}), 400

        cache_key = f"trend:{city.lower()}:{days_n}"
        cached = cache.get(cache_key)
        if cached:
            return jsonify(cached)

        try:
            payload = get_trend_payload(city, days_n)
        except OpenAQError as e:
            stale = cache.get_stale(cache_key)
            if stale:
                return jsonify(stale_response(stale, f"Live provider unavailable: {str(e)}"))
            return jsonify({"error": str(e)}), 502

        cache.set(cache_key, payload)
        return jsonify(payload)

    @app.get("/api/map-cities")
    def map_cities():
        try:
            limit = max(1, min(len(default_cities), map_max_limit, int(request.args.get("limit", str(map_default_limit)))))
        except ValueError:
            return jsonify({"error": "limit must be an integer"}), 400

        target_cities = default_cities[:limit]
        cache_key = f"map-cities:{limit}"
        stale_map = cache.get_stale(cache_key)
        cached = cache.get(cache_key)
        if cached:
            return jsonify(cached)

        out: Dict[str, Any] = {"cities": [], "count": 0, "limit": limit}
        warnings: Dict[str, str] = {}

        def build_map_point(city: str) -> Dict[str, Any]:
            city_cache_key = f"current:{city.lower()}"
            current_payload = cache.get(city_cache_key)
            if not current_payload:
                current_payload = get_current_payload(city)
                cache.set(city_cache_key, current_payload)

            meta = client.get_city_metadata(city)
            overall = (current_payload.get("aqi") or {}).get("overall")
            return {
                "city": meta["city"],
                "query_city": city,
                "country": meta.get("country", ""),
                "country_code": meta.get("country_code", ""),
                "latitude": meta["latitude"],
                "longitude": meta["longitude"],
                "aqi": overall,
                "dominant": (current_payload.get("aqi") or {}).get("dominant"),
                "timestamp": current_payload.get("timestamp"),
            }

        with ThreadPoolExecutor(max_workers=min(3, len(target_cities))) as executor:
            futures = {executor.submit(build_map_point, city): city for city in target_cities}
            for future in as_completed(futures):
                city = futures[future]
                try:
                    point = future.result()
                    if point.get("aqi") is None:
                        warnings[city] = "AQI overall is unavailable"
                        continue
                    out["cities"].append(point)
                except OpenAQError as e:
                    warnings[city] = str(e)

        out["cities"] = sorted(out["cities"], key=lambda point: point["aqi"])
        out["count"] = len(out["cities"])
        if warnings:
            out["partial"] = True
            out["warnings"] = warnings

        if out["count"] == 0:
            if stale_map:
                return jsonify(
                    stale_response(
                        stale_map,
                        "Live provider unavailable for map data; returning cached map payload.",
                    )
                )
            return jsonify({"error": "no AQI map data available", "details": warnings}), 502

        cache.set(cache_key, out)
        return jsonify(out)

    def parse_compare_cities() -> List[str]:
        cities_q = request.args.get("cities", "").strip()
        if cities_q:
            raw = [c.strip() for c in cities_q.split(",") if c.strip()]
            return list(dict.fromkeys(raw))

        body = request.get_json(silent=True) if request.is_json else None
        if body and isinstance(body, dict):
            cities = body.get("cities")
            if isinstance(cities, list):
                raw = [str(c).strip() for c in cities if str(c).strip()]
                return list(dict.fromkeys(raw))
            if isinstance(cities, str):
                raw = [c.strip() for c in cities.split(",") if c.strip()]
                return list(dict.fromkeys(raw))

        if request.data:
            try:
                parsed = json.loads(request.data.decode("utf-8"))
                if isinstance(parsed, dict):
                    cities = parsed.get("cities")
                    if isinstance(cities, list):
                        raw = [str(c).strip() for c in cities if str(c).strip()]
                        return list(dict.fromkeys(raw))
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass

        return []

    @app.route("/api/compare", methods=["GET", "POST"])
    def compare():
        cities_list = parse_compare_cities()
        if not cities_list:
            return jsonify({"error": "cities is required (comma-separated or JSON list)"}), 400
        if len(cities_list) < 2:
            return jsonify({"error": "provide at least 2 cities"}), 400
        if len(cities_list) > 10:
            return jsonify({"error": "max 10 cities for comparison"}), 400

        try:
            days_raw = request.args.get("days")
            if days_raw is None and request.is_json:
                body = request.get_json(silent=True) or {}
                days_raw = body.get("days")
            days_n = max(1, min(365, int(days_raw if days_raw is not None else default_days)))
        except ValueError:
            return jsonify({"error": "days must be an integer"}), 400

        cache_key = f"compare:{'|'.join([c.lower() for c in cities_list])}:{days_n}"
        stale_compare = cache.get_stale(cache_key)
        cached = cache.get(cache_key)
        if cached:
            return jsonify(cached)

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days_n)

        out: Dict[str, Any] = {"cities": cities_list, "days": days_n, "data": {}}
        compare_errors: Dict[str, str] = {}

        def build_compare_series(city: str) -> List[Dict[str, Any]]:
            points = client.get_city_time_series(
                city=city,
                parameters=["pm25", "pm10", "no2"],
                date_from=start,
                date_to=end,
            )
            series = build_daily_series(points)
            return [{"day": p["day"], "aqi": p["aqi"]} for p in series]

        with ThreadPoolExecutor(max_workers=min(4, len(cities_list))) as executor:
            futures = {executor.submit(build_compare_series, city): city for city in cities_list}
            for future in as_completed(futures):
                city = futures[future]
                city_cache_key = f"compare_city:{city.lower()}:{days_n}"
                try:
                    city_data = future.result()
                    out["data"][city] = city_data
                    cache.set(city_cache_key, city_data)
                except OpenAQError as e:
                    stale_city = cache.get_stale(city_cache_key)
                    if stale_city:
                        out["data"][city] = stale_city
                        compare_errors[city] = f"served from cache ({str(e)})"
                    else:
                        compare_errors[city] = str(e)

        # Keep output order stable with requested cities.
        out["data"] = {city: out["data"].get(city, []) for city in cities_list}

        successful = [city for city, series in out["data"].items() if series]
        if len(successful) < 2:
            if stale_compare:
                return jsonify(
                    stale_response(
                        stale_compare,
                        "Live provider unavailable for requested cities; returning cached comparison.",
                    )
                )
            return (
                jsonify(
                    {
                        "error": "Provider temporarily unavailable and insufficient cached data for comparison.",
                        "details": compare_errors,
                    }
                ),
                502,
            )

        if compare_errors:
            out["partial"] = True
            out["warnings"] = compare_errors

        cache.set(cache_key, out)
        return jsonify(out)

    def warm_history_and_current_cache() -> None:
        target_cities = warm_cache_cities
        if warm_cache_max_cities > 0:
            target_cities = target_cities[:warm_cache_max_cities]

        for idx, city in enumerate(target_cities):
            if idx > 0 and warm_cache_request_delay_seconds > 0:
                time.sleep(warm_cache_request_delay_seconds)

            try:
                current_payload = get_current_payload(city)
            except OpenAQError as e:
                if "HTTP 429" in str(e):
                    app.logger.warning(
                        "Warm cache received provider 429 after %s/%s cities. "
                        "Reduce WARM_CACHE_MAX_CITIES or increase WARM_CACHE_REQUEST_DELAY_SECONDS.",
                        idx,
                        len(target_cities),
                    )
                    if warm_cache_cooldown_on_429_seconds > 0:
                        time.sleep(warm_cache_cooldown_on_429_seconds)
                    if warm_cache_abort_on_429:
                        break
                app.logger.warning("Warm current cache failed for %s: %s", city, e)
                continue
            cache.set(f"current:{city.lower()}", current_payload)

            if warm_cache_include_history:
                try:
                    history_payload = get_history_payload(city, warm_cache_days)
                    cache.set(f"history:{city.lower()}:{warm_cache_days}", history_payload)
                except OpenAQError as e:
                    if "HTTP 429" in str(e):
                        app.logger.warning(
                            "Warm history cache received provider 429 after %s/%s cities.",
                            idx,
                            len(target_cities),
                        )
                        if warm_cache_cooldown_on_429_seconds > 0:
                            time.sleep(warm_cache_cooldown_on_429_seconds)
                        if warm_cache_abort_on_429:
                            break
                    app.logger.warning("Warm history cache failed for %s: %s", city, e)

    if warm_cache_enabled:
        threading.Thread(target=warm_history_and_current_cache, daemon=True).start()

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)