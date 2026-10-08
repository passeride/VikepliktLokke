import math
import networkx as nx
import pytest
from app.conflicts import paths_conflict
from app.engine import find_shortest_loop

CENTER = [62.7379, 7.1608]


def arm(bearing, incoming):
    r = math.radians(bearing)
    point = [CENTER[0] + 60*math.cos(r)/111111,
             CENTER[1] + 60*math.sin(r)/(111111*math.cos(math.radians(CENTER[0])))]
    return [point, CENTER] if incoming else [CENTER, point]


@pytest.mark.parametrize('rotation', [0, 45, 90, 180, 270])
def test_right_turn_and_oncoming_stream_use_separate_lanes(rotation):
    # The user's screenshot: me south->east; traffic east->west.
    result = paths_conflict(arm(180+rotation, True), arm(90+rotation, False),
                           arm(90+rotation, True), arm(270+rotation, False), CENTER)
    assert not result['conflicts'] and result['kind'] == 'separate'


def test_straight_ahead_crosses_right_hand_traffic():
    result = paths_conflict(arm(180, True), arm(0, False), arm(90, True), arm(270, False), CENTER)
    assert result['conflicts'] and result['kind'] == 'crossing'


def test_same_direction_exit_is_merging_not_opposing_lanes():
    result = paths_conflict(arm(180, True), arm(270, False), arm(90, True), arm(270, False), CENTER, same_exit=True)
    assert result['kind'] == 'merge'
    assert 0 <= result['traffic_conflict_fraction'] <= 1


def test_search_requires_a_conflicting_departure_not_just_incoming_road(streets):
    normal = find_shortest_loop(streets, 1, 2, 0)
    constrained = find_shortest_loop(streets, 1, 2, 0, allowed_departures={(2, 3, 0)})
    assert normal['departure_edge'] == [2, 6, 0]
    assert constrained['departure_edge'] == [2, 3, 0]
    assert constrained['length_m'] > normal['length_m']
    with pytest.raises(nx.NetworkXNoPath):
        find_shortest_loop(streets, 1, 2, 0, allowed_departures=set())


def t_junction():
    from app.engine import haversine_m
    g = nx.MultiDiGraph(bounds=[62.73, 7.15, 62.75, 7.17], turn_restrictions={},
                        restriction_count=0, excluded_restrictions=0, excluded_ways=0)
    points = {1: CENTER, 2: arm(90, False)[1], 3: arm(270, False)[1],
              4: arm(180, False)[1], 5: [CENTER[0]+.001, CENTER[1]-.001]}
    for node, (lat, lon) in points.items():
        g.add_node(node, y=lat, x=lon)
    for u,v,way in [(2,1,10),(1,2,10),(1,3,10),(4,1,20),(1,4,20),(3,5,30),(5,2,40)]:
        g.add_edge(u,v,osmid=way,length=haversine_m(points[u],points[v]),maxspeed='50',name='Testvei')
    return g


def test_api_rejects_false_blocking_claim_in_users_t_junction(monkeypatch):
    from app import main
    from fastapi.testclient import TestClient
    g = t_junction()
    monkeypatch.setattr(main, 'load_roads', lambda *_: g.copy())
    c = TestClient(main.app)
    s = c.post('/api/intersection',json={'lat':CENTER[0],'lon':CENTER[1]}).json()
    request = {'session_id':s['session_id'],'approach_id':'0','waiting_approach_id':'1','destination_id':'0'}
    # Eastward exit (id 0): turning right is separate from the westbound stream.
    response = c.post('/api/solve',json=request)
    assert response.status_code == 200
    assert response.json()['mode'] == 'no_conflict'
    assert response.json()['routes'] == {}
    assert 'blokkerer ikke' in response.json()['message']
    # Same waiting/traffic roads, westward exit instead: merging, and a valid loop.
    request['destination_id'] = '1'
    response = c.post('/api/solve',json=request)
    assert response.status_code == 200
    assert response.json()['mode'] == 'osm'
    assert response.json()['routes']['distance']['conflict']['kind'] == 'merge'


def test_api_requires_ego_maneuver_before_computing_blocking():
    from app import main
    from fastapi.testclient import TestClient
    response = TestClient(main.app).post('/api/solve',json={'session_id':'old','approach_id':'0'})
    assert response.status_code == 422
