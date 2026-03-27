from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from typing import Any, ClassVar, Dict, List
from urllib.parse import urljoin
import time

import requests


class OpenAQError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenAQClient:
    base_url: str = "https://air-quality-api.open-meteo.com/v1"
    geocode_url: str = "https://geocoding-api.open-meteo.com/v1"
    timeout_seconds: int = 10
    max_retries: int = 2
    session: requests.Session = field(default_factory=requests.Session, repr=False, compare=False)

    CITY_COORDS: ClassVar[Dict[str, Dict[str, float | str]]] = {
        "delhi": {"name": "Delhi", "latitude": 28.6139, "longitude": 77.2090},
        "mumbai": {"name": "Mumbai", "latitude": 19.0760, "longitude": 72.8777},
        "bengaluru": {"name": "Bengaluru", "latitude": 12.9716, "longitude": 77.5946},
        "bangalore": {"name": "Bengaluru", "latitude": 12.9716, "longitude": 77.5946},
        "hyderabad": {"name": "Hyderabad", "latitude": 17.3850, "longitude": 78.4867},
        "chennai": {"name": "Chennai", "latitude": 13.0827, "longitude": 80.2707},
        "kolkata": {"name": "Kolkata", "latitude": 22.5726, "longitude": 88.3639},
        "pune": {"name": "Pune", "latitude": 18.5204, "longitude": 73.8567},
        "ahmedabad": {"name": "Ahmedabad", "latitude": 23.0225, "longitude": 72.5714},
        "jaipur": {"name": "Jaipur", "latitude": 26.9124, "longitude": 75.7873},
        "lucknow": {"name": "Lucknow", "latitude": 26.8467, "longitude": 80.9462},
        "chandigarh": {"name": "Chandigarh", "latitude": 30.7333, "longitude": 76.7794},
        "amritsar": {"name": "Amritsar", "latitude": 31.6340, "longitude": 74.8723},
        "ludhiana": {"name": "Ludhiana", "latitude": 30.9010, "longitude": 75.8573},
        "jalandhar": {"name": "Jalandhar", "latitude": 31.3260, "longitude": 75.5762},
        "patiala": {"name": "Patiala", "latitude": 30.3398, "longitude": 76.3869},
        "bathinda": {"name": "Bathinda", "latitude": 30.2110, "longitude": 74.9455},
        "mohali": {"name": "Mohali", "latitude": 30.7046, "longitude": 76.7179},
        "new york": {"name": "New York", "latitude": 40.7128, "longitude": -74.0060},
        "los angeles": {"name": "Los Angeles", "latitude": 34.0522, "longitude": -118.2437},
        "london": {"name": "London", "latitude": 51.5074, "longitude": -0.1278},
        "paris": {"name": "Paris", "latitude": 48.8566, "longitude": 2.3522},
        "tokyo": {"name": "Tokyo", "latitude": 35.6762, "longitude": 139.6503},
        "singapore": {"name": "Singapore", "latitude": 1.3521, "longitude": 103.8198},
        "dubai": {"name": "Dubai", "latitude": 25.2048, "longitude": 55.2708},
        "sydney": {"name": "Sydney", "latitude": -33.8688, "longitude": 151.2093},
        "toronto": {"name": "Toronto", "latitude": 43.6532, "longitude": -79.3832},
        "lahore": {"name": "Lahore", "latitude": 31.5204, "longitude": 74.3587},
    }

    def _get(self, base_url: str, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
        last_error: Exception | None = None
        timeout = (3, self.timeout_seconds)

        for attempt in range(self.max_retries + 1):
            try:
                r = self.session.get(url, params=params, timeout=timeout)
            except requests.RequestException as e:
                last_error = e
                if attempt < self.max_retries:
                    time.sleep(0.3 * (attempt + 1))
                    continue
                raise OpenAQError(
                    "Network error contacting air-quality provider. "
                    "Please retry shortly or use cached results if available."
                ) from e

            if r.status_code == 429:
                raise OpenAQError("Provider rate limit exceeded (HTTP 429). Try again later.")
            if r.status_code >= 500 and attempt < self.max_retries:
                time.sleep(0.3 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise OpenAQError(f"Provider request failed: HTTP {r.status_code}: {r.text[:300]}")

            try:
                return r.json()
            except ValueError as e:
                last_error = e
                if attempt < self.max_retries:
                    time.sleep(0.3 * (attempt + 1))
                    continue
                raise OpenAQError("Invalid JSON response from provider.") from e

        raise OpenAQError(f"Provider request failed after retries: {last_error}")

    @lru_cache(maxsize=512)
    def _resolve_city(self, city: str) -> Dict[str, Any]:
        city_key = city.strip().lower()
        if city_key in self.CITY_COORDS:
            # Keep deterministic coordinates for known cities, but enrich with
            # country metadata from geocoding when available.
            hardcoded = dict(self.CITY_COORDS[city_key])
            try:
                data = self._get(
                    self.geocode_url,
                    "/search",
                    {
                        "name": hardcoded.get("name", city),
                        "count": 1,
                        "language": "en",
                        "format": "json",
                    },
                )
                results = data.get("results") or []
                if results:
                    geo = results[0]
                    hardcoded["country"] = geo.get("country") or hardcoded.get("country", "")
                    hardcoded["country_code"] = geo.get("country_code") or hardcoded.get(
                        "country_code", ""
                    )
            except OpenAQError:
                # Fall back to hardcoded metadata if geocoding is unavailable.
                pass
            return hardcoded

        data = self._get(
            self.geocode_url,
            "/search",
            {"name": city, "count": 1, "language": "en", "format": "json"},
        )
        results = data.get("results") or []
        if not results:
            raise OpenAQError(f"City '{city}' was not found.")
        return results[0]

    def get_city_metadata(self, city: str) -> Dict[str, Any]:
        city_info = self._resolve_city(city)
        lat = city_info.get("latitude")
        lon = city_info.get("longitude")
        if lat is None or lon is None:
            raise OpenAQError(f"City coordinates missing for '{city}'.")

        country = city_info.get("country", "")
        country_code = city_info.get("country_code", "")

        # For static CITY_COORDS entries we may not have country fields; fill from geocoding.
        if not country or not country_code:
            try:
                data = self._get(
                    self.geocode_url,
                    "/search",
                    {"name": city, "count": 1, "language": "en", "format": "json"},
                )
                results = data.get("results") or []
                if results:
                    match = results[0]
                    country = country or match.get("country", "")
                    country_code = country_code or match.get("country_code", "")
            except OpenAQError:
                # Keep metadata usable even if geocoding fallback fails.
                pass

        return {
            "city": city_info.get("name", city),
            "latitude": float(lat),
            "longitude": float(lon),
            "country": country,
            "country_code": country_code,
        }

    @staticmethod
    def _parameter_map() -> Dict[str, str]:
        return {
            "pm25": "pm2_5",
            "pm10": "pm10",
            "no2": "nitrogen_dioxide",
        }

    @staticmethod
    def _unit_map() -> Dict[str, str]:
        return {
            "pm25": "ug/m3",
            "pm10": "ug/m3",
            "no2": "ug/m3",
        }

    def get_latest_city_measurements(
        self, city: str, parameters: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        city_info = self._resolve_city(city)
        lat = city_info.get("latitude")
        lon = city_info.get("longitude")
        if lat is None or lon is None:
            raise OpenAQError(f"City coordinates missing for '{city}'.")

        pmap = self._parameter_map()
        hourly_fields = [pmap[p] for p in parameters if p in pmap]
        if not hourly_fields:
            raise OpenAQError("No supported parameters were requested.")

        data = self._get(
            self.base_url,
            "/air-quality",
            {
                "latitude": lat,
                "longitude": lon,
                "current": ",".join(hourly_fields),
                "timezone": "UTC",
            },
        )
        current = data.get("current") or {}
        when = current.get("time")
        if not current or when is None:
            raise OpenAQError(f"No latest results found for city '{city}'.")

        best: Dict[str, Dict[str, Any]] = {}
        units = self._unit_map()
        location_name = city_info.get("name", city)
        for p in parameters:
            src_key = pmap.get(p)
            if not src_key:
                continue
            value = current.get(src_key)
            if value is None:
                continue
            best[p] = {
                "value": float(value),
                "unit": units.get(p, ""),
                "lastUpdated": when,
                "location": location_name,
            }

        if not best:
            raise OpenAQError(f"No pollutant measurements found for city '{city}'.")
        return best

    def get_city_time_series(
        self,
        city: str,
        parameters: List[str],
        date_from: datetime,
        date_to: datetime,
        limit: int = 5000,
    ) -> List[Dict[str, Any]]:
        city_info = self._resolve_city(city)
        lat = city_info.get("latitude")
        lon = city_info.get("longitude")
        if lat is None or lon is None:
            raise OpenAQError(f"City coordinates missing for '{city}'.")

        pmap = self._parameter_map()
        inverse_pmap = {v: k for k, v in pmap.items()}
        units = self._unit_map()

        hourly_fields = [pmap[p] for p in parameters if p in pmap]
        if not hourly_fields:
            raise OpenAQError("No supported parameters were requested.")

        data = self._get(
            self.base_url,
            "/air-quality",
            {
                "latitude": lat,
                "longitude": lon,
                "hourly": ",".join(hourly_fields),
                "timezone": "UTC",
                "start_date": date_from.date().isoformat(),
                "end_date": date_to.date().isoformat(),
            },
        )
        hourly = data.get("hourly") or {}
        times = hourly.get("time") or []

        all_points: List[Dict[str, Any]] = []
        for idx, timestamp in enumerate(times):
            for src_key in hourly_fields:
                values = hourly.get(src_key) or []
                if idx >= len(values):
                    continue
                value = values[idx]
                if value is None:
                    continue
                param = inverse_pmap.get(src_key)
                if param is None:
                    continue
                all_points.append(
                    {
                        "parameter": param,
                        "value": float(value),
                        "unit": units.get(param, ""),
                        "date": timestamp,
                    }
                )
                if len(all_points) >= limit:
                    break
            if len(all_points) >= limit:
                break

        if not all_points:
            raise OpenAQError(f"No historical measurements found for city '{city}'.")
        return all_points

    def get_city_forecast(self, city: str, parameters: List[str], days: int = 5) -> List[Dict[str, Any]]:
        city_info = self._resolve_city(city)
        lat = city_info.get("latitude")
        lon = city_info.get("longitude")
        if lat is None or lon is None:
            raise OpenAQError(f"City coordinates missing for '{city}'.")

        pmap = self._parameter_map()
        inverse_pmap = {v: k for k, v in pmap.items()}
        units = self._unit_map()

        hourly_fields = [pmap[p] for p in parameters if p in pmap]
        if not hourly_fields:
            raise OpenAQError("No supported parameters were requested.")

        data = self._get(
            self.base_url,
            "/air-quality",
            {
                "latitude": lat,
                "longitude": lon,
                "hourly": ",".join(hourly_fields),
                "timezone": "UTC",
                "forecast_days": max(1, min(10, int(days))),
            },
        )
        hourly = data.get("hourly") or {}
        times = hourly.get("time") or []

        all_points: List[Dict[str, Any]] = []
        for idx, timestamp in enumerate(times):
            for src_key in hourly_fields:
                values = hourly.get(src_key) or []
                if idx >= len(values):
                    continue
                value = values[idx]
                if value is None:
                    continue
                param = inverse_pmap.get(src_key)
                if param is None:
                    continue
                all_points.append(
                    {
                        "parameter": param,
                        "value": float(value),
                        "unit": units.get(param, ""),
                        "date": timestamp,
                    }
                )

        if not all_points:
            raise OpenAQError(f"No forecast measurements found for city '{city}'.")
        return all_points