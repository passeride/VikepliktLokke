"""Directed loop routing and an explicitly idealized time-gap traffic model."""
from __future__ import annotations

import math
import re
import heapq
from itertools import count
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
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(mph|km/h|kph)?\s*", value.lower())
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
        "front_spacing_at_crossing_m": round(headway_s * cross_mps, 2),
        "bumper_spacing_at_crossing_m": round(headway_s * cross_mps - vehicle_length_m, 2),
        "clear_gap_with_one_fewer_car_s": (round(cycle_seconds / (minimum_cars - 1) - vehicle_passage_s, 3)
                                           if minimum_cars > 1 else None),
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
    allowed_departures: set[tuple] | None = None,
) -> dict[str, Any]:
    """Dijkstra over directed edge states, including the turn across the lap seam.

    No reversal on the same road anywhere. Node-via no/only restrictions are
    enforced by allowed_turn; unsupported restriction ways are excluded at load.
    The optimum is within the downloaded graph, not a global road-network claim.
    """
    if optimize not in ("distance", "time"):
        raise ValueError("optimize must be 'distance' or 'time'")
    selected = edge_details(graph[incoming_u][junction_v][incoming_key], fallback_speed_kmh)
    records = {}
    for u, v, key, attrs in graph.edges(keys=True, data=True):
        try:
            details = edge_details(attrs, fallback_speed_kmh)
        except (TypeError, ValueError):
            continue
        weight = details["length_m"] if optimize == "distance" else details["time_s"]
        records[u, v, key] = (attrs, details, weight)
    start = (incoming_u, junction_v, incoming_key)
    serial = count()
    queue = [(0.0, next(serial), start)]
    costs = {start: 0.0}
    previous = {}
    last = None
    while queue:
        cost, _, edge = heapq.heappop(queue)
        if cost != costs[edge]:
            continue
        # Complete only when the selected incoming edge can be traversed again.
        # Its next outgoing turn is already checked by the initial expansion.
        if edge[1] == incoming_u and allowed_turn(graph, edge, start):
            last = edge
            break
        for _, target, key in graph.out_edges(edge[1], keys=True):
            nxt = (edge[1], target, key)
            if edge == start and allowed_departures is not None and nxt not in allowed_departures:
                continue
            if nxt == start or nxt not in records or not allowed_turn(graph, edge, nxt):
                continue
            candidate = cost + records[nxt][2]
            if candidate < costs.get(nxt, math.inf):
                costs[nxt] = candidate
                previous[nxt] = edge
                heapq.heappush(queue, (candidate, next(serial), nxt))
    if last is None:
        raise nx.NetworkXNoPath("No circuit respecting the supported turn rules")
    path_edges = []
    while last != start:
        path_edges.append(last)
        last = previous[last]
    path_edges.reverse()
    path_edges.append(start)
    legs = [(u, v, records[u, v, key][0], records[u, v, key][1]) for u, v, key in path_edges]

    total_length = sum(item[3]["length_m"] for item in legs)
    total_time = sum(item[3]["time_s"] for item in legs)
    return {
        "optimization": optimize,
        "length_m": round(total_length, 2),
        "cycle_seconds": total_time,
        "crossing_speed_kmh": selected["speed_kmh"],
        "minimum_speed_kmh": min(item[3]["speed_kmh"] for item in legs),
        "segments_with_tagged_speed": sum(item[3]["speed_from_osm"] for item in legs),
        "segment_count": len(legs),
        "trajectory": _route_as_trajectory(graph, legs),
        "departure_edge": list(path_edges[0]),
        "departure_speed_kmh": legs[0][3]["speed_kmh"],
        "segments": [{"name": attrs.get("name", "Uten veinavn"), **details}
                     for _, _, attrs, details in legs],
        "control_points": [{"lat": graph.nodes[n]["y"], "lon": graph.nodes[n]["x"],
                            "type": graph.nodes[n]["highway"]}
                           for n in dict.fromkeys(v for _, v, _, _ in legs)
                           if graph.nodes[n].get("highway") in ("traffic_signals", "stop", "give_way")],
        "touches_boundary": any(graph.nodes[n].get("boundary", False)
                                for edge in path_edges for n in edge[:2]),
    }


def way_ids(attrs: dict) -> set:
    value = attrs.get("osmid")
    return set(value if isinstance(value, (list, tuple, set)) else [value])


def allowed_turn(graph: nx.MultiDiGraph, incoming: tuple, outgoing: tuple) -> bool:
    u, v, key = incoming
    _, w, next_key = outgoing
    before, after = graph[u][v][key], graph[v][w][next_key]
    # Different parallel roads between two junctions can form a real circuit.
    if w == u and way_ids(before) & way_ids(after):
        return False
    for rule in graph.graph.get("turn_restrictions", {}).get(v, []):
        if rule["from"] not in way_ids(before):
            continue
        matches = bool(set(rule["to"]) & way_ids(after))
        if rule["kind"].startswith("only_") and not matches:
            return False
        if rule["kind"].startswith("no_") and matches:
            # no_u_turn prohibits reversal, not continuing along the same way.
            if rule["kind"] != "no_u_turn" or w == u:
                return False
    return True


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
        "cycle_seconds": elapsed,
        "crossing_speed_kmh": speed,
        "minimum_speed_kmh": speed,
        "segments_with_tagged_speed": 0,
        "segment_count": 1,
        "trajectory": points,
        "synthetic": True,
    }
