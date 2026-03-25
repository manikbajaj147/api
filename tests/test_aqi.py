from services.aqi import aqi_subindex_pm25, aqi_subindex_pm10, aqi_from_concentrations


def test_pm25_good_boundary():
    assert aqi_subindex_pm25(12.0) == 50


def test_pm10_good_boundary():
    assert aqi_subindex_pm10(54.0) == 50


def test_overall_is_max_subindex():
    out = aqi_from_concentrations({"pm25": 35.4, "pm10": 54.0})
    assert out["overall"] is not None
    assert out["subindices"]["pm25"] >= out["subindices"]["pm10"]
    assert out["dominant"] == "pm25"