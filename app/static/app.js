/* Map selection and an explicitly idealized circulating traffic stream. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const fmt = (v, dp = 1) => Number(v).toLocaleString('nb-NO', {maximumFractionDigits: dp});
  const S = TrafficScene;
  const state = {departures: [], situationLayer: null, ego: null, intent: null, mode: null, session: null, point: null, junction: null, approaches: [],
    routes: null, selected: null, response: null, cars: [], road: null, waiting: null,
    approachesLayer: null, boundary: null, elapsed: 0, lastFrame: performance.now(),
    playing: true, operation: 0, controller: null, busy: false, settings: null};
  const map = L.map('map').setView([62.7379, 7.1608], 14);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
  }).addTo(map);
  const carIcon = L.divIcon({className: '', html: '<div class="vehicle traffic-vehicle car-pin"><div class="vehicle-body">' + S.vehicleSvg() + '</div></div>', iconSize: [18, 31], iconAnchor: [9, 15]});
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
    $('counter').textContent = '0 s';
    if (state.junction) state.waiting?.setLatLng([state.junction.lat, state.junction.lon]);
    state.ego?.getElement()?.classList.remove('can-enter'); $('situation').classList.remove('can-enter');
    const caption = state.ego?.getElement()?.querySelector('.ego-caption');
    if (caption) caption.textContent = 'DU · venter på beregning';
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
      radius: 5, color: '#b36516', fillColor: '#fff6df', fillOpacity: .8, weight: 2, dashArray: '3 3'
    }).addTo(map).bindPopup('Valgt kryss / utkjøring. Ved en konflikt viser markøren hvor kjørebanene møtes.');
  }
  function chosenWaiting() {
    if (state.mode === 'demo' || $('selection-mode').value === 'driveway') {
      const traffic = state.approaches.find(a => a.id === $('approach').value);
      const origin = $('driveway-side').value === 'auto' ? (traffic ? S.incomingHeading(traffic) - 90 : 0) : Number($('driveway-side').value);
      const center = [state.junction.lat, state.junction.lon];
      return {id: 'virtual', name: 'Utkjøringen (illustrert)', coordinates: [S.offset(center, origin, 75), center]};
    }
    return state.approaches.find(a => a.id === $('waiting-approach').value);
  }
  function turnLabel(waiting, departure) {
    const turn = S.delta(S.incomingHeading(waiting), S.outgoingHeading(departure));
    return Math.abs(turn) < 30 ? 'Rett frem' : turn > 0 ? 'Til høyre' : 'Til venstre';
  }
  function updateScenario(preferredTraffic = null) {
    if (!state.junction) return false;
    const driveway = $('selection-mode').value === 'driveway';
    $('driveway-controls').hidden = !driveway;
    if (!driveway && !$('waiting-approach').value) {
      const preferred = state.approaches.find(a => a.id === preferredTraffic) || state.approaches[0];
      const waiting = state.approaches.find(a => preferred && S.fromRight(a, preferred)) ||
        state.approaches.find(a => S.rightCandidates(a, state.approaches).length);
      if (waiting) $('waiting-approach').value = waiting.id;
    }
    const waiting = chosenWaiting();
    const right = waiting ? S.rightCandidates(waiting, state.approaches) : [];
    for (const option of $('approach').options) option.disabled = !right.some(a => a.id === option.value);
    if (!right.some(a => a.id === $('approach').value)) $('approach').value = right[0]?.id || '';
    const oldDestination = $('destination').value;
    const destinations = state.departures.filter(d => waiting && (waiting.id === 'virtual' || waiting.allowed_departures.includes(d.id)));
    $('destination').replaceChildren();
    for (const d of destinations) $('destination').append(new Option(turnLabel(waiting, d) + ' · ' + d.name, d.id));
    const straight = [...destinations].sort((a, b) => Math.abs(S.delta(S.incomingHeading(waiting), S.outgoingHeading(a))) - Math.abs(S.delta(S.incomingHeading(waiting), S.outgoingHeading(b))))[0];
    $('destination').value = destinations.some(d => d.id === oldDestination) ? oldDestination : straight?.id || '';
    $('destination').disabled = !destinations.length;
    return !!right.length;
  }
  function paintSituation() {
    remove(state.situationLayer); state.situationLayer = L.layerGroup().addTo(map); state.ego = null;
    if (!state.junction) return;
    const waiting = chosenWaiting(), destination = state.departures.find(d => d.id === $('destination').value);
    if (!waiting) { $('situation-title').textContent = 'Velg veien der du står'; return; }
    const coords = waiting.coordinates, available = coords.slice(1).reduce((total, p, i) => total + S.distance(coords[i], p), 0);
    const pose = S.along(coords, Math.min(22, available * .65), true);
    // Lane centers are illustrative offsets from OSM centerlines.
    const egoPosition = S.offset(pose.point, pose.heading + 90, 2.2);
    const turn = destination ? turnLabel(waiting, destination) : '';
    const signal = turn === 'Til høyre' ? 'signal-right' : turn === 'Til venstre' ? 'signal-left' : '';
    state.ego = L.marker(egoPosition, {zIndexOffset: 1000, icon: L.divIcon({className: '',
      html: '<div class="vehicle ego-vehicle ' + signal + '"><div class="vehicle-body" style="transform:rotate(' + pose.heading + 'deg)">' + S.vehicleSvg() + '</div><span class="ego-caption">DU · venter her</span></div>',
      iconSize: [28, 48], iconAnchor: [14, 24]})}).addTo(state.situationLayer)
      .bindTooltip('Din bil venter før konfliktpunktet', {direction: 'left'});
    state.ego.getElement().dataset.lat = egoPosition[0]; state.ego.getElement().dataset.lon = egoPosition[1];
    state.ego.getElement().dataset.heading = pose.heading;
    if (waiting.id === 'virtual') L.polyline(coords, {color: '#8756a9', weight: 7, dashArray: '8 6', opacity: .8, interactive: false}).addTo(state.situationLayer);
    const signPosition = S.offset(S.offset(pose.point, pose.heading, 6), pose.heading + 90, 8);
    L.marker(signPosition, {icon: L.divIcon({className: '', html: '<div class="yield-sign"><svg viewBox="0 0 36 44" aria-hidden="true"><path d="M18 26V44" stroke="#64727b" stroke-width="3"/><path d="M3 3H33L18 29Z" fill="#fff7df" stroke="#da3938" stroke-width="4" stroke-linejoin="round"/></svg></div>', iconSize: [30, 37], iconAnchor: [15, 37]})})
      .addTo(state.situationLayer).bindTooltip('Vikeplikt · illustrert skilt, ikke bekreftet kartdata');
    if (destination) {
      const outgoingLength = destination.coordinates.slice(1).reduce((total, p, i) => total + S.distance(destination.coordinates[i], p), 0);
      const end = S.along(destination.coordinates, Math.min(55, outgoingLength * .8));
      const outPosition = S.offset(end.point, end.heading + 90, 2.2);
      const center = [state.junction.lat, state.junction.lon];
      const desired = state.selected?.conflict?.ego_path ? [egoPosition, ...state.selected.conflict.ego_path, outPosition] : [egoPosition, center, outPosition];
      L.polyline(desired, {color: '#eeac22', weight: 5, opacity: .9, dashArray: '7 8', className: 'intent-path', interactive: false}).addTo(state.situationLayer);
      L.marker(outPosition, {icon: L.divIcon({className: '', html: '<div class="intent-arrow" style="--heading:' + end.heading + 'deg"><svg viewBox="0 0 28 40" aria-hidden="true"><path d="M14 3L25 16H19V37H9V16H3Z"/></svg></div><span class="intent-caption">DIT VIL DU · ' + turn + '</span>', iconSize: [28, 40], iconAnchor: [14, 20]}), interactive: false})
        .addTo(state.situationLayer).bindTooltip('DIT VIL DU · ' + turn, {direction: 'right'});
      state.intent = desired;
    } else state.intent = [egoPosition, [state.junction.lat, state.junction.lon]];
    $('focus').disabled = false;
    const traffic = state.approaches.find(a => a.id === $('approach').value);
    $('situation-title').textContent = destination ? 'Du vil ' + turn.toLowerCase() + ' · du venter før krysset' : 'Du står her · velg hvor du vil kjøre';
    $('situation-description').textContent = traffic ? 'Trafikken kommer fra din høyre side (' + direction(traffic) + '). ' +
      (waiting.id === 'virtual' ? 'Utkjøringen er tegnet som en illustrasjon.' : 'Din innkjøring: ' + waiting.name + '.') : 'Ingen innkommende vei fra høyre. Velg en annen innkjøring.';
  }
  function focusSituation() {
    map.stop();
    if (state.intent) map.fitBounds(L.latLngBounds(state.intent).pad(.35), {maxZoom: 19, animate: false});
    else if (state.junction) map.setView([state.junction.lat, state.junction.lon], 18);
  }
  async function loadDemo() {
    const op = begin(); clearRoute();
    state.mode = null; state.session = null; state.point = null;
    remove(state.approachesLayer); remove(state.boundary); remove(state.situationLayer); state.approaches = []; state.departures = [];
    $('approach').disabled = true; $('waiting-approach').disabled = true; $('share').disabled = true;
    status('Laster syntetisk demo …');
    try {
      const values = settings(), result = await request('/api/demo', values, op.signal);
      if (op.id !== state.operation) return;
      state.mode = 'demo'; state.settings = values; state.routes = result.routes; state.response = result; state.junction = result.junction;
      const center = [state.junction.lat, state.junction.lon];
      state.approaches = [{id: 'demo', name: 'Demoveien', coordinates: [S.offset(center, 270, 80), center]}];
      state.departures = [{id: 'demo-out', name: 'Demoveien', coordinates: [center, S.offset(center, 90, 90)]}];
      $('approach').replaceChildren(new Option('Fra høyre · demoveien', 'demo'));
      $('destination').replaceChildren(new Option('Til venstre · demoveien', 'demo-out'));
      pointMarker(); paintSituation(); showRoute(true);
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
      const waiting = chosenWaiting()?.id === item.id;
      const selected = $('approach').value === item.id;
      const color = waiting ? '#8756a9' : selected ? '#1478b4' : '#748a99';
      const select = () => {
        if (waiting || !chosenWaiting() || !S.fromRight(chosenWaiting(), item)) {
          if ($('selection-mode').value !== 'driveway') $('waiting-approach').value = item.id;
          if (!updateScenario()) { clearRoute(); paintApproaches(); status('Ingen vei fra høyre for denne innkjøringen. Velg en annen vei.', true); return; }
        } else $('approach').value = item.id;
        paintApproaches(); solve(true);
      };
      L.polyline(item.coordinates, {color, weight: selected || waiting ? 8 : 5, opacity: .85})
        .addTo(state.approachesLayer).on('click', event => { L.DomEvent.stopPropagation(event); select(); });
      const middle = item.coordinates[Math.floor((item.coordinates.length - 1) / 2)];
      L.marker(middle, {icon: L.divIcon({className: '', html: '<div class="approach-pin' + (waiting ? ' waiting' : '') + '">' + (Number(item.id) + 1) + '</div>', iconSize: [24, 24], iconAnchor: [12, 12]})})
        .addTo(state.approachesLayer).bindTooltip((Number(item.id) + 1) + ' · Fra ' + direction(item), {direction: 'top'})
        .on('click', select);
    }
    paintSituation();
  }
  async function loadIntersection(point) {
    const op = begin(); clearRoute(); state.mode = null; state.session = null; state.point = point;
    state.junction = point; pointMarker(); remove(state.approachesLayer); remove(state.boundary);
    remove(state.situationLayer); state.ego = null; state.intent = null; $('focus').disabled = true;
    $('approach').disabled = true; $('waiting-approach').disabled = true; $('share').disabled = true;
    status('Henter bilveier og svingeregler fra OpenStreetMap …');
    try {
      const result = await request('/api/intersection', {lat: point.lat, lon: point.lon,
        radius_m: Number($('radius').value), fallback_speed_kmh: settings().fallback_speed_kmh,
        selection_mode: $('selection-mode').value}, op.signal);
      if (op.id !== state.operation) return;
      state.mode = 'osm'; state.session = result.session_id; state.junction = result.junction;
      state.approaches = result.approaches; state.departures = result.departures; state.network = result.network;
      $('approach').replaceChildren(); $('waiting-approach').replaceChildren();
      for (const item of result.approaches) {
        const label = (Number(item.id) + 1) + ' · ' + item.name + ' fra ' + direction(item) + ' · ' + fmt(item.speed_kmh, 0) + ' km/t' + (item.speed_from_osm ? '' : ' (antatt)');
        $('approach').appendChild(new Option(label, item.id));
        $('waiting-approach').appendChild(new Option(label, item.id));
      }
      $('approach').disabled = false; $('waiting-approach').disabled = $('selection-mode').value === 'driveway';
      if ($('waiting-approach').disabled) $('waiting-approach').replaceChildren(new Option('Din utkjøring · illustrert på kartet', ''));
      const [s, w, n, e] = result.network.bounds;
      state.boundary = L.rectangle([[s, w], [n, e]], {color: '#637987', weight: 1, dashArray: '5 7', fill: false, interactive: false}).addTo(map);
      // Start with a real waiting road that sees the first traffic approach on its right.
      $('waiting-approach').value = '';
      const valid = updateScenario(result.approaches[0]?.id);
      pointMarker(); paintApproaches(); $('share').disabled = false;
      status('Fant ' + result.approaches.length + ' retninger. Punktet er flyttet ' + fmt(result.snap_distance_m) + ' m til veien. Velg trafikkretning.');
      finish(op); if (valid) await solve(true); else status('Ingen innkommende vei fra høyre. Velg en annen innkjøring eller utkjøring langs vei.', true);
    } catch (err) { if (err.name !== 'AbortError') status(err.message, true); }
    finally { finish(op); }
  }
  async function solve(fit = false) {
    if (state.mode === 'demo') return loadDemo();
    if (!state.session) return;
    if (!chosenWaiting() || !state.approaches.some(a => a.id === $('approach').value && S.fromRight(chosenWaiting(), a))) {
      clearRoute(); status('Velg en innkjøring med trafikk fra høyre.', true); return;
    }
    const op = begin(); clearRoute(); status('Finner korteste løkke med kjøreretning og støttede svingeregler …');
    try {
      const waiting = chosenWaiting();
      const values = settings(), response = await request('/api/solve', {...values, session_id: state.session, approach_id: $('approach').value,
        destination_id: $('destination').value, waiting_approach_id: waiting.id === 'virtual' ? null : waiting.id,
        virtual_origin_bearing: S.bearing([state.junction.lat, state.junction.lon], waiting.coordinates[0])}, op.signal);
      if (op.id !== state.operation) return;
      state.settings = values; state.routes = response.routes; state.response = response;
      if (response.mode === 'no_conflict') {
        paintSituation(); $('feasible').textContent = 'Ingen konflikt'; $('feasible').className = '';
        const caption = state.ego?.getElement()?.querySelector('.ego-caption');
        if (caption) caption.textContent = 'DU · ingen konflikt';
        $('situation-title').textContent = 'Denne trafikkstrømmen hindrer ikke valgt manøver'; $('entry-state').textContent = 'Denne trafikken hindrer ikke manøveren din';
        $('notes').textContent = response.message + ' Feltgeometrien er illustrert ut fra OSM-senterlinjer.';
        status(response.message); if (fit) focusSituation(); return;
      }
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
    paintSituation();
    if (fit) focusSituation();
    if (route.conflict) {
      const c = route.conflict;
      L.polyline(c.traffic_path, {color: '#207cc5', weight: 4, opacity: .8, interactive: false}).addTo(state.situationLayer);
      const at = c.traffic_path[Math.min(c.traffic_path.length - 1, Math.round(c.traffic_conflict_fraction * (c.traffic_path.length - 1)))];
      state.waiting?.setLatLng(at);
      $('situation-description').textContent = c.kind === 'merge' ? 'Blå biler fletter inn i samme utkjøring som deg.' : 'De blå bilenes kjørebane krysser din valgte kjørebane.';
    }
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
      (route.conflict ? (route.conflict.kind === 'merge' ? 'Konflikt: fletting inn i samme utkjøring. ' : 'Konflikt: kjørebanene krysser hverandre. ') + route.conflict.assumption + ' ' : '') +
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
  function trafficPose(route, t) {
    const c = route.conflict;
    if (c) {
      const before = c.traffic_in_distance_m / (route.approach_speed_kmh / 3.6);
      const after = c.traffic_out_distance_m / (route.departure_speed_kmh / 3.6);
      const local = t > route.cycle_seconds - before ? t - route.cycle_seconds : t;
      if (local >= -before && local <= after) {
        const index = Math.max(0, Math.min(c.traffic_path.length - 1.001, (local + before) / (before + after) * (c.traffic_path.length - 1)));
        const i = Math.floor(index), f = index - i, a = c.traffic_path[i], b = c.traffic_path[i + 1];
        return {point: [a[0] + (b[0]-a[0])*f, a[1] + (b[1]-a[1])*f], heading: S.bearing(a, b)};
      }
    }
    const location = interpolate(route.trajectory, t), ahead = interpolate(route.trajectory, (t + .05) % route.cycle_seconds);
    const heading = S.bearing(location, ahead);
    return {point: S.offset(location, heading + 90, 2.2), heading};
  }
  function frame(now) {
    if (state.playing) state.elapsed += Math.max(0, Math.min(.25, (now - state.lastFrame) / 1000)) * Number($('simulation-speed').value);
    state.lastFrame = now; const route = state.selected;
    if (route) {
      const n = simulationCars(), T = route.cycle_seconds, h = T / n;
      for (let i = 0; i < state.cars.length; i++) {
        // When capped, sample cars around the entire loop, preserving true phases.
        const index = Math.floor(i * n / state.cars.length);
        const t = (state.elapsed + index * T / n) % T;
        const pose = trafficPose(route, t), heading = pose.heading;
        state.cars[i].setLatLng(pose.point);
        const body = state.cars[i].getElement()?.querySelector('.vehicle-body');
        if (body) body.style.transform = 'rotate(' + heading + 'deg)';
      }
      const a = route.analysis, passage = a.vehicle_length_m / (route.crossing_speed_kmh / 3.6);
      const phase = ((state.elapsed - (route.conflict?.time_s || 0)) % h + h) % h, remaining = Math.max(0, h - phase);
      const occupied = phase < passage || h <= passage, acceptable = !occupied && remaining >= a.critical_clear_gap_s;
      const simulatedGap = Math.max(0, h - passage);
      $('entry-state').textContent = occupied ? 'En bil passerer punktet' : acceptable ? 'Stor nok luke til å kjøre ut nå' : 'Vent · luken er for liten';
      if (n > a.maximum_safe_cars) $('entry-state').textContent += ' · avstandskrav brytes';
      $('sim-gap').textContent = 'Fri luke: ' + fmt(simulatedGap, 3) + ' / ' + fmt(a.critical_clear_gap_s) + ' s' + (n > 150 ? ' · viser 150 av ' + n + ' biler' : '');
      $('entry-state').parentElement.classList.toggle('open', acceptable);
      $('gap-fill').style.width = fmt(Math.min(100, Math.max(0, remaining / a.critical_clear_gap_s * 100)), 0).replace(/\s/g, '') + '%';
      state.waiting?.setStyle({fillColor: acceptable ? '#288461' : '#fff6df'});
      state.ego?.getElement()?.classList.toggle('can-enter', acceptable);
      $('situation').classList.toggle('can-enter', acceptable);
      const caption = state.ego?.getElement()?.querySelector('.ego-caption');
      if (caption) caption.textContent = acceptable ? 'DU · stor nok luke' : 'DU · venter her';
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
      map.setView([lat, lon], 17, {animate: false}); status('Kartet er flyttet. Klikk på krysset eller utkjøringen.'); return;
    }
    $('search-button').disabled = true; status('Søker etter sted …');
    try {
      const res = await fetch('/api/search?q=' + encodeURIComponent(query)), data = await res.json();
      if (generation !== searchGeneration) return;
      if (!res.ok) throw Error(data.detail);
      for (const place of data.places) {
        const button = document.createElement('button'); button.textContent = place.name;
        button.addEventListener('click', () => { map.setView([place.lat, place.lon], 16, {animate: false}); $('search-results').replaceChildren(); status('Klikk på krysset eller utkjøringen du vil undersøke.'); });
        $('search-results').append(button);
      }
      status(data.places.length ? 'Velg et søkeresultat.' : 'Ingen steder funnet. Prøv et annet navn eller koordinater.');
    } catch (err) { status(err.message, true); }
    finally { if (generation === searchGeneration) $('search-button').disabled = false; }
  });
  $('locate').addEventListener('click', () => {
    if (!navigator.geolocation) { status('Nettleseren støtter ikke posisjon. Bruk stedssøk.', true); return; }
    navigator.geolocation.getCurrentPosition(pos => { map.setView([pos.coords.latitude, pos.coords.longitude], 17, {animate: false}); status('Klikk på utkjøringen i kartet.'); }, () => status('Posisjon er utilgjengelig. Bruk stedssøk eller naviger i kartet.', true), {timeout: 10000});
  });
  for (const id of ['speed', 'critical', 'follow', 'length', 'clearance']) {
    const suffix = {speed: ' km/t', critical: ' s', follow: ' s', length: ' m', clearance: ' m'}[id];
    $(id).addEventListener('input', () => { $(id + '-value').textContent = fmt($(id).value) + suffix; });
    $(id).addEventListener('change', () => { if (state.mode === 'demo') loadDemo(); else if (state.session) solve(); });
  }
  $('mode').addEventListener('change', () => showRoute(true));
  $('approach').addEventListener('change', () => { paintApproaches(); solve(true); });
  $('waiting-approach').addEventListener('change', () => {
    const valid = updateScenario(); paintApproaches();
    if (valid) solve(true); else { clearRoute(); status('Ingen trafikk fra høyre for denne innkjøringen.', true); }
  });
  $('driveway-side').addEventListener('change', () => {
    const valid = updateScenario(); paintApproaches();
    if (valid) solve(true); else { clearRoute(); status('Ingen trafikk fra høyre fra denne siden. Velg en annen side.', true); }
  });
  $('destination').addEventListener('change', () => { paintSituation(); solve(true); });
  $('focus').addEventListener('click', focusSituation);
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
    params.set('approach', $('approach').value); params.set('waiting', $('waiting-approach').value); params.set('route', $('mode').value); params.set('destination', $('destination').value); params.set('side', $('driveway-side').value);
    const url = new URL(location.href); url.search = params.toString();
    history.replaceState(null, '', url);
    try { await navigator.clipboard.writeText(url.href); status('Lenken til valgt punkt og parametere er kopiert.'); }
    catch { status('Lenken ligger nå i adressefeltet. Kopier den derfra.'); }
  });
  map.on('click', event => loadIntersection({lat: event.latlng.lat, lon: event.latlng.lng}));
  new ResizeObserver(() => map.invalidateSize()).observe($('map'));
  requestAnimationFrame(frame);
  if (matchMedia('(max-width: 750px)').matches) document.querySelector('.panel details').open = false;
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
    map.setView([lat, lon], 17, {animate: false}); await loadIntersection({lat, lon});
    if (state.mode === 'osm') {
      if (state.approaches.some(a => a.id === p.get('approach'))) $('approach').value = p.get('approach');
      if (!$('waiting-approach').disabled && state.approaches.some(a => a.id === p.get('waiting'))) $('waiting-approach').value = p.get('waiting');
      if ([...$('driveway-side').options].some(o => o.value === p.get('side'))) $('driveway-side').value = p.get('side');
      updateScenario();
      if ([...$('destination').options].some(d => d.value === p.get('destination'))) $('destination').value = p.get('destination');
      paintApproaches(); await solve(true);
    }
  }
  restore();
})();
