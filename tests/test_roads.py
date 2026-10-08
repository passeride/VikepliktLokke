import networkx as nx
import pytest

from app.engine import find_shortest_loop, allowed_turn
from app.roads import graph_from_osm, select_point, driveable
from tests.conftest import street_data

BOUNDS = [62.73, 7.15, 62.75, 7.17]


def restriction(kind='no_left_turn', via=2, source=10, target=30, **tags):
    return {'type': 'relation', 'id': 100, 'tags': {'type': 'restriction', 'restriction': kind, **tags},
            'members': [{'type': 'way', 'ref': source, 'role': 'from'},
                        {'type': 'node', 'ref': via, 'role': 'via'},
                        {'type': 'way', 'ref': target, 'role': 'to'}]}


def test_distance_and_time_are_distinct(streets):
    distance = find_shortest_loop(streets, 1, 2, 0, optimize='distance')
    time = find_shortest_loop(streets, 1, 2, 0, optimize='time')
    assert distance['length_m'] < time['length_m']
    assert distance['cycle_seconds'] > time['cycle_seconds']
    assert time['control_points'][0]['type'] == 'traffic_signals'


@pytest.mark.parametrize('kind,target', [('no_left_turn', 30), ('only_right_turn', 20)])
def test_node_via_restrictions_change_shortest_loop(kind, target):
    data = street_data()
    data['elements'].append(restriction(kind, target=target))
    g = graph_from_osm(data, BOUNDS)
    result = find_shortest_loop(g, 1, 2, 0)
    assert all(s['name'] != 'Indre løkke' for s in result['segments'])
    assert g.graph['restriction_count'] == 1


def test_restriction_at_lap_seam(streets):
    streets.graph['turn_restrictions'][1] = [{'from': 30, 'to': [10], 'kind': 'no_left_turn'}]
    route = find_shortest_loop(streets, 1, 2, 0)
    assert all(s['name'] != 'Indre løkke' for s in route['segments'])


def test_dead_end_u_turn_is_not_a_loop(streets):
    streets.remove_edge(2, 6, 0)
    streets.remove_edge(2, 3, 0)
    # Only escape is 2->5->2, a reverse movement at the dead end.
    with pytest.raises(nx.NetworkXNoPath):
        find_shortest_loop(streets, 1, 2, 0)


def test_parallel_two_node_circuit_is_not_a_u_turn():
    g = nx.MultiDiGraph()
    g.add_node(1, y=62, x=7)
    g.add_node(2, y=62, x=7.01)
    g.add_edge(1, 2, length=100, osmid=1, maxspeed='50')
    g.add_edge(2, 1, length=130, osmid=2, maxspeed='50')
    assert find_shortest_loop(g, 1, 2, 0)['length_m'] == 230


def test_only_turn_checks_parallel_edge_identity(streets):
    streets.add_edge(2, 6, length=1, osmid=999)
    streets.graph['turn_restrictions'][2] = [{'from': 10, 'to': [30], 'kind': 'only_right_turn'}]
    assert allowed_turn(streets, (1, 2, 0), (2, 6, 0))
    assert not allowed_turn(streets, (1, 2, 0), (2, 6, 1))


def test_via_way_and_conditional_restrictions_exclude_source():
    for condition in ('via_way', 'conditional'):
        data = street_data()
        relation = restriction(source=30, target=10, via=1)
        if condition == 'via_way':
            relation['members'][1] = {'type': 'way', 'ref': 20, 'role': 'via'}
        else:
            relation['tags']['restriction:conditional'] = 'no_left_turn @ (Mo-Fr)'
        data['elements'].append(relation)
        g = graph_from_osm(data, BOUNDS)
        assert g.graph['excluded_restrictions'] == 1
        assert g.graph['excluded_ways'] == 1
        assert all(a['osmid'] != 30 for _, _, a in g.edges(data=True))


def test_motorcar_except_does_not_restrict():
    data = street_data()
    data['elements'].append(restriction(except_='unused'))
    data['elements'][-1]['tags']['except'] = 'bicycle;motorcar'
    g = graph_from_osm(data, BOUNDS)
    assert g.graph['restriction_count'] == 0
    assert any(s['name'] == 'Indre løkke' for s in find_shortest_loop(g, 1, 2, 0)['segments'])


def test_junction_snap_is_not_just_nearest_geometry_node(streets):
    n, distance = select_point(streets, 62.7385, 7.1608, 'junction')
    assert n == 2 and 50 < distance < 100
    with pytest.raises(ValueError):
        select_point(streets, 62.748, 7.168, 'junction')


def test_driveway_snap_splits_both_directions(streets):
    before = streets[5][2][0]['length']
    n, distance = select_point(streets, 62.7374, 7.1608, 'driveway')
    assert n == 'selected-driveway' and distance < 1
    assert not streets.has_edge(5, 2)
    assert streets.has_edge(5, n) and streets.has_edge(n, 2)
    assert streets.has_edge(2, n) and streets.has_edge(n, 5)
    assert streets[5][n][0]['length'] + streets[n][2][0]['length'] == pytest.approx(before)


def test_driveway_on_ring_makes_closed_loop(streets):
    n, _ = select_point(streets, 62.7379, 7.1588, 'driveway')
    route = find_shortest_loop(streets, 1, n, 0)
    assert route['trajectory'][0]['lat'] == route['trajectory'][-1]['lat']
    assert route['trajectory'][0]['lon'] == route['trajectory'][-1]['lon']


@pytest.mark.parametrize('tags,expected', [
    ({'highway': 'footway'}, False), ({'highway': 'residential', 'access': 'private'}, False),
    ({'highway': 'residential', 'access': 'no', 'motorcar': 'yes'}, True),
    ({'highway': 'residential', 'motor_vehicle': 'destination'}, False),
    ({'highway': 'residential', 'oneway:conditional': 'yes @ (Mo-Fr)'}, False),
    ({'highway': 'residential', 'access:conditional': 'no @ (Mo-Fr)'}, False),
    ({'highway': 'service', 'service': 'parking_aisle'}, False),
])
def test_access(tags, expected):
    assert driveable(tags) == expected


def test_reverse_oneway_and_directional_speed():
    data = street_data()
    data['elements'][6]['tags'].update({'oneway': '-1', 'maxspeed:backward': '30'})
    g = graph_from_osm(data, BOUNDS)
    assert not g.has_edge(1, 2)
    assert g[2][1][0]['maxspeed'] == '30'


def test_roundabout_is_implicitly_oneway():
    data = street_data()
    data['elements'][6]['tags'].pop('oneway')
    data['elements'][6]['tags']['junction'] = 'roundabout'
    g = graph_from_osm(data, BOUNDS)
    assert g.has_edge(1, 2) and not g.has_edge(2, 1)


@pytest.mark.parametrize('kind', ['unexpected_rule', 'only_u_turn'])
def test_unsupported_turn_kinds_fail_conservatively(kind):
    data = street_data()
    data['elements'].append(restriction(kind, source=30, target=10, via=1))
    g = graph_from_osm(data, BOUNDS)
    assert g.graph['excluded_restrictions'] == 1
    assert all(a['osmid'] != 30 for _, _, a in g.edges(data=True))


def test_unknown_permission_is_not_assumed_public():
    assert not driveable({'highway': 'residential', 'access': 'permit'})
    assert not driveable({'highway': 'residential', 'motor_vehicle': 'emergency'})


def test_download_retry_and_cache_isolation(monkeypatch):
    import httpx
    from app import roads
    roads.REGIONS.clear()
    calls = []
    def post(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            return httpx.Response(200, json={'remark': 'runtime timeout', 'elements': []},
                                  request=httpx.Request('POST', url))
        return httpx.Response(200, json=street_data(), request=httpx.Request('POST', url))
    monkeypatch.delenv('OVERPASS_URL', raising=False)
    monkeypatch.setattr(httpx, 'post', post)
    first = roads.load_roads(62.7379, 7.1608, 1200)
    first.remove_node(2)
    second = roads.load_roads(62.7379, 7.1608, 1200)
    assert 2 in second and len(calls) == 2
    roads.REGIONS.clear()


def test_download_does_not_accept_incomplete_overpass_data(monkeypatch):
    import httpx
    from app import roads
    roads.REGIONS.clear()
    def post(url, **kwargs):
        return httpx.Response(200, json={'remark': 'runtime timeout', 'elements': street_data()['elements']},
                              request=httpx.Request('POST', url))
    monkeypatch.setattr(httpx, 'post', post)
    with pytest.raises(ValueError, match='ufullstendige'):
        roads.load_roads(62.7379, 7.1608, 1200)
    assert not roads.REGIONS
