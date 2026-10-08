import math

import networkx as nx
import pytest

from app.engine import (
    add_analysis, demo_route, find_shortest_loop, speed_kmh, traffic_requirements,
)


def test_speed_parser():
    assert speed_kmh("50", 30) == (50, True)
    assert speed_kmh("30 mph", 50)[0] == pytest.approx(48.28032)
    assert speed_kmh("NO:urban", 45) == (45, False)
    assert speed_kmh(["80", "50"], 30) == (50, True)


def test_gap_is_clear_time_not_front_headway():
    model = traffic_requirements(72, 50, 50, 6, 2, 4.5, 2)
    assert model["minimum_cars"] == 12
    assert model["maximum_safe_cars"] >= 12
    assert model["clear_gap_s"] < 6
    eleven = 72 / 11 - 4.5 / (50 / 3.6)
    assert eleven >= 6


def test_strict_threshold_at_equality():
    # 36 km/h = 10 m/s; vehicle clearance takes 0.5 s;
    # T=66, required front-to-front headway must be < 6s.
    model = traffic_requirements(66, 36, 36, 5.5, 1.0, 5, 1)
    assert model["minimum_cars"] == 12


def test_impossible_following_gap():
    model = traffic_requirements(60, 50, 50, 2.0, 3.0, 4.5, 2)
    assert model["feasible"] is False


def graph_example():
    graph = nx.MultiDiGraph()
    for node, x, y in [(1, 7.0, 62.0), (2, 7.01, 62.0), (3, 7.01, 62.01)]:
        graph.add_node(node, x=x, y=y)
    graph.add_edge(1, 2, key=0, length=100, maxspeed="50")
    graph.add_edge(2, 1, key=0, length=10, maxspeed="50")  # forbidden immediate U-turn
    graph.add_edge(2, 3, key=0, length=150, maxspeed="30")
    graph.add_edge(3, 1, key=0, length=200, maxspeed="50")
    return graph


def test_shortest_loop_avoids_immediate_u_turn():
    result = find_shortest_loop(graph_example(), 1, 2, 0)
    assert result["length_m"] == 450
    assert result["segment_count"] == 3
    assert result["trajectory"][0]["t"] == 0
    assert result["trajectory"][-1]["t"] == pytest.approx(result["cycle_seconds"], abs=0.001)
    assert result["trajectory"][0]["lat"] == result["trajectory"][-1]["lat"]
    assert result["trajectory"][0]["lon"] == result["trajectory"][-1]["lon"]


def test_demo_trajectory_and_analysis():
    route = demo_route(50)
    assert 900 < route["length_m"] < 1200
    assert route["synthetic"] is True
    assert route["trajectory"][-1]["t"] == pytest.approx(route["cycle_seconds"], abs=0.001)
    analysis = add_analysis(route, dict(
        critical_clear_gap_s=6, min_following_clear_gap_s=2,
        vehicle_length_m=4.5, min_bumper_clearance_m=2,
    ))
    assert analysis["analysis"]["minimum_cars"] > 0


def test_invalid_input():
    with pytest.raises(ValueError):
        traffic_requirements(0, 50, 50)
    with pytest.raises(ValueError):
        traffic_requirements(60, 50, 50, vehicle_length_m=-1)


def test_speed_parser_does_not_treat_conditional_text_as_a_numeric_limit():
    assert speed_kmh('50 @ (Mo-Fr)', 30) == (30, False)


def test_spacing_and_minimum_proof():
    model = traffic_requirements(72, 50, 30)
    v = 50 / 3.6
    assert model['bumper_spacing_at_crossing_m'] == pytest.approx(72 / model['minimum_cars'] * v - 4.5, abs=.01)
    assert model['clear_gap_s'] < model['critical_clear_gap_s']
    assert model['clear_gap_with_one_fewer_car_s'] >= model['critical_clear_gap_s']
