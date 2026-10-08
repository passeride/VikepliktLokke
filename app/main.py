"""FastAPI server for map selection, directed circuits and time-gap analysis."""
from __future__ import annotations

from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
import threading
import time
import uuid
from typing import Literal

import httpx
import networkx as nx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .engine import add_analysis, allowed_turn, demo_route, find_shortest_loop, speed_kmh
from .roads import load_roads, select_point
from .conflicts import paths_conflict
import math

STATIC = Path(__file__).parent / 'static'
app = FastAPI(title='VikepliktLokke', version='0.2.0')
app.mount('/static', StaticFiles(directory=STATIC), name='static')
CACHE: OrderedDict[str, dict] = OrderedDict()
CACHE_LOCK = threading.Lock()
SEARCH_LOCK = threading.Lock()
LAST_SEARCH = 0.0
MAX_SESSIONS = 16


class Settings(BaseModel):
    critical_clear_gap_s: float = Field(6, gt=0, le=30)
    min_following_clear_gap_s: float = Field(2, gt=0, le=20)
    vehicle_length_m: float = Field(4.5, gt=0, le=30)
    min_bumper_clearance_m: float = Field(2, ge=0, le=50)
    fallback_speed_kmh: float = Field(50, ge=5, le=130)


class PointRequest(BaseModel):
    lat: float = Field(ge=-85, le=85)
    lon: float = Field(ge=-180, le=180)
    radius_m: int = Field(1200, ge=300, le=5000)
    fallback_speed_kmh: float = Field(50, ge=5, le=130)
    selection_mode: Literal['junction', 'driveway'] = 'junction'


class SolveRequest(Settings):
    session_id: str
    approach_id: str
    destination_id: str
    waiting_approach_id: str | None = None
    virtual_origin_bearing: float = Field(0, ge=0, lt=360)


@app.get('/')
def index():
    return FileResponse(STATIC / 'index.html')


@app.get('/api/health')
def health():
    return {'status': 'ok'}


@lru_cache(maxsize=64)
def search_place(query: str):
    global LAST_SEARCH
    # Nominatim public policy: submitted searches only, cached, <=1 request/s.
    with SEARCH_LOCK:
        time.sleep(max(0, 1.1 - (time.monotonic() - LAST_SEARCH)))
        LAST_SEARCH = time.monotonic()
        response = httpx.get('https://nominatim.openstreetmap.org/search',
                             params={'q': query, 'format': 'jsonv2', 'limit': 5}, timeout=15,
                             headers={'User-Agent': 'VikepliktLokke/0.2 (github.com/passeride/VikepliktLokke)',
                                      'Accept-Language': 'nb,en'})
        response.raise_for_status()
        return [{'name': p['display_name'], 'lat': float(p['lat']), 'lon': float(p['lon'])}
                for p in response.json()]


@app.get('/api/search')
def search(q: str = Query(min_length=2, max_length=200)):
    try:
        return {'places': search_place(q.strip())}
    except (httpx.HTTPError, ValueError, KeyError):
        raise HTTPException(502, 'Stedssøk er utilgjengelig. Naviger i kartet eller søk med breddegrad, lengdegrad.')


@app.post('/api/demo')
def demo(settings: Settings):
    route = add_analysis(demo_route(settings.fallback_speed_kmh), settings.model_dump())
    return {'mode': 'demo', 'junction': {'lat': route['trajectory'][0]['lat'],
                                       'lon': route['trajectory'][0]['lon']},
            'routes': {'distance': route, 'time': route},
            'warnings': ['Syntetisk ellipse, ikke en kjørbar gate.']}


def display_name(value) -> str:
    if isinstance(value, list):
        return ' / '.join(str(item) for item in value[:2])
    return str(value or 'Uten veinavn')


def approach_geometry(graph, u, v, outgoing=False) -> list:
    """Follow an incoming road backwards to make its direction visible on map."""
    path = [u, v] if outgoing else [v, u]
    distance = graph[u][v][next(iter(graph[u][v]))]['length']
    while distance < 200 and len(path) < 100:
        neighbors = set(graph.predecessors(path[-1])) | set(graph.successors(path[-1]))
        if len(neighbors) != 2:
            break
        candidates = [n for n in (graph.successors(path[-1]) if outgoing else graph.predecessors(path[-1])) if n not in path]
        if len(candidates) != 1:
            break
        n = candidates[0]
        a, b = (path[-1], n) if outgoing else (n, path[-1])
        distance += graph[a][b][next(iter(graph[a][b]))]['length']
        path.append(n)
    return [[graph.nodes[n]['y'], graph.nodes[n]['x']] for n in (path if outgoing else reversed(path))]


@app.post('/api/intersection')
def intersection(request: PointRequest):
    try:
        graph = load_roads(request.lat, request.lon, request.radius_m)
        junction, snap_distance = select_point(graph, request.lat, request.lon, request.selection_mode)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    approaches, selection, departures, departure_edges = [], {}, [], {}
    for u, v, key, attrs in graph.out_edges(junction, keys=True, data=True):
        departure_id = str(len(departures))
        departure_edges[departure_id] = (u, v, key)
        departures.append({'id': departure_id, 'name': display_name(attrs.get('name')),
                           'coordinates': approach_geometry(graph, u, v, outgoing=True)})
    for u, v, key, attrs in graph.in_edges(junction, keys=True, data=True):
        kmh, tagged = speed_kmh(attrs.get('maxspeed'), request.fallback_speed_kmh)
        approach_id = str(len(approaches))
        selection[approach_id] = (u, v, key)
        coords = approach_geometry(graph, u, v)
        approaches.append({'id': approach_id, 'name': display_name(attrs.get('name')),
                           'road_type': display_name(attrs.get('highway')),
                           'speed_kmh': kmh, 'speed_from_osm': tagged,
                           'coordinates': coords,
                           'allowed_departures': [id_ for id_, edge in departure_edges.items()
                                                  if allowed_turn(graph, (u, v, key), edge)],
                           'from': {'lat': coords[0][0], 'lon': coords[0][1]},
                           'to': {'lat': graph.nodes[v]['y'], 'lon': graph.nodes[v]['x']}})
    if not approaches:
        raise HTTPException(422, 'Ingen innkommende bilveier ved punktet.')
    session_id = str(uuid.uuid4())
    metadata = {'restriction_count': graph.graph['restriction_count'],
                'excluded_restrictions': graph.graph['excluded_restrictions'],
                'excluded_ways': graph.graph['excluded_ways'], 'bounds': graph.graph['bounds']}
    with CACHE_LOCK:
        CACHE[session_id] = {'graph': graph, 'selection': selection, 'metadata': metadata,
                             'approaches': approaches, 'departures': departures,
                             'departure_edges': departure_edges, 'junction': junction,
                             'selection_mode': request.selection_mode}
        while len(CACHE) > MAX_SESSIONS:
            CACHE.popitem(last=False)
    return {'session_id': session_id,
            'junction': {'lat': graph.nodes[junction]['y'], 'lon': graph.nodes[junction]['x']},
            'snap_distance_m': round(snap_distance, 1), 'approaches': approaches,
            'departures': departures, 'network': metadata, 'selection_mode': request.selection_mode}


@app.post('/api/solve')
def solve(request: SolveRequest):
    with CACHE_LOCK:
        entry = CACHE.get(request.session_id)
        if entry:
            CACHE.move_to_end(request.session_id)
    if entry is None:
        raise HTTPException(404, 'Kartøkten er utløpt. Velg punktet igjen.')
    selected = entry['selection'].get(request.approach_id)
    if selected is None:
        raise HTTPException(422, 'Ugyldig trafikkretning.')
    graph = entry['graph']
    destination = next((d for d in entry['departures'] if d['id'] == request.destination_id), None)
    if destination is None:
        raise HTTPException(422, 'Ugyldig ønsket utkjøring.')
    center = [graph.nodes[entry['junction']]['y'], graph.nodes[entry['junction']]['x']]
    if entry['selection_mode'] == 'driveway':
        bearing = math.radians(request.virtual_origin_bearing)
        waiting_coords = [[center[0] + 75 * math.cos(bearing) / 111111,
                           center[1] + 75 * math.sin(bearing) / (111111 * math.cos(math.radians(center[0])))], center]
    else:
        waiting = next((a for a in entry['approaches'] if a['id'] == request.waiting_approach_id), None)
        if waiting is None or waiting['id'] == request.approach_id:
            raise HTTPException(422, 'Velg innkjøringen din og en annen trafikkretning.')
        if request.destination_id not in waiting['allowed_departures']:
            raise HTTPException(422, 'Den ønskede svingen er ikke tilgjengelig for innkjøringen din.')
        waiting_coords = waiting['coordinates']
    traffic = next(a for a in entry['approaches'] if a['id'] == request.approach_id)
    def heading(coords):
        a, b = coords[-2:]
        return math.degrees(math.atan2((b[1]-a[1])*math.cos(math.radians(center[0])), b[0]-a[0]))
    angle = (heading(traffic['coordinates']) + 180 - heading(waiting_coords) + 180) % 360 - 180
    if not 20 < angle < 160:
        raise HTTPException(422, 'Trafikken må komme fra din høyre side i denne modellen.')
    conflicts = {}
    for departure in entry['departures']:
        edge = entry['departure_edges'][departure['id']]
        if not allowed_turn(graph, selected, edge):
            continue
        conflict = paths_conflict(waiting_coords, destination['coordinates'], traffic['coordinates'],
                                  departure['coordinates'], center, same_exit=departure['id'] == request.destination_id)
        if conflict['conflicts']:
            conflicts[edge] = {**conflict, 'traffic_departure_id': departure['id'], 'traffic_departure_name': departure['name']}
    if not conflicts:
        return {'mode': 'no_conflict', 'routes': {}, 'network': entry['metadata'],
                'message': 'Ingen konflikt: trafikkens tilgjengelige kjørebaner krysser ikke din og fletter ikke inn i samme utkjøring. Denne trafikkstrømmen blokkerer ikke manøveren din i modellen.'}
    routes = {}
    for optimize in ('distance', 'time'):
        try:
            result = find_shortest_loop(graph, *selected,
                                       fallback_speed_kmh=request.fallback_speed_kmh, optimize=optimize,
                                       allowed_departures=set(conflicts))
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue
        result['conflict'] = conflicts[tuple(result['departure_edge'])]
        result['approach_speed_kmh'] = result['crossing_speed_kmh']
        c = result['conflict']
        duration_in = c['traffic_in_distance_m'] / (result['approach_speed_kmh'] / 3.6)
        duration_out = c['traffic_out_distance_m'] / (result['departure_speed_kmh'] / 3.6)
        c['time_s'] = c['traffic_conflict_fraction'] * (duration_in + duration_out) - duration_in
        if c['time_s'] >= 0:
            result['crossing_speed_kmh'] = result['departure_speed_kmh']
        routes[optimize] = add_analysis(result, request.model_dump())
    if not routes:
        raise HTTPException(422, 'Ingen lukket løkke for denne retningen innen søkeområdet og de støttede svingereglene. Velg en annen retning eller øk søkeradius og hent punktet igjen.')
    return {'mode': 'osm', 'routes': routes, 'network': entry['metadata'],
            'warnings': ['Korteste løkke gjelder bare det nedlastede søkeområdet.',
                         'Forkjørsrett og kontinuerlig flyt gjennom andre kryss er ikke automatisk bekreftet.']}
