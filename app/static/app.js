/* Browser view: real OSM data on demand; synthetic example always available. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const state = {
    session: null, routes: null, mode: "demo", junction: null, cars: [],
    simStart: performance.now(), elapsed: 0, playing: true,
    road: null, waiting: null, selected: null, lastResponse: null,
  };
  const map = L.map("map").setView([62.7379, 7.1608], 14);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "© OpenStreetMap contributors"
  }).addTo(map);
  const carIcon = L.divIcon({className: "", html: '<div class="car-pin"></div>', iconSize: [12, 21], iconAnchor: [6, 11]});
  const fmt = (v, dp = 1) => Number(v).toLocaleString("nb-NO", {maximumFractionDigits: dp});
  function settings() {
    return {
      fallback_speed_kmh: Number($("speed").value),
      critical_clear_gap_s: Number($("critical").value),
      min_following_clear_gap_s: Number($("follow").value),
      vehicle_length_m: Number($("length").value),
      min_bumper_clearance_m: Number($("clearance").value),
    };
  }
  function status(message) { $("status").textContent = message; }
  async function request(url, body) {
    const res = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    let data;
    try { data = await res.json(); } catch { throw Error("Ugyldig svar fra serveren"); }
    if (!res.ok) {
      const detail = data.detail;
      throw Error(typeof detail === "string" ? detail : JSON.stringify(detail || data));
    }
    return data;
  }
  async function loadDemo() {
    status("Laster syntetisk demo …");
    try {
      state.mode = "demo";
      state.session = null;
      $("approach").disabled = true;
      const result = await request("/api/demo", settings());
      state.lastResponse = result;
      state.routes = result.routes;
      state.junction = result.junction;
      $("calculate").disabled = false;
      showRoute();
      map.setView([result.junction.lat + 0.0012, result.junction.lon], 15);
      status("Syntetisk demonstrasjon aktiv. Klikk på et veikryss for å beregne en ekte OSM-sløyfe.");
    } catch (err) { status("Feil: " + err.message); }
  }
  async function loadIntersection(latlng) {
    status("Henter veinett fra OpenStreetMap. Dette kan ta litt tid …");
    $("calculate").disabled = true;
    try {
      const result = await request("/api/intersection", {
        lat: latlng.lat, lon: latlng.lng, radius_m: Number($("radius").value),
        fallback_speed_kmh: settings().fallback_speed_kmh,
      });
      state.mode = "osm";
      state.session = result.session_id;
      state.junction = result.junction;
      const choices = $("approach");
      choices.replaceChildren();
      for (const item of result.approaches) {
        const opt = document.createElement("option");
        opt.value = item.id;
        opt.textContent = item.name + " (" + fmt(item.speed_kmh, 0) + " km/t" + (item.speed_from_osm ? "" : ", antatt") + ") fra " + fmt(item.from.lat, 4) + ", " + fmt(item.from.lon, 4);
        choices.appendChild(opt);
      }
      choices.disabled = false;
      $("calculate").disabled = false;
      map.panTo([result.junction.lat, result.junction.lon]);
      status("Fant " + result.approaches.length + " innkommende retninger. Punktet ble flyttet " + fmt(result.snap_distance_m) + " m til nærmeste nettverksnode. Velg en retning.");
      await solve();
    } catch (err) { status("Kartoppslag feilet: " + err.message); }
  }
  async function solve() {
    if (state.mode === "demo") return loadDemo();
    if (!state.session) return;
    $("calculate").disabled = true;
    status("Beregner lukket kjørerute …");
    try {
      const response = await request("/api/solve", {...settings(), session_id: state.session, approach_id: $("approach").value});
      state.routes = response.routes;
      state.lastResponse = response;
      showRoute();
      status("Rutesøk ferdig. Kontroller svingeregler og vikeplikter manuelt.");
    } catch (err) { status("Rutesøk feilet: " + err.message); }
    finally { $("calculate").disabled = false; }
  }
  function interpolate(points, t) {
    if (!points || points.length < 2) return [62.7379, 7.1608];
    let low = 0, high = points.length - 1;
    while (low < high - 1) {
      const mid = (low + high) >> 1;
      if (points[mid].t < t) low = mid; else high = mid;
    }
    const a = points[low], b = points[high];
    const fraction = b.t <= a.t ? 0 : Math.max(0, Math.min(1, (t - a.t) / (b.t - a.t)));
    return [a.lat + (b.lat - a.lat) * fraction, a.lon + (b.lon - a.lon) * fraction];
  }
  function showRoute() {
    const route = state.routes && (state.routes[$("mode").value] || state.routes.distance || state.routes.time);
    if (!route) return;
    state.selected = route;
    const analysis = route.analysis;
    $("cars").textContent = fmt(analysis.minimum_cars, 0);
    $("distance").textContent = fmt(route.length_m, 0) + " m";
    $("lap").textContent = fmt(route.cycle_seconds) + " s";
    $("gap").textContent = fmt(analysis.clear_gap_s, 2) + " s";
    $("maxcars").textContent = fmt(analysis.maximum_safe_cars, 0);
    $("feasible").textContent = analysis.feasible ? "Mulig*" : "Uforenlig";
    $("feasible").className = analysis.feasible ? "" : "warning";
    if (state.road) map.removeLayer(state.road);
    if (state.waiting) map.removeLayer(state.waiting);
    for (const car of state.cars) map.removeLayer(car);
    state.cars = [];
    const coords = route.trajectory.map((p) => [p.lat, p.lon]);
    state.road = L.polyline(coords, {color: "#117ec4", weight: 6, opacity: 0.75}).addTo(map);
    state.waiting = L.circleMarker(coords[0], {radius: 9, color: "#8c3507", fillColor: "#e97821", fillOpacity: 1, weight: 2}).addTo(map).bindPopup("Konfliktpunkt / ventende bil");
    map.fitBounds(state.road.getBounds().pad(.35));
    const visible = Math.min(analysis.minimum_cars, 150);
    for (let i = 0; i < visible; i++) {
      state.cars.push(L.marker(coords[0], {icon: carIcon, interactive: false}).addTo(map));
    }
    state.elapsed = 0;
    state.simStart = performance.now();
    $("play").disabled = false;
    $("notes").textContent = (route.synthetic ? "SYNTETISK DEMO: Ikke faktisk veigeometri. " : "OSM-RUTE: Svingeforbud, kryssregulering, lyskryss og reell trafikk er ikke verifisert. ") +
      "Beregningen forutsetter perfekt jevn fordeling, like biler og konstant rundetid. " +
      (analysis.feasible ? "*Modellen tillater de nødvendige bilene gitt valgte avstandskrav." : "De nødvendige bilene bryter de valgte minsteavstandene.") +
      (analysis.minimum_cars > 150 ? " Viser kun 150 biler for ytelse." : "") +
      (route.segment_count ? " Fart fra OSM på " + route.segments_with_tagged_speed + " av " + route.segment_count + " veisegmenter." : "");
  }
  function frame(now) {
    if (state.playing) {
      const dt = Math.max(0, Math.min(.25, (now - state.simStart) / 1000));
      state.elapsed += dt * Number($("simulation-speed").value);
    }
    state.simStart = now;
    const route = state.selected;
    if (route && route.cycle_seconds > 0) {
      const number = route.analysis.minimum_cars;
      for (let i = 0; i < state.cars.length; i++) {
        const time = (state.elapsed + i * route.cycle_seconds / number) % route.cycle_seconds;
        state.cars[i].setLatLng(interpolate(route.trajectory, time));
      }
      $("counter").textContent = fmt(state.elapsed, 0) + " s simulert";
    }
    requestAnimationFrame(frame);
  }
  for (const id of ["speed", "critical", "follow", "length", "clearance"]) {
    const suffix = {speed: " km/t", critical: " s", follow: " s", length: " m", clearance: " m"}[id];
    $(id).addEventListener("input", () => {$(id + "-value").textContent = fmt($(id).value, 1) + suffix;});
    $(id).addEventListener("change", () => { if (state.mode === "demo") loadDemo(); else if (state.session) solve(); });
  }
  $("mode").addEventListener("change", showRoute);
  $("approach").addEventListener("change", solve);
  $("demo").addEventListener("click", loadDemo);
  $("calculate").addEventListener("click", solve);
  $("play").addEventListener("click", () => {
    state.playing = !state.playing;
    $("play").textContent = state.playing ? "Pause" : "Spill av";
  });
  map.on("click", (event) => loadIntersection(event.latlng));
  requestAnimationFrame(frame);
  loadDemo();
})();
