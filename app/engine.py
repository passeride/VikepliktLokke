"""Directed loop routing and an explicitly idealized time-gap traffic model."""
from __future__ import annotations

import math
import re
from typing import Any

import networkx as nx

EARTH_LAT_M = 111_111.0


def speed_kmh(value: Any, fallback: float) -> tuple[float, bool]:
    """Return (speed, from_tag). Unknown/non-numeric OSM limits use the fallback."""
    if isinstance(value, (list, tuple)):
        parsed = [speed_kmh(item, fallback) for item in value]
        known = [speed for speed, from_tag in parsed if from_tag]
        return (min(known), True) if known else (fallback, False)
    if isinstance(value, (int, float)) and math.isfinite(value) and value > 0:
        return float(value), True
    if isinstance(value, str):
        match = re.search(r"(?<!\d)(\d+(?:\.\d+)?)\s*(mph|km/h|kph)?", value.lower())
        if match:
            speed = float(match.group(1)) * (1.609344 if match.group(2) == "mph" else 1)
            if 1 <= speed <= 160:
                return speed, True
    return fallback, False


def traffic_requirements(
    cycle_seconds: float,
    crossing_speed_kmh: float,
    minimum_speed_kmh: float,
    critical_clear_gap_s: float = 6.0,
    min_following_clear_gap_s: float = 2.0,
    vehicle_length_m: float = 4.5,
    min_bumper_clearance_m: float = 2.0,
) -> dict[str, Any]:
    """Uniform front-to-front arrival headways for a circulating platoon.

    An accepted time gap is measured from the preceding car's rear clearing
    the conflict point until the following car's front reaches it.
    """
    inputs = (cycle_seconds, crossing_speed_kmh, minimum_speed_kmh,
              critical_clear_gap_s, min_following_clear_gap_s,
              vehicle_length_m, min_bumper_clearance_m)
    if not all(math.isfinite(v) for v in inputs):
        raise ValueError("All model parameters must be finite")
    if min(inputs[:5]) <= 0 or vehicle_length_m <= 0 or min_bumper_clearance_m < 0:
        raise ValueError("Times/speeds/vehicle length must be positive; clearance nonnegative")

    cross_mps = crossing_speed_kmh / 3.6
    slow_mps = minimum_speed_kmh / 3.6
    vehicle_passage_s = vehicle_length_m / cross_mps
    # Strictly less than threshold: equality is an acceptable gap.
    minimum_cars = math.floor(cycle_seconds / (critical_clear_gap_s + vehicle_passage_s)) + 1
    safe_headway_s = max(
        min_following_clear_gap_s + vehicle_length_m / slow_mps,
        (vehicle_length_m + min_bumper_clearance_m) / slow_mps,
    )
    maximum_cars = math.floor(cycle_seconds / safe_headway_s)
    headway_s = cycle_seconds / minimum_cars
    clear_gap_s = headway_s - vehicle_passage_s
    feasible = minimum_cars <= maximum_cars
    return {
        "minimum_cars": minimum_cars,
        "maximum_safe_cars": maximum_cars,
        "feasible": feasible,
        "front_to_front_headway_s": round(headway_s, 3),
        "clear_gap_s": round(clear_gap_s, 3),
        "critical_clear_gap_s": critical_clear_gap_s,
        "min_following_clear_gap_s": min_following_clear_gap_s,
        "vehicle_length_m": vehicle_length_m,
        "min_bumper_clearance_m": min_bumper_clearance_m,
        "crossing_speed_kmh": round(crossing_speed_kmh, 2),
        "cycle_seconds": round(cycle_seconds, 2),
        "assumptions": [
            "All cars complete identical laps and maintain uniformly staggered arrival times.",
            "Vehicle lengths are equal; weather, reaction-time variation and merging are ignored.",
            "The traffic stream is not guaranteed to be physically or legally sustainable.",
        ],
    }


def edge_details(attrs: dict[str, Any], fallback_speed_kmh: float) -> dict[str, Any]:
    kmh, tagged = speed_kmh(attrs.get("maxspeed"), fallback_speed_kmh)
    length = float(attrs.get("length", 0))
    if length <= 0:
        raise ValueError("Edges require a positive length")
    return {
        "length_m": length,
        "speed_kmh": kmh,
        "time_s": length / (kmh / 3.6),
        "speed_from_osm": tagged,
    }


def oriented_coordinates(graph: nx.MultiDiGraph, u: Any, v: Any, attrs: dict) -> list[list[float]]:
    geometry = attrs.get("geometry")
    if geometry is not None and hasattr(geometry, "coords"):
        coords = [[float(y), float(x)] for x, y in geometry.coords]
    else:
        coords = [[float(graph.nodes[u]["y"]), float(graph.nodes[u]["x"])],
                  [float(graph.nodes[v]["y"]), float(graph.nodes[v]["x"])]]
    start = graph.nodes[u]
    if ((coords[0][0] - start["y"]) ** 2 + (coords[0][1] - start["x"]) ** 2 >
            (coords[-1][0] - start["y"]) ** 2 + (coords[-1][1] - start["x"]) ** 2):
        coords.reverse()
    return coords


def _route_as_trajectory(graph: nx.MultiDiGraph, legs: list[tuple[Any, Any, dict, dict]]) -> list[dict]:
    points: list[dict] = []
    clock = 0.0
    for u, v, attrs, values in legs:
        coords = oriented_coordinates(graph, u, v, attrs)
        lat0 = sum(pt[0] for pt in coords) / len(coords)
        weights = [
            max(1e-9, math.hypot((b[0] - a[0]) * EARTH_LAT_M,
                                 (b[1] - a[1]) * EARTH_LAT_M * math.cos(math.radians(lat0))))
            for a, b in zip(coords, coords[1:])
        ]
        total_weight = sum(weights)
        if not points:
            points.append({"lat": coords[0][0], "lon": coords[0][1], "t": 0.0})
        for coordinate, weight in zip(coords[1:], weights):
            clock += values["time_s"] * weight / total_weight
            points.append({"lat": coordinate[0], "lon": coordinate[1], "t": round(clock, 4)})
    return points


def find_shortest_loop(
    graph: nx.MultiDiGraph,
    incoming_u: Any,
    junction_v: Any,
    incoming_key: Any,
    fallback_speed_kmh: float = 50.0,
    optimize: str = "distance",
) -> dict[str, Any]:
    """Return directed closed walk arriving on incoming_u -> junction_v.

    Forbid an immediate reversal on the incoming road; note that real turn
    restrictions/signals and right-of-way at other intersections are NOT modeled.
    """
    if optimize not in ("distance", "time"):
        raise ValueError("optimize must be 'distance' or 'time'")
    selected_attrs = graph[incoming_u][junction_v][incoming_key]
    selected = edge_details(selected_attrs, fallback_speed_kmh)
    simplified = nx.DiGraph()
    for u, v, key, attrs in graph.edges(keys=True, data=True):
        try:
            details = edge_details(attrs, fallback_speed_kmh)
        except (TypeError, ValueError):
            continue
        weight = details["length_m"] if optimize == "distance" else details["time_s"]
        if not simplified.has_edge(u, v) or weight < simplified[u][v]["weight"]:
            simplified.add_edge(u, v, key=key, weight=weight, details=details, attrs=attrs)
    # A single reverse movement at the junction is not a valid circuit.
    if simplified.has_edge(junction_v, incoming_u):
        simplified.remove_edge(junction_v, incoming_u)
    path = nx.shortest_path(simplified, junction_v, incoming_u, weight="weight")
    legs = []
    for u, v in zip(path, path[1:]):
        record = simplified[u][v]
        legs.append((u, v, record["attrs"], record["details"]))
    legs.append((incoming_u, junction_v, selected_attrs, selected))

    total_length = sum(item[3]["length_m"] for item in legs)
    total_time = sum(item[3]["time_s"] for item in legs)
    return {
        "optimization": optimize,
        "length_m": round(total_length, 2),
        "cycle_seconds": round(total_time, 3),
        "crossing_speed_kmh": selected["speed_kmh"],
        "minimum_speed_kmh": min(item[3]["speed_kmh"] for item in legs),
        "segments_with_tagged_speed": sum(item[3]["speed_from_osm"] for item in legs),
        "segment_count": len(legs),
        "trajectory": _route_as_trajectory(graph, legs),
    }


def add_analysis(route: dict, settings: dict) -> dict:
    return {
        **route,
        "analysis": traffic_requirements(
            cycle_seconds=route["cycle_seconds"],
            crossing_speed_kmh=route["crossing_speed_kmh"],
            minimum_speed_kmh=route["minimum_speed_kmh"],
            critical_clear_gap_s=settings["critical_clear_gap_s"],
            min_following_clear_gap_s=settings["min_following_clear_gap_s"],
            vehicle_length_m=settings["vehicle_length_m"],
            min_bumper_clearance_m=settings["min_bumper_clearance_m"],
        ),
    }


def haversine_m(a: list[float], b: list[float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    v = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(min(1, math.sqrt(v)))


def demo_route(speed: float) -> dict:
    """An intentionally synthetic ellipse, NOT a route on real streets."""
    latitude, longitude = 62.7379, 7.1608
    coords = []
    for i in range(97):
        angle = -math.pi / 2 + i * 2 * math.pi / 96
        coords.append([latitude + 130 * math.sin(angle) / EARTH_LAT_M,
                       longitude + 190 * math.cos(angle) / (EARTH_LAT_M * math.cos(math.radians(latitude)))])
    lengths = [haversine_m(a, b) for a, b in zip(coords, coords[1:])]
    length = sum(lengths)
    elapsed = 0.0
    points = [{"lat": coords[0][0], "lon": coords[0][1], "t": 0.0}]
    for coordinate, distance in zip(coords[1:], lengths):
        elapsed += distance / (speed / 3.6)
        points.append({"lat": coordinate[0], "lon": coordinate[1], "t": round(elapsed, 4)})
    return {
        "optimization": "distance",
        "length_m": round(length, 2),
        "cycle_seconds": round(elapsed, 3),
        "crossing_speed_kmh": speed,
        "minimum_speed_kmh": speed,
        "segments_with_tagged_speed": 0,
        "segment_count": 1,
        "trajectory": points,
        "synthetic": True,
    }
