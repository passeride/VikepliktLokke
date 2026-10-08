/* Map selection and an explicitly idealized circulating traffic stream. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const fmt = (v, dp = 1) => Number(v).toLocaleString('nb-NO', {maximumFractionDigits: dp});
  const state = {mode: null, session: null, point: null, junction: null, approaches: [],
    routes: null, selected: null, response: null, cars: [], road: null, waiting: null,
    approachesLayer: null, boundary: null, elapsed: 0, lastFrame: performance.now(),
    playing: true, operation: 0, controller: null, busy: false, settings: null};
  const map = L.map('map').setView([62.7379, 7.1608], 14);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
  }).addTo(map);
  const carIcon = L.divIcon({className: '', html: '<div class="car-pin"></div>', iconSize: [10, 17], iconAnchor: [5, 8]});
  function settings() {
    return {fallback_speed_kmh: Number($('speed').value), critical_clear_gap_s: Number($('critical').value),
      min_following_clear_gap_s: Number($('follow').value), vehicle_length_m: Number($('length').value),
      min_bumper_clearance_m: Number($('clearance').value)};
  }
  function status(message, error = false) { $('status').textContent = message; $('status').classList.toggle('error', error); }
  function begin() {
    state.controller?.abort();
    state.controller = new AbortController(); state.busy = true;
    $('calculate').disabled = true;
    return {id: ++state.operation, signal: state.controller.signal};
  }
  function finish(op) {
    if (op.id !== state.operation) return;
    state.busy = false; $('calculate').disabled = !state.mode;
  }
  async function request(url, body, signal) {
    const res = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body), signal});
    const data = await res.json();
    if (!res.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'Kontroller verdiene i skjemaet.');
    return data;
  }
  function remove(layer) { if (layer) map.removeLayer(layer); }
  function clearRoute() {
    remove(state.road); state.road = null;
    state.cars.forEach(remove); state.cars = [];
    state.routes = null; state.selected = null; state.response = null;
    for (const id of ['cars', 'distance', 'lap', 'gap', 'spacing', 'feasible']) $(id).textContent = '—';
    $('maxcars').textContent = ''; $('sim-gap').textContent = '';
    $('entry-state').textContent = 'Velg en løkke for å se tidslukene'; $('gap-fill').style.width = '0%';
    $('comparison').replaceChildren(); $('road-list').replaceChildren(); $('route-info').textContent = '';
    for (const id of ['play', 'sim-cars', 'minimum', 'fit', 'export']) $(id).disabled = true;
    $('notes').textContent = 'Velg trafikkretning og beregn løkken. Ingen aktiv simulering.';
  }
  function pointMarker() {
    remove(state.waiting);
    if (state.junction) state.waiting = L.circleMarker([state.junction.lat, state.junction.lon], {
      radius: 9, color: '#8c3507', fillColor: '#e97821', fillOpacity: 1, weight: 2
    }).addTo(map).bindPopup('Valgt kryss / utkjøring. Oransje: venter. Grønn: tilstrekkelig luke i modellen.');
  }
  async function loadDemo() {
    const op = begin(); clearRoute();
    state.mode = null; state.session = null; state.point = null;
    remove(state.approachesLayer); remove(state.boundary); state.approaches = [];
    $('approach').disabled = true; $('waiting-approach').disabled = true; $('share').disabled = true;
    status('Laster syntetisk demo …');
    try {
      const values = settings(), result = await request('/api/demo', values, op.signal);
      if (op.id !== state.operation) return;
      state.mode = 'demo'; state.settings = values; state.routes = result.routes; state.response = result; state.junction = result.junction;
      pointMarker(); showRoute(true);
      status('Syntetisk ellipse aktiv. Klikk på kartet for en løkke langs faktiske bilveier.');
    } catch (err) { if (err.name !== 'AbortError') status('Feil: ' + err.message, true); }
    finally { finish(op); }
  }
  function direction(item) {
    const origin = item.coordinates[Math.max(0, item.coordinates.length - 2)], end = item.coordinates.at(-1);
    const bearing = Math.atan2((origin[1] - end[1]) * Math.cos(end[0] * Math.PI / 180), origin[0] - end[0]) * 180 / Math.PI;
    return ['nord', 'nordøst', 'øst', 'sørøst', 'sør', 'sørvest', 'vest', 'nordvest'][Math.round((bearing + 360) / 45) % 8];
  }
  function paintApproaches() {
    remove(state.approachesLayer); state.approachesLayer = L.layerGroup().addTo(map);
    for (const item of state.approaches) {
      const waiting = $('waiting-approach').value === item.id;
      const selected = $('approach').value === item.id;
      const color = waiting ? '#8756a9' : selected ? '#1478b4' : '#748a99';
      const select = () => {
        if (waiting) { status('Denne veien er valgt som vikepliktig. Velg en annen trafikkretning.', true); return; }
        $('approach').value = item.id; paintApproaches(); solve(true);
      };
      L.polyline(item.coordinates, {color, weight: selected || waiting ? 8 : 5, opacity: .85})
        .addTo(state.approachesLayer).on('click', event => { L.DomEvent.stopPropagation(event); select(); });
      const middle = item.coordinates[Math.floor((item.coordinates.length - 1) / 2)];
      L.marker(middle, {icon: L.divIcon({className: '', html: '<div class="approach-pin' + (waiting ? ' waiting' : '') + '">' + (Number(item.id) + 1) + '</div>', iconSize: [24, 24], iconAnchor: [12, 12]})})
        .addTo(state.approachesLayer).bindTooltip((Number(item.id) + 1) + ' · Fra ' + direction(item), {direction: 'top'})
        .on('click', select);
    }
  }
  async function loadIntersection(point) {
    const op = begin(); clearRoute(); state.mode = null; state.session = null; state.point = point;
    state.junction = point; pointMarker(); remove(state.approachesLayer); remove(state.boundary);
    $('approach').disabled = true; $('waiting-approach').disabled = true; $('share').disabled = true;
    status('Henter bilveier og svingeregler fra OpenStreetMap …');
    try {
      const result = await request('/api/intersection', {lat: point.lat, lon: point.lon,
        radius_m: Number($('radius').value), fallback_speed_kmh: settings().fallback_speed_kmh,
        selection_mode: $('selection-mode').value}, op.signal);
      if (op.id !== state.operation) return;
      state.mode = 'osm'; state.session = result.session_id; state.junction = result.junction;
      state.approaches = result.approaches; state.network = result.network;
      $('approach').replaceChildren(); $('waiting-approach').replaceChildren(new Option('Ikke angitt / utenfor kartlagt vei', ''));
      for (const item of result.approaches) {
        const label = (Number(item.id) + 1) + ' · ' + item.name + ' fra ' + direction(item) + ' · ' + fmt(item.speed_kmh, 0) + ' km/t' + (item.speed_from_osm ? '' : ' (antatt)');
        $('approach').appendChild(new Option(label, item.id));
        $('waiting-approach').appendChild(new Option(label, item.id));
      }
      $('approach').disabled = false; $('waiting-approach').disabled = $('selection-mode').value === 'driveway';
      const [s, w, n, e] = result.network.bounds;
      state.boundary = L.rectangle([[s, w], [n, e]], {color: '#637987', weight: 1, dashArray: '5 7', fill: false, interactive: false}).addTo(map);
      pointMarker(); paintApproaches(); $('share').disabled = false;
      status('Fant ' + result.approaches.length + ' retninger. Punktet er flyttet ' + fmt(result.snap_distance_m) + ' m til veien. Velg trafikkretning.');
      finish(op); await solve(true);
    } catch (err) { if (err.name !== 'AbortError') status(err.message, true); }
    finally { finish(op); }
  }
  async function solve(fit = false) {
    if (state.mode === 'demo') return loadDemo();
    if (!state.session) return;
    if ($('waiting-approach').value === $('approach').value) {
      clearRoute(); status('Vikepliktig bil og prioritert trafikk må ha ulike innkommende veier.', true); return;
    }
    const op = begin(); clearRoute(); status('Finner korteste løkke med kjøreretning og støttede svingeregler …');
    try {
      const values = settings(), response = await request('/api/solve', {...values, session_id: state.session, approach_id: $('approach').value}, op.signal);
      if (op.id !== state.operation) return;
      state.settings = values; state.routes = response.routes; state.response = response;
      showRoute(fit); status('Løkke funnet. Den stiplede rammen viser området søket gjelder.');
    } catch (err) { if (err.name !== 'AbortError') status(err.message, true); }
    finally { finish(op); }
  }
  function interpolate(points, t) {
    let low = 0, high = points.length - 1;
    while (low < high - 1) { const mid = (low + high) >> 1; if (points[mid].t < t) low = mid; else high = mid; }
    const a = points[low], b = points[high], f = b.t <= a.t ? 0 : Math.max(0, Math.min(1, (t - a.t) / (b.t - a.t)));
    return [a.lat + (b.lat - a.lat) * f, a.lon + (b.lon - a.lon) * f];
  }
  function rebuildCars() {
    state.cars.forEach(remove); state.cars = [];
    if (!state.selected) return;
    const n = simulationCars(), visible = Math.min(n, 150);
    for (let i = 0; i < visible; i++) state.cars.push(L.marker([state.junction.lat, state.junction.lon], {icon: carIcon, interactive: false}).addTo(map));
  }
  function simulationCars() {
    const n = Number($('sim-cars').value);
    return Number.isInteger(n) && n >= 1 && n <= 10000 ? n : state.selected.analysis.minimum_cars;
  }
  function showRoute(fit = false) {
    const route = state.routes && (state.routes[$('mode').value] || state.routes.distance || state.routes.time);
    if (!route) return;
    state.selected = route; const a = route.analysis;
    $('cars').textContent = fmt(a.minimum_cars, 0); $('distance').textContent = fmt(route.length_m, 0) + ' m';
    $('lap').textContent = fmt(route.cycle_seconds) + ' s'; $('gap').textContent = fmt(a.clear_gap_s, 3) + ' s';
    $('spacing').textContent = fmt(a.bumper_spacing_at_crossing_m) + ' m';
    $('feasible').textContent = a.feasible ? 'Ja, i modellen' : 'Nei'; $('feasible').className = a.feasible ? '' : 'warning';
    $('maxcars').textContent = 'Maksimum: ' + fmt(a.maximum_safe_cars, 0) + ' biler';
    remove(state.road);
    state.road = L.polyline(route.trajectory.map(p => [p.lat, p.lon]), {color: '#1478b4', weight: 6, opacity: .7, interactive: false}).addTo(map);
    if (fit) map.fitBounds(state.road.getBounds().pad(.3));
    state.waiting?.bringToFront();
    $('sim-cars').value = a.minimum_cars; rebuildCars(); state.elapsed = 0; state.lastFrame = performance.now();
    for (const id of ['play', 'sim-cars', 'minimum', 'fit', 'export']) $(id).disabled = false;
    const network = state.response.network;
    $('notes').textContent = (route.synthetic ? 'SYNTETISK DEMO · Ellipsen følger ikke veinettet. ' : 'OSM-LØKKE · Korteste rute innen søkeområdet og støttede regler. ') +
      'Identiske biler, jevne ankomsttider og fart lik fartsgrense. Reell forkjørsrett, kø og uavbrutt flyt er ikke bekreftet. ' +
      (!a.feasible ? 'Nødvendig bilantall bryter de valgte avstandskravene. ' : '') +
      (route.control_points?.length ? route.control_points.length + ' kartlagte stopp-, vikeplikt- eller lyspunkter på løkken kan avbryte flyten. ' : '') +
      (network?.excluded_restrictions ? network.excluded_restrictions + ' komplekse/betingede svingeregler: berørte fra-veier er utelatt konservativt. ' : '') +
      (route.touches_boundary ? 'Ruten nærmer seg søkegrensen; prøv et større område. ' : '') +
      '*Færrest biler gjelder denne trafikkretningen; korteste distanse kan kreve flere biler enn raskeste løkke.';
    $('route-info').textContent = 'Fart fra OSM på ' + route.segments_with_tagged_speed + ' av ' + route.segment_count + ' segmenter. ' +
      (network ? network.restriction_count + ' støttede svingeregler i området. ' : '') +
      'Ved punktet: ' + fmt(a.crossing_speed_kmh) + ' km/t og ' + fmt(a.front_to_front_headway_s, 3) + ' s mellom bilfrontene. ' +
      (a.clear_gap_with_one_fewer_car_s != null ? 'Med én bil mindre blir fri luke ' + fmt(a.clear_gap_with_one_fewer_car_s, 3) + ' s (nødvendig: ' + fmt(a.critical_clear_gap_s) + ' s).' : 'Én bil er nok i denne idealiserte modellen.');
    const table = document.createElement('table');
    const tr = document.createElement('tr');
    for (const title of ['Løkke', 'Lengde', 'Rundetid', 'Biler']) { const th = document.createElement('th'); th.textContent = title; tr.append(th); }
    table.append(tr);
    for (const [key, candidate] of Object.entries(state.routes)) {
      const row = document.createElement('tr');
      for (const value of [key === 'distance' ? 'Kortest' : 'Raskest', fmt(candidate.length_m, 0) + ' m', fmt(candidate.cycle_seconds) + ' s', fmt(candidate.analysis.minimum_cars, 0)]) {
        const td = document.createElement('td'); td.textContent = value; row.append(td);
      }
      table.append(row);
    }
    $('comparison').replaceChildren(table); $('road-list').replaceChildren();
    const roads = [];
    for (const segment of route.segments || []) {
      const name = Array.isArray(segment.name) ? segment.name.join(' / ') : segment.name;
      const key = name + ':' + segment.speed_kmh + ':' + segment.speed_from_osm;
      if (roads.at(-1)?.key === key) roads.at(-1).length += segment.length_m;
      else roads.push({key, name, length: segment.length_m, speed: segment.speed_kmh, tagged: segment.speed_from_osm});
    }
    for (const road of roads) { const li = document.createElement('li'); li.textContent = road.name + ' · ' + fmt(road.length, 0) + ' m · ' + fmt(road.speed, 0) + ' km/t' + (road.tagged ? '' : ' (antatt)'); $('road-list').append(li); }
  }
  function frame(now) {
    if (state.playing) state.elapsed += Math.max(0, Math.min(.25, (now - state.lastFrame) / 1000)) * Number($('simulation-speed').value);
    state.lastFrame = now; const route = state.selected;
    if (route) {
      const n = simulationCars(), T = route.cycle_seconds, h = T / n;
      for (let i = 0; i < state.cars.length; i++) {
        // When capped, sample cars around the entire loop, preserving true phases.
        const index = Math.floor(i * n / state.cars.length);
        state.cars[i].setLatLng(interpolate(route.trajectory, (state.elapsed + index * T / n) % T));
      }
      const a = route.analysis, passage = a.vehicle_length_m / (route.crossing_speed_kmh / 3.6);
      const phase = state.elapsed % h, remaining = Math.max(0, h - phase);
      const occupied = phase < passage || h <= passage, acceptable = !occupied && remaining >= a.critical_clear_gap_s;
      const simulatedGap = Math.max(0, h - passage);
      $('entry-state').textContent = occupied ? 'En bil passerer punktet' : acceptable ? 'Stor nok luke til å kjøre ut nå' : 'Vent · luken er for liten';
      if (n > a.maximum_safe_cars) $('entry-state').textContent += ' · avstandskrav brytes';
      $('sim-gap').textContent = 'Fri luke: ' + fmt(simulatedGap, 3) + ' / ' + fmt(a.critical_clear_gap_s) + ' s' + (n > 150 ? ' · viser 150 av ' + n + ' biler' : '');
      $('entry-state').parentElement.classList.toggle('open', acceptable);
      $('gap-fill').style.width = fmt(Math.min(100, Math.max(0, remaining / a.critical_clear_gap_s * 100)), 0).replace(/\s/g, '') + '%';
      state.waiting?.setStyle({fillColor: acceptable ? '#288461' : '#e97821'});
      $('counter').textContent = fmt(state.elapsed, 0) + ' s simulert';
    }
    requestAnimationFrame(frame);
  }
  let searchGeneration = 0;
  $('search-form').addEventListener('submit', async event => {
    event.preventDefault(); const generation = ++searchGeneration, query = $('search').value.trim();
    $('search-results').replaceChildren();
    const coords = query.match(/^(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)$/);
    if (coords) {
      const lat = Number(coords[1]), lon = Number(coords[2]);
      if (Math.abs(lat) > 85 || Math.abs(lon) > 180) { status('Koordinatene er utenfor støttet område.', true); return; }
      map.setView([lat, lon], 17); status('Kartet er flyttet. Klikk på krysset eller utkjøringen.'); return;
    }
    $('search-button').disabled = true; status('Søker etter sted …');
    try {
      const res = await fetch('/api/search?q=' + encodeURIComponent(query)), data = await res.json();
      if (generation !== searchGeneration) return;
      if (!res.ok) throw Error(data.detail);
      for (const place of data.places) {
        const button = document.createElement('button'); button.textContent = place.name;
        button.addEventListener('click', () => { map.setView([place.lat, place.lon], 16); $('search-results').replaceChildren(); status('Klikk på krysset eller utkjøringen du vil undersøke.'); });
        $('search-results').append(button);
      }
      status(data.places.length ? 'Velg et søkeresultat.' : 'Ingen steder funnet. Prøv et annet navn eller koordinater.');
    } catch (err) { status(err.message, true); }
    finally { if (generation === searchGeneration) $('search-button').disabled = false; }
  });
  $('locate').addEventListener('click', () => {
    if (!navigator.geolocation) { status('Nettleseren støtter ikke posisjon. Bruk stedssøk.', true); return; }
    navigator.geolocation.getCurrentPosition(pos => { map.setView([pos.coords.latitude, pos.coords.longitude], 17); status('Klikk på utkjøringen i kartet.'); }, () => status('Posisjon er utilgjengelig. Bruk stedssøk eller naviger i kartet.', true), {timeout: 10000});
  });
  for (const id of ['speed', 'critical', 'follow', 'length', 'clearance']) {
    const suffix = {speed: ' km/t', critical: ' s', follow: ' s', length: ' m', clearance: ' m'}[id];
    $(id).addEventListener('input', () => { $(id + '-value').textContent = fmt($(id).value) + suffix; });
    $(id).addEventListener('change', () => { if (state.mode === 'demo') loadDemo(); else if (state.session) solve(); });
  }
  $('mode').addEventListener('change', () => showRoute(true));
  $('approach').addEventListener('change', () => { paintApproaches(); solve(true); });
  $('waiting-approach').addEventListener('change', () => {
    if ($('waiting-approach').value === $('approach').value) {
      const other = state.approaches.find(a => a.id !== $('waiting-approach').value);
      if (other) $('approach').value = other.id;
    }
    paintApproaches(); solve();
  });
  for (const id of ['radius', 'selection-mode']) $(id).addEventListener('change', () => { if (state.point) loadIntersection(state.point); });
  $('demo').addEventListener('click', loadDemo); $('calculate').addEventListener('click', () => solve());
  $('play').addEventListener('click', () => { state.playing = !state.playing; $('play').textContent = state.playing ? 'Pause' : 'Spill av'; });
  $('fit').addEventListener('click', () => { if (state.road) map.fitBounds(state.road.getBounds().pad(.3)); });
  $('sim-cars').addEventListener('change', () => { $('sim-cars').value = simulationCars(); rebuildCars(); state.elapsed = 0; });
  $('minimum').addEventListener('click', () => { $('sim-cars').value = state.selected.analysis.minimum_cars; rebuildCars(); state.elapsed = 0; });
  $('export').addEventListener('click', () => {
    const r = state.selected;
    const json = {type: 'FeatureCollection', features: [
      {type: 'Feature', geometry: {type: 'LineString', coordinates: r.trajectory.map(p => [p.lon, p.lat])},
        properties: {optimization: r.optimization, length_m: r.length_m, cycle_seconds: r.cycle_seconds,
          analysis: r.analysis, settings: state.settings, synthetic: !!r.synthetic,
          limitations: $('notes').textContent}},
      {type: 'Feature', geometry: {type: 'Point', coordinates: [state.junction.lon, state.junction.lat]}, properties: {role: 'conflict_point'}}]};
    const url = URL.createObjectURL(new Blob([JSON.stringify(json, null, 2)], {type: 'application/geo+json'}));
    const link = document.createElement('a'); link.href = url; link.download = 'vikeplikt-lokke.geojson'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  $('share').addEventListener('click', async () => {
    if (!state.point) return;
    const params = new URLSearchParams({lat: state.point.lat, lon: state.point.lon, kind: $('selection-mode').value, radius: $('radius').value});
    for (const id of ['speed', 'critical', 'follow', 'length', 'clearance']) params.set(id, $(id).value);
    params.set('approach', $('approach').value); params.set('waiting', $('waiting-approach').value); params.set('route', $('mode').value);
    const url = new URL(location.href); url.search = params.toString();
    history.replaceState(null, '', url);
    try { await navigator.clipboard.writeText(url.href); status('Lenken til valgt punkt og parametere er kopiert.'); }
    catch { status('Lenken ligger nå i adressefeltet. Kopier den derfra.'); }
  });
  map.on('click', event => loadIntersection({lat: event.latlng.lat, lon: event.latlng.lng}));
  new ResizeObserver(() => map.invalidateSize()).observe($('map'));
  requestAnimationFrame(frame);
  async function restore() {
    const p = new URLSearchParams(location.search), lat = Number(p.get('lat')), lon = Number(p.get('lon'));
    if (!p.has('lat') || !p.has('lon') || !Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 85 || Math.abs(lon) > 180) return;
    for (const id of ['speed', 'critical', 'follow', 'length', 'clearance']) {
      const input = $(id), value = Number(p.get(id));
      if (p.has(id) && value >= Number(input.min) && value <= Number(input.max)) { input.value = value; input.dispatchEvent(new Event('input')); }
    }
    if (['junction', 'driveway'].includes(p.get('kind'))) $('selection-mode').value = p.get('kind');
    if ([...$('radius').options].some(o => o.value === p.get('radius'))) $('radius').value = p.get('radius');
    if (['distance', 'time'].includes(p.get('route'))) $('mode').value = p.get('route');
    map.setView([lat, lon], 17); await loadIntersection({lat, lon});
    if (state.mode === 'osm') {
      if (state.approaches.some(a => a.id === p.get('approach'))) $('approach').value = p.get('approach');
      if (!$('waiting-approach').disabled && state.approaches.some(a => a.id === p.get('waiting'))) $('waiting-approach').value = p.get('waiting');
      paintApproaches(); await solve(true);
    }
  }
  restore();
})();
