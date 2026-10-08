"""Browser integration tests against the real API, with synthetic OSM data."""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
import urllib.request

import pytest

pytestmark = pytest.mark.skipif(os.environ.get('BROWSER_TESTS') != '1', reason='Run make browser-test')


@pytest.fixture(scope='module')
def browser_page():
    from playwright.sync_api import sync_playwright
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    proc = subprocess.Popen([sys.executable, '-m', 'tests.browser_server', str(port)],
                            cwd=Path(__file__).resolve().parents[1])
    base = f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + '/api/health', timeout=.2)
                break
            except OSError:
                time.sleep(.05)
        else:
            raise RuntimeError('Browser test server did not start')
        with sync_playwright() as p:
            launch = {'headless': True}
            if os.environ.get('CHROMIUM_EXECUTABLE'):
                launch['executable_path'] = os.environ['CHROMIUM_EXECUTABLE']
            browser = p.chromium.launch(**launch)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            # Offline tests: only tile images are stubbed, all app/API requests run.
            page.route('https://tile.openstreetmap.org/**', lambda route: route.abort())
            errors = []
            page.on('pageerror', lambda exc: errors.append(str(exc)))
            yield page, base, errors
            assert not errors
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def choose_junction(page, base):
    page.goto(base)
    page.locator('#search').fill('62.7379, 7.1608')
    page.locator('#search-button').click()
    box = page.locator('#map').bounding_box()
    page.locator('#map').click(position={'x': box['width'] / 2, 'y': box['height'] / 2})
    page.wait_for_function("document.querySelector('#status').textContent.includes('Løkke funnet')")


def test_map_selection_routes_simulation_export(browser_page):
    page, base, _ = browser_page
    choose_junction(page, base)
    assert page.locator('#approach option').count() == 3
    assert page.locator('.approach-pin').count() == 3
    shortest = int(page.locator('#cars').inner_text())
    page.select_option('#mode', 'time')
    fastest = int(page.locator('#cars').inner_text())
    assert fastest < shortest
    page.locator('#sim-cars').fill(str(fastest - 1))
    page.locator('#sim-cars').dispatch_event('change')
    page.wait_for_function("document.querySelector('#entry-state').textContent.includes('Stor nok luke')", timeout=15000)
    page.locator('#minimum').click()
    assert int(page.locator('#sim-cars').input_value()) == fastest
    page.locator('#play').click()
    counter = page.locator('#counter').inner_text()
    page.wait_for_timeout(150)
    assert page.locator('#counter').inner_text() == counter
    page.locator('#route-details').evaluate('(node) => node.open = true')
    with page.expect_download() as event:
        page.locator('#export').click()
    import json
    data = json.loads(Path(event.value.path()).read_text())
    assert data['type'] == 'FeatureCollection'
    assert data['features'][0]['properties']['analysis']['minimum_cars'] == fastest


def test_failed_direction_clears_previous_result(browser_page):
    page, base, _ = browser_page
    choose_junction(page, base)
    page.select_option('#waiting-approach', '0')
    page.wait_for_function("document.querySelector('#status').textContent.includes('Ingen lukket løkke')")
    assert page.locator('#cars').inner_text() == '—'
    assert page.locator('.car-pin').count() == 0
    assert page.locator('#export').is_disabled()
    page.select_option('#waiting-approach', '1')
    page.wait_for_function("document.querySelector('#status').textContent.includes('Løkke funnet')")


def test_search_driveway_and_mobile(browser_page):
    page, base, _ = browser_page
    page.goto(base)
    page.locator('#search').fill('Molde')
    page.locator('#search-button').click()
    page.locator('#search-results button').first.click()
    page.select_option('#selection-mode', 'driveway')
    # The map is centered on fixture junction; click just west for a driveway.
    box = page.locator('#map').bounding_box()
    page.locator('#map').click(position={'x': box['width'] / 2 - 40, 'y': box['height'] / 2})
    page.wait_for_function("document.querySelector('#status').textContent.includes('Løkke funnet')")
    assert page.locator('#waiting-approach').is_disabled()
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.locator('#map').scroll_into_view_if_needed()
    assert page.locator('#map').is_visible()
    if os.environ.get('BROWSER_SCREENSHOT'):
        page.screenshot(path=os.environ['BROWSER_SCREENSHOT'], full_page=True)
    page.set_viewport_size({'width': 1440, 'height': 1000})


def test_ego_position_right_hand_priority_and_turn_signal(browser_page):
    page, base, _ = browser_page
    choose_junction(page, base)
    # Heading south from the northern approach: traffic from west is on the right.
    assert page.locator('#waiting-approach').input_value() == '1'
    assert page.locator('#approach').input_value() == '0'
    assert page.locator('#approach option[value="1"]').is_disabled()
    assert page.locator('#approach option[value="2"]').is_disabled()
    ego = page.locator('.ego-vehicle').locator('..')
    latitude = float(ego.get_attribute('data-lat'))
    longitude = float(ego.get_attribute('data-lon'))
    assert latitude > 62.7379 + .0001  # Car stands north of, not in, the junction.
    assert longitude < 7.1608  # Right-hand lane for a southbound vehicle.
    assert page.locator('.yield-sign').count() == 1
    assert page.locator('.intent-arrow').count() == 1
    options = page.locator('#destination option')
    left = next((o.get_attribute('value') for o in options.all() if o.inner_text().startswith('Til høyre')), None)
    assert left is not None
    page.select_option('#destination', left)
    assert page.locator('.ego-vehicle.signal-right').count() == 1
    assert 'til høyre' in page.locator('#situation-title').inner_text()
    assert page.locator('.traffic-vehicle svg').count() > 0
    if os.environ.get('BROWSER_DESKTOP_SCREENSHOT'):
        page.screenshot(path=os.environ['BROWSER_DESKTOP_SCREENSHOT'], full_page=True)
