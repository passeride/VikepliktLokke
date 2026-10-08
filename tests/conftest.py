"""A synthetic OSM-shaped street fixture. These are not real Molde streets."""
import pytest
from app.roads import graph_from_osm


def street_data():
    nodes = [(1, 62.7379, 7.1568), (2, 62.7379, 7.1608),
             (3, 62.7419, 7.1608), (4, 62.7419, 7.1568),
             (5, 62.7369, 7.1608), (6, 62.7389, 7.1588)]
    elements = [{'type': 'node', 'id': n, 'lat': lat, 'lon': lon} for n, lat, lon in nodes]
    elements[2]['tags'] = {'highway': 'traffic_signals'}
    for id_, refs, name, speed, oneway in [
        (10, [1, 2], 'Hovedveien', '50', 'yes'),
        (20, [2, 3, 4, 1], 'Ytre løkke', '80', 'no'),
        (30, [2, 6, 1], 'Indre løkke', '10', 'yes'),
        (40, [5, 2], 'Utkjøringen', '30', 'no'),
    ]:
        elements.append({'type': 'way', 'id': id_, 'nodes': refs,
                         'tags': {'highway': 'residential', 'name': name, 'maxspeed': speed, 'oneway': oneway}})
    return {'elements': elements}


def street_graph():
    return graph_from_osm(street_data(), [62.73, 7.15, 62.75, 7.17])


@pytest.fixture
def streets():
    return street_graph()
