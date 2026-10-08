/* Geometry in meters and clockwise compass bearings. No map-library dependency. */
(function (root) {
  'use strict';
  const rad = Math.PI / 180;
  function bearing(a, b) { return (Math.atan2((b[1] - a[1]) * Math.cos(a[0] * rad), b[0] - a[0]) / rad + 360) % 360; }
  function delta(a, b) { return ((b - a + 540) % 360) - 180; }
  function offset(point, heading, meters) {
    return [point[0] + Math.cos(heading * rad) * meters / 111111,
      point[1] + Math.sin(heading * rad) * meters / (111111 * Math.cos(point[0] * rad))];
  }
  function distance(a, b) { return Math.hypot((b[0] - a[0]) * 111111, (b[1] - a[1]) * 111111 * Math.cos(a[0] * rad)); }
  function along(points, meters, fromEnd = false) {
    const path = fromEnd ? [...points].reverse() : points;
    for (let i = 1; i < path.length; i++) {
      const length = distance(path[i - 1], path[i]);
      if (meters <= length) {
        const fraction = length ? meters / length : 0;
        return {point: [path[i - 1][0] + (path[i][0] - path[i - 1][0]) * fraction,
          path[i - 1][1] + (path[i][1] - path[i - 1][1]) * fraction],
          heading: bearing(fromEnd ? path[i] : path[i - 1], fromEnd ? path[i - 1] : path[i])};
      }
      meters -= length;
    }
    const a = path.at(-2), b = path.at(-1);
    return {point: b, heading: bearing(fromEnd ? b : a, fromEnd ? a : b)};
  }
  function incomingHeading(approach) { const p = approach.coordinates; return bearing(p.at(-2), p.at(-1)); }
  function outgoingHeading(departure) { return bearing(departure.coordinates[0], departure.coordinates[1]); }
  function fromRight(waiting, traffic) {
    const angle = delta(incomingHeading(waiting), incomingHeading(traffic) + 180);
    // Collinear/opposing traffic is not a right-hand approach. Allow skewed junctions.
    return angle > 20 && angle < 160;
  }
  function rightCandidates(waiting, approaches) {
    return approaches.filter(a => a.id !== waiting.id && fromRight(waiting, a))
      .sort((a, b) => Math.abs(delta(incomingHeading(waiting), incomingHeading(a) + 180) - 90)
        - Math.abs(delta(incomingHeading(waiting), incomingHeading(b) + 180) - 90));
  }
  function vehicleSvg() {
    return '<svg viewBox="0 0 28 48" aria-hidden="true"><rect class="tire" x="1" y="8" width="5" height="10" rx="2"/><rect class="tire" x="22" y="8" width="5" height="10" rx="2"/><rect class="tire" x="1" y="31" width="5" height="10" rx="2"/><rect class="tire" x="22" y="31" width="5" height="10" rx="2"/><rect class="body" x="4" y="2" width="20" height="44" rx="7"/><path class="glass" d="M7 13Q14 9 21 13L20 20H8Z"/><path class="glass" d="M8 31H20L21 37Q14 40 7 37Z"/><rect class="headlight" x="7" y="3" width="4" height="3" rx="1"/><rect class="headlight" x="17" y="3" width="4" height="3" rx="1"/><rect class="indicator left" x="4" y="4" width="3" height="5" rx="1"/><rect class="indicator right" x="21" y="4" width="3" height="5" rx="1"/><rect class="tail" x="6" y="41" width="4" height="3" rx="1"/><rect class="tail" x="18" y="41" width="4" height="3" rx="1"/></svg>';
  }
  const api = {bearing, delta, offset, distance, along, incomingHeading, outgoingHeading, fromRight, rightCandidates, vehicleSvg};
  if (typeof module !== 'undefined') module.exports = api;
  else root.TrafficScene = api;
})(typeof window !== 'undefined' ? window : globalThis);
