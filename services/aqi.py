from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class Breakpoint:
    c_low: float
    c_high: float
    i_low: int
    i_high: int


# US EPA AQI breakpoints (simplified) for:
# - PM2.5 (24-hr), µg/m³
# - PM10 (24-hr), µg/m³
# - NO2 (1-hr), ppb (if µg/m³ is returned, we approximate conversion)
PM25_BREAKPOINTS = [
    Breakpoint(0.0, 12.0, 0, 50),
    Breakpoint(12.1, 35.4, 51, 100),
    Breakpoint(35.5, 55.4, 101, 150),
    Breakpoint(55.5, 150.4, 151, 200),
    Breakpoint(150.5, 250.4, 201, 300),
    Breakpoint(250.5, 350.4, 301, 400),
    Breakpoint(350.5, 500.4, 401, 500),
]

PM10_BREAKPOINTS = [
    Breakpoint(0.0, 54.0, 0, 50),
    Breakpoint(55.0, 154.0, 51, 100),
    Breakpoint(155.0, 254.0, 101, 150),
    Breakpoint(255.0, 354.0, 151, 200),
    Breakpoint(355.0, 424.0, 201, 300),
    Breakpoint(425.0, 504.0, 301, 400),
    Breakpoint(505.0, 604.0, 401, 500),
]

NO2_BREAKPOINTS_PPB = [
    Breakpoint(0, 53, 0, 50),
    Breakpoint(54, 100, 51, 100),
    Breakpoint(101, 360, 101, 150),
    Breakpoint(361, 649, 151, 200),
    Breakpoint(650, 1249, 201, 300),
    Breakpoint(1250, 1649, 301, 400),
    Breakpoint(1650, 2049, 401, 500),
]


def _aqi_linear(c: float, bp: Breakpoint) -> int:
    i = (bp.i_high - bp.i_low) / (bp.c_high - bp.c_low) * (c - bp.c_low) + bp.i_low
    return int(round(i))


def _find_bp(c: float, bps: list[Breakpoint]) -> Optional[Breakpoint]:
    for bp in bps:
        if bp.c_low <= c <= bp.c_high:
            return bp
    return None


def aqi_subindex_pm25(value_ugm3: float) -> Optional[int]:
    bp = _find_bp(value_ugm3, PM25_BREAKPOINTS)
    return None if not bp else _aqi_linear(value_ugm3, bp)


def aqi_subindex_pm10(value_ugm3: float) -> Optional[int]:
    bp = _find_bp(value_ugm3, PM10_BREAKPOINTS)
    return None if not bp else _aqi_linear(value_ugm3, bp)


def aqi_subindex_no2(value: float, unit: str = "ppb") -> Optional[int]:
    if unit.lower() in ["µg/m³", "ug/m3", "ug/m³", "micrograms per cubic meter"]:
        # ppb ≈ (µg/m³ * 24.45) / MW at 25°C, 1 atm; NO2 MW = 46.0055
        ppb = (value * 24.45) / 46.0055
    else:
        ppb = value

    bp = _find_bp(ppb, NO2_BREAKPOINTS_PPB)
    return None if not bp else _aqi_linear(ppb, bp)


def aqi_from_concentrations(
    concentrations: Dict[str, float], units: Dict[str, str] | None = None
) -> Dict[str, object]:
    units = units or {}
    sub = {
        "pm25": aqi_subindex_pm25(concentrations["pm25"]) if "pm25" in concentrations else None,
        "pm10": aqi_subindex_pm10(concentrations["pm10"]) if "pm10" in concentrations else None,
        "no2": aqi_subindex_no2(concentrations["no2"], unit=units.get("no2", "ppb"))
        if "no2" in concentrations
        else None,
    }

    overall = None
    dominant = None
    for k, v in sub.items():
        if v is None:
            continue
        if overall is None or v > overall:
            overall = v
            dominant = k

    return {"overall": overall, "subindices": sub, "dominant": dominant}


def aqi_category(aqi: int) -> Tuple[str, str]:
    if aqi <= 50:
        return ("Good", "#00e400")
    if aqi <= 100:
        return ("Moderate", "#ffff00")
    if aqi <= 150:
        return ("Unhealthy for Sensitive Groups", "#ff7e00")
    if aqi <= 200:
        return ("Unhealthy", "#ff0000")
    if aqi <= 300:
        return ("Very Unhealthy", "#8f3f97")
    return ("Hazardous", "#7e0023")