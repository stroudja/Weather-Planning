from route_weather import hazards


def _weather(**overrides):
    base = {
        "temperature_2m": 20.0,
        "precipitation": 0.0,
        "snowfall": 0.0,
        "weather_code": 0,
        "wind_gusts_10m": 10.0,
        "visibility": 20000.0,
    }
    base.update(overrides)
    return base


def test_clear_day_scores_zero():
    score, flags = hazards.assess(_weather())
    assert score == 0
    assert flags == []


def test_freezing_rain_is_severe():
    score, flags = hazards.assess(_weather(weather_code=67, temperature_2m=-1, precipitation=2))
    assert score >= 8
    assert any("freezing" in f.lower() for f in flags)


def test_ice_risk_flagged_near_freezing():
    score, flags = hazards.assess(_weather(weather_code=61, temperature_2m=0.5, precipitation=1))
    assert any("ice risk" in f.lower() for f in flags)
    assert score >= 4


def test_no_ice_risk_when_snowing():
    # Snow already scores; the rain-on-frozen-road heuristic shouldn't stack
    _, flags = hazards.assess(_weather(weather_code=73, temperature_2m=-2,
                                       precipitation=1, snowfall=1))
    assert not any("ice risk" in f.lower() for f in flags)


def test_high_gusts_add_hazard():
    score, flags = hazards.assess(_weather(wind_gusts_10m=85))
    assert score >= 4
    assert any("wind" in f.lower() for f in flags)


def test_dense_fog_visibility():
    score, flags = hazards.assess(_weather(weather_code=45, visibility=300))
    assert score >= 5
    assert any("visibility" in f.lower() for f in flags)


def test_score_capped_at_ten():
    score, _ = hazards.assess(_weather(
        weather_code=99, temperature_2m=-25, precipitation=15,
        wind_gusts_10m=100, visibility=100,
    ))
    assert score == 10


def test_levels():
    assert hazards.level_for(0) == ("Good", "green")
    assert hazards.level_for(4) == ("Caution", "yellow")
    assert hazards.level_for(6) == ("Hazardous", "orange")
    assert hazards.level_for(10) == ("Severe", "red")
