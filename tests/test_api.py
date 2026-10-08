from fastapi.testclient import TestClient
import pytest
from app import main


@pytest.fixture
def client(monkeypatch, streets):
    main.CACHE.clear()
    monkeypatch.setattr(main, 'load_roads', lambda *_: streets.copy())
    return TestClient(main.app)


def test_full_selection_and_solve(client):
    selection = client.post('/api/intersection', json={'lat': 62.7379, 'lon': 7.1608}).json()
    assert len(selection['approaches']) == 3
    assert len(selection['approaches'][0]['coordinates']) >= 2
    response = client.post('/api/solve', json={'session_id': selection['session_id'], 'approach_id': '0'})
    assert response.status_code == 200
    routes = response.json()['routes']
    assert routes['distance']['length_m'] < routes['time']['length_m']
    assert routes['distance']['analysis']['minimum_cars'] > routes['time']['analysis']['minimum_cars']


def test_driveway_api(client):
    selection = client.post('/api/intersection', json={'lat': 62.7379, 'lon': 7.1588, 'selection_mode': 'driveway'}).json()
    response = client.post('/api/solve', json={'session_id': selection['session_id'], 'approach_id': '0'})
    assert response.status_code == 200


def test_unknown_session_and_direction(client):
    assert client.post('/api/solve', json={'session_id': 'missing', 'approach_id': '0'}).status_code == 404
    s = client.post('/api/intersection', json={'lat': 62.7379, 'lon': 7.1608}).json()
    assert client.post('/api/solve', json={'session_id': s['session_id'], 'approach_id': 'bad'}).status_code == 422


def test_no_loop_has_useful_error(client):
    s = client.post('/api/intersection', json={'lat': 62.7379, 'lon': 7.1608}).json()
    r = client.post('/api/solve', json={'session_id': s['session_id'], 'approach_id': '2'})
    assert r.status_code == 422 and 'søkeområdet' in r.json()['detail']


@pytest.mark.parametrize('params', [ {'critical_clear_gap_s': 0}, {'vehicle_length_m': -1},
                                     {'fallback_speed_kmh': 999}, {'cycle_seconds': float('inf')} ])
def test_api_parameter_validation(client, params):
    if 'cycle_seconds' in params:
        params = {'critical_clear_gap_s': 'Infinity'}
    assert client.post('/api/demo', json=params).status_code == 422


def test_offline_demo_and_static_assets(client):
    assert client.get('/').status_code == 200
    assert client.get('/static/vendor/leaflet.js').status_code == 200
    assert client.get('/api/health').json() == {'status': 'ok'}
    assert client.post('/api/demo', json={}).json()['routes']['distance']['synthetic'] is True


def test_network_failure_is_reported(client, monkeypatch):
    def fail(*args):
        raise ValueError('Kunne ikke hente veinett')
    monkeypatch.setattr(main, 'load_roads', fail)
    r = client.post('/api/intersection', json={'lat': 62.7379, 'lon': 7.1608})
    assert r.status_code == 422 and 'veinett' in r.json()['detail']


def test_search(client, monkeypatch):
    monkeypatch.setattr(main, 'search_place', lambda q: [{'name': q, 'lat': 62.7, 'lon': 7.1}])
    assert client.get('/api/search?q=Molde').json()['places'][0]['name'] == 'Molde'
    assert client.get('/api/search?q=x').status_code == 422


def test_scene_has_outgoing_roads_and_turn_constraints(client):
    selection = client.post('/api/intersection', json={'lat': 62.7379, 'lon': 7.1608}).json()
    assert selection['departures']
    assert all(d['coordinates'][0] == [62.7379, 7.1608] for d in selection['departures'])
    waiting = selection['approaches'][1]  # Arriving southbound from north.
    # Cannot reverse onto the northern approach, can continue south to node 5.
    allowed = [d for d in selection['departures'] if d['id'] in waiting['allowed_departures']]
    assert all(d['coordinates'][1] != [62.7419, 7.1608] for d in allowed)
    assert any(d['coordinates'][1] == [62.7369, 7.1608] for d in allowed)
