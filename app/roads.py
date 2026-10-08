"""OSM road graph and turn restrictions from a single consistent Overpass snapshot."""
from __future__ import annotations

import math
import os
import time
from collections import OrderedDict
from threading import Lock

import httpx
import networkx as nx

from .engine import haversine_m

# Public roads on which ordinary motorcars can circulate. Destination/private
# access and conditional access are deliberately omitted for a through circuit.
ROAD_TYPES = {'motorway', 'motorway_link', 'trunk', 'trunk_link', 'primary',
              'primary_link', 'secondary', 'secondary_link', 'tertiary',
              'tertiary_link', 'unclassified', 'residential', 'living_street', 'service'}
PERMITTED = {'yes', 'designated', 'permissive'}
SUPPORTED_TURNS = {'no_left_turn', 'no_right_turn', 'no_straight_on', 'no_u_turn',
                   'no_entry', 'no_exit', 'only_left_turn', 'only_right_turn', 'only_straight_on'}
REGIONS: OrderedDict = OrderedDict()
REGION_LOCK = Lock()


def driveable(tags: dict) -> bool:
    if tags.get('highway') not in ROAD_TYPES or tags.get('area') == 'yes':
        return False
    if any(key in tags for key in ('access:conditional', 'vehicle:conditional',
                                    'motor_vehicle:conditional', 'motorcar:conditional',
                                    'oneway:conditional', 'oneway:motorcar:conditional',
                                    'oneway:motor_vehicle:conditional', 'oneway:vehicle:conditional')):
        return False
    if tags.get('oneway') in ('reversible', 'alternating'):
        return False
    # Mode-specific permission overrides a more general permission.
    access = next((tags[k] for k in ('motorcar', 'motor_vehicle', 'vehicle', 'access') if k in tags), 'yes')
    return access in PERMITTED and tags.get('service') not in ('parking_aisle', 'driveway')


def graph_from_osm(data: dict, bounds: list[float]) -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph(bounds=bounds, turn_restrictions={}, restriction_count=0,
                            excluded_restrictions=0, excluded_ways=0)
    nodes = {e['id']: e for e in data['elements'] if e['type'] == 'node'}
    ways = {e['id']: e for e in data['elements'] if e['type'] == 'way' and driveable(e.get('tags', {}))}
    blocked = set()
    restrictions = {}
    for relation in (e for e in data['elements'] if e['type'] == 'relation'):
        tags = relation.get('tags', {})
        if tags.get('type') != 'restriction':
            continue
        if set(tags.get('except', '').replace(' ', '').split(';')) & {'motorcar', 'motor_vehicle', 'vehicle'}:
            continue
        kind = next((tags[k] for k in ('restriction:motorcar', 'restriction:motor_vehicle',
                                       'restriction:vehicle', 'restriction') if k in tags), None)
        # Restrictions solely for bicycles, buses, trucks etc. do not apply here.
        conditional = any(k in tags for k in ('restriction:conditional', 'restriction:motorcar:conditional',
                                               'restriction:motor_vehicle:conditional', 'restriction:vehicle:conditional'))
        if kind is None and not conditional:
            continue
        members = relation.get('members', [])
        source = [m['ref'] for m in members if m['role'] == 'from' and m['type'] == 'way']
        target = [m['ref'] for m in members if m['role'] == 'to' and m['type'] == 'way']
        via = [m for m in members if m['role'] == 'via']
        if not source or not target or len(via) != 1 or via[0]['type'] != 'node' or conditional or kind not in SUPPORTED_TURNS or (str(kind).startswith('only_') and set(source) & set(target)):
            # Fail conservatively: do not route over a from-way whose restriction
            # cannot be interpreted. Never silently advertise it as validated.
            blocked.update(source)
            graph.graph['excluded_restrictions'] += 1
            continue
        if kind == 'no_u_turn':
            # A node-based U-turn relation can use two separate OSM ways.
            kind = 'no_turn' if not set(source) & set(target) else kind
        for from_way in source:
            restrictions.setdefault(via[0]['ref'], []).append({'from': from_way, 'to': target, 'kind': kind})
        graph.graph['restriction_count'] += 1
    graph.graph['turn_restrictions'] = restrictions
    graph.graph['excluded_ways'] = len(blocked & ways.keys())
    south, west, north, east = bounds
    for way_id, way in ways.items():
        if way_id in blocked:
            continue
        tags = way['tags']
        refs = way['nodes']
        direction = next((tags[k] for k in ('oneway:motorcar', 'oneway:motor_vehicle', 'oneway:vehicle', 'oneway')
                          if k in tags), 'yes' if tags.get('junction') in ('roundabout', 'circular')
                          or tags['highway'] == 'motorway' else 'no')
        for u, v in zip(refs, refs[1:]):
            if u == v or u not in nodes or v not in nodes:
                continue
            a, b = nodes[u], nodes[v]
            # Recursion returns whole ways; truncate at the search rectangle.
            inside_a = south <= a['lat'] <= north and west <= a['lon'] <= east
            inside_b = south <= b['lat'] <= north and west <= b['lon'] <= east
            if not (inside_a and inside_b):
                continue
            length = haversine_m([a['lat'], a['lon']], [b['lat'], b['lon']])
            if length <= 0:
                continue
            for ref, node in ((u, a), (v, b)):
                graph.add_node(ref, x=node['lon'], y=node['lat'], **node.get('tags', {}),
                               boundary=min(node['lat'] - south, north - node['lat']) < .0005
                               or min(node['lon'] - west, east - node['lon']) < .001)
            attrs = {**tags, 'osmid': way_id, 'length': length}
            for a, b, orientation, enabled in ((u, v, 'forward', direction != '-1'),
                                              (v, u, 'backward', direction not in ('yes', '1', 'true'))):
                access = next((tags[k + ':' + orientation] for k in ('motorcar', 'motor_vehicle', 'vehicle', 'access')
                               if k + ':' + orientation in tags), 'yes')
                if enabled and access in PERMITTED:
                    limit = tags.get('maxspeed:' + orientation, tags.get('maxspeed'))
                    if 'maxspeed:conditional' in tags or 'maxspeed:' + orientation + ':conditional' in tags:
                        limit = None  # The time-dependent limit cannot be established.
                    graph.add_edge(a, b, **{**attrs, 'maxspeed': limit})
    if not graph:
        raise ValueError('Ingen tilgjengelige bilveier i dette området.')
    return graph


def load_roads(lat: float, lon: float, radius: int) -> nx.MultiDiGraph:
    with REGION_LOCK:
        for key, (loaded, center, graph) in list(REGIONS.items()):
            if time.monotonic() - loaded < 600 and key[2] == radius and haversine_m([lat, lon], center) < radius / 4:
                REGIONS.move_to_end(key)
                return graph.copy()
    dy = radius / 111_111
    dx = dy / max(.01, math.cos(math.radians(lat)))
    bounds = [max(-90, lat - dy), max(-180, lon - dx), min(90, lat + dy), min(180, lon + dx)]
    bbox = ','.join(str(n) for n in bounds)
    query = f'[out:json][timeout:25];(way["highway"]({bbox});relation["type"="restriction"]({bbox}););(._;>;);out body;'
    endpoints = [os.environ['OVERPASS_URL']] if os.environ.get('OVERPASS_URL') else [
        'https://overpass-api.de/api/interpreter', 'https://overpass.kumi.systems/api/interpreter']
    failures = []
    for endpoint in endpoints:
        try:
            response = httpx.post(endpoint, data={'data': query}, timeout=35,
                                  headers={'User-Agent': 'VikepliktLokke/0.2 (OSM traffic gap research)'})
            response.raise_for_status()
            data = response.json()
            if data.get('remark') or 'elements' not in data:
                raise ValueError('Overpass leverte ufullstendige data. Prøv mindre søkeradius.')
            graph = graph_from_osm(data, bounds)
            with REGION_LOCK:
                REGIONS[lat, lon, radius] = (time.monotonic(), [lat, lon], graph)
                while len(REGIONS) > 4:
                    REGIONS.popitem(last=False)
            return graph.copy()
        except (httpx.HTTPError, ValueError) as exc:
            failures.append(str(exc))
    raise ValueError('Kunne ikke hente veinett og svingeregler fra OpenStreetMap. Prøv igjen senere. ' + failures[-1])


def select_point(graph: nx.MultiDiGraph, lat: float, lon: float, mode: str) -> tuple:
    """Snap to a junction or split the closest road for an unmapped driveway."""
    click = [lat, lon]
    if mode == 'junction':
        candidates = [n for n in graph if len(set(graph.predecessors(n)) | set(graph.successors(n))) >= 3]
        if not candidates:
            raise ValueError('Ingen kryss funnet. Velg utkjøring langs vei eller øk søkeradius.')
        node = min(candidates, key=lambda n: haversine_m(click, [graph.nodes[n]['y'], graph.nodes[n]['x']]))
        distance = haversine_m(click, [graph.nodes[node]['y'], graph.nodes[node]['x']])
        if distance > 150:
            raise ValueError('Nærmeste kryss er over 150 m unna. Klikk nærmere et kryss, eller velg utkjøring langs vei.')
        return node, distance
    scale = math.cos(math.radians(lat))
    best = None
    for u, v, key in graph.edges(keys=True):
        a, b = graph.nodes[u], graph.nodes[v]
        ax, ay = (a['x'] - lon) * scale, a['y'] - lat
        bx, by = (b['x'] - lon) * scale, b['y'] - lat
        dx, dy = bx - ax, by - ay
        fraction = max(0, min(1, -(ax * dx + ay * dy) / (dx * dx + dy * dy)))
        point = [a['y'] + fraction * (b['y'] - a['y']), a['x'] + fraction * (b['x'] - a['x'])]
        distance = haversine_m(click, point)
        if best is None or distance < best[0]:
            best = (distance, u, v, fraction, point)
    distance, u, v, fraction, point = best
    if distance > 100:
        raise ValueError('Ingen bilvei innen 100 m fra punktet. Velg et punkt nærmere veien.')
    if fraction < .00001:
        return u, distance
    if fraction > .99999:
        return v, distance
    node = 'selected-driveway'
    graph.add_node(node, y=point[0], x=point[1])
    for a, b, ratio in ((u, v, fraction), (v, u, 1 - fraction)):
        if not graph.has_edge(a, b):
            continue
        for key, attrs in list(graph[a][b].items()):
            graph.remove_edge(a, b, key)
            graph.add_edge(a, node, **{**attrs, 'length': attrs['length'] * ratio})
            graph.add_edge(node, b, **{**attrs, 'length': attrs['length'] * (1 - ratio)})
    return node, distance
