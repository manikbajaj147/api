from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone

import app as app_module
from services.openaq_client import OpenAQClient


def _fake_latest_city_measurements(self, city, parameters):
    values = {"pm25": 42.0, "pm10": 70.0, "no2": 24.0}
    return {
        p: {
            "value": values[p],
            "unit": "ug/m3",
            "lastUpdated": "2026-03-27T12:00",
            "location": city,
        }
        for p in parameters
        if p in values
    }


def _fake_city_time_series(self, city, parameters, date_from, date_to, limit=5000):
    points = []
    cursor = datetime.combine(date_from.date(), datetime.min.time(), tzinfo=timezone.utc)
    end = datetime.combine(date_to.date(), datetime.min.time(), tzinfo=timezone.utc)
    day_index = 0

    while cursor <= end and len(points) < limit:
        stamp = cursor.strftime("%Y-%m-%dT12:00")
        for p in parameters:
            base = {"pm25": 30.0, "pm10": 50.0, "no2": 20.0}.get(p, 10.0)
            points.append(
                {
                    "parameter": p,
                    "value": base + (day_index * 2.0),
                    "unit": "ug/m3",
                    "date": stamp,
                }
            )
        day_index += 1
        cursor += timedelta(days=1)

    return points


def _fake_city_forecast(self, city, parameters, days=5):
    now = datetime.now(timezone.utc)
    points = []
    for day_index in range(days):
        stamp = (now + timedelta(days=day_index)).strftime("%Y-%m-%dT12:00")
        for p in parameters:
            base = {"pm25": 35.0, "pm10": 60.0, "no2": 22.0}.get(p, 10.0)
            points.append(
                {
                    "parameter": p,
                    "value": base + day_index,
                    "unit": "ug/m3",
                    "date": stamp,
                }
            )
    return points


def _fake_city_metadata(self, city):
    seed = abs(hash(city)) % 1000
    return {
        "city": city,
        "latitude": float(-50 + (seed % 100)),
        "longitude": float(-170 + (seed % 340)),
        "country": "Testland",
        "country_code": "TS",
    }


def _build_client(monkeypatch, **env):
    monkeypatch.setenv("WARM_CACHE_ON_START", "0")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "120")
    monkeypatch.setenv("REQUIRE_API_KEY", "0")
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("API_KEYS", raising=False)

    for key, value in env.items():
        monkeypatch.setenv(key, value)

    monkeypatch.setattr(OpenAQClient, "get_latest_city_measurements", _fake_latest_city_measurements)
    monkeypatch.setattr(OpenAQClient, "get_city_time_series", _fake_city_time_series)
    monkeypatch.setattr(OpenAQClient, "get_city_forecast", _fake_city_forecast)
    monkeypatch.setattr(OpenAQClient, "get_city_metadata", _fake_city_metadata)

    importlib.reload(app_module)
    app = app_module.create_app()
    return app.test_client()


def test_forecast_endpoint_returns_5_days(monkeypatch):
    client = _build_client(monkeypatch)

    response = client.get("/api/forecast?city=Delhi&days=5")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["city"] == "Delhi"
    assert payload["days"] == 5
    assert len(payload["series"]) == 5
    assert payload["series"][0]["aqi"]["overall"] is not None


def test_trend_endpoint_returns_summary(monkeypatch):
    client = _build_client(monkeypatch)

    response = client.get("/api/trend?city=Delhi&days=7")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["city"] == "Delhi"
    assert payload["summary"]["direction"] in {"improving", "stable", "worsening"}
    assert len(payload["series"]) >= 3
    assert "moving_avg_3d" in payload["series"][0]


def test_api_key_is_enforced_when_enabled(monkeypatch):
    client = _build_client(monkeypatch, REQUIRE_API_KEY="1", API_KEY="secret-123")

    no_key = client.get("/api/cities")
    bad_key = client.get("/api/cities", headers={"X-API-Key": "invalid"})
    ok_key = client.get("/api/cities", headers={"X-API-Key": "secret-123"})

    assert no_key.status_code == 401
    assert bad_key.status_code == 401
    assert ok_key.status_code == 200


def test_rate_limit_returns_429(monkeypatch):
    client = _build_client(monkeypatch, RATE_LIMIT_PER_MINUTE="2")

    first = client.get("/api/cities")
    second = client.get("/api/cities")
    third = client.get("/api/cities")

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    body = third.get_json()
    assert body["error"] == "rate limit exceeded"


def test_map_cities_endpoint_returns_points(monkeypatch):
    client = _build_client(monkeypatch)

    response = client.get("/api/map-cities?limit=3")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["limit"] == 3
    assert payload["count"] >= 1
    point = payload["cities"][0]
    assert "latitude" in point
    assert "longitude" in point
    assert "aqi" in point
