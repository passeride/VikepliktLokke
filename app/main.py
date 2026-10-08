"""FastAPI server and OpenStreetMap intersection lookup for VikepliktLokke."""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
import threading
import uuid

import networkx as nx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .engine import add_analysis, demo_route, find_shortest_loop, haversine_m, speed_kmh

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="VikepliktLokke POC", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

CACHE: OrderedDict[str, dict] = OrderedDict()
CACHE_LOCK = threading.Lock()
MAX_SESSIONS = 8


class Settings(BaseModel):
    critical_clear_gap_s: float = Field(6, gt=0, le=30)
    min_following_clear_gap_s: float = Field(2, gt=0, le=20)
    vehicle_length_m: float = Field(4.5, gt=0, le=30)
    min_bumper_clearance_m: float = Field(2, ge=0, le=50)
    fallback_speed_kmh: float = Field(50, ge=5, le=130)


class PointRequest(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    radius_m: int = Field(1200, ge=300, le=3000)
    fallback_speed_kmh: float = Field(50, ge=5, le=130)


class SolveRequest(Settings):
    session_id: str
    approach_id: str


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/demo")
def demo(settings: Settings):
    route = add_analysis(demo_route(settings.fallback_speed_kmh), settings.model_dump())
    return {
        "mode": "demo", "junction": {
            "lat": route["trajectory"][0]["lat"], "lon": route["trajectory"][0]["lon"]},
        "routes": {"distance": route, "time": route},
        "warnings": ["Demo is a synthetic ellipse, not a drivable street loop.",
                     "Uniform speed and perfectly staggered vehicles are assumed."],
    }


def display_name(value) -> str:
    if isinstance(value, list):
        return " / ".join(str(item) for item in value[:2])
    return str(value or "Unnamed road")


@app.post("/api/intersection")
def intersection(request: PointRequest):
    try:
        import osmnx as ox
    except ImportError:
        raise HTTPException(503, "OSMnx not installed. Run pip install -r requirements.txt.")
    try:
        graph = ox.graph_from_point(
            (request.lat, request.lon), dist=request.radius_m,
            network_type="drive", simplify=True,
        )
        junction = ox.distance.nearest_nodes(graph, X=request.lon, Y=request.lat)
    except Exception as exc:
        raise HTTPException(502, "Could not download or parse OpenStreetMap road network: " + str(exc))

    approaches = []
    selection = {}
    seen = set()
    for u, v, key, attrs in graph.in_edges(junction, keys=True, data=True):
        if (u, v) in seen:
            continue
        seen.add((u, v))
        kmh, tagged = speed_kmh(attrs.get("maxspeed"), request.fallback_speed_kmh)
        approach_id = str(len(approaches))
        selection[approach_id] = (u, v, key)
        approaches.append({
            "id": approach_id, "name": display_name(attrs.get("name")),
            "road_type": display_name(attrs.get("highway")),
            "speed_kmh": kmh, "speed_from_osm": tagged,
            "from": {"lat": graph.nodes[u]["y"], "lon": graph.nodes[u]["x"]},
            "to": {"lat": graph.nodes[v]["y"], "lon": graph.nodes[v]["x"]},
        })
    if not approaches:
        raise HTTPException(422, "No incoming drivable approaches at the selected intersection.")

    session_id = str(uuid.uuid4())
    with CACHE_LOCK:
        CACHE[session_id] = {"graph": graph, "selection": selection}
        while len(CACHE) > MAX_SESSIONS:
            CACHE.popitem(last=False)
    coordinate = [graph.nodes[junction]["y"], graph.nodes[junction]["x"]]
    snap_distance = haversine_m([request.lat, request.lon], coordinate)
    return {
        "session_id": session_id,
        "junction": {"lat": coordinate[0], "lon": coordinate[1]},
        "snap_distance_m": round(snap_distance, 1),
        "approaches": approaches,
        "warnings": [
            "OSM routing does not enforce turn restrictions, traffic signals or priority rules.",
            "OpenStreetMap speed limits can be missing or incomplete.",
        ] + (["Nearest graph junction is more than 75 m from the click. Select a closer junction."] if snap_distance > 75 else []),
    }


@app.post("/api/solve")
def solve(request: SolveRequest):
    with CACHE_LOCK:
        entry = CACHE.get(request.session_id)
    if entry is None:
        raise HTTPException(404, "Map session expired; click the intersection again.")
    selected = entry["selection"].get(request.approach_id)
    if selected is None:
        raise HTTPException(422, "Invalid approach ID.")
    graph = entry["graph"]
    routes = {}
    for key, weight in (("distance", "distance"), ("time", "time")):
        try:
            result = find_shortest_loop(
                graph, *selected, fallback_speed_kmh=request.fallback_speed_kmh,
                optimize=weight,
            )
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue
        routes[key] = add_analysis(result, request.model_dump())
    if not routes:
        raise HTTPException(422, "No closed directed loop without immediate U-turn found. Try another approach or larger search radius.")
    return {
        "mode": "osm",
        "routes": routes,
        "warnings": [
            "A directed OSM loop is not proof of a continuously drivable, legal loop.",
            "Turn restrictions, roundabout lane choice, yielding and stoplights are not validated.",
            "Missing maxspeed tags use your chosen fallback, not an authoritative speed limit.",
        ],
    }
