"""Idealized junction lane movements, separate from the right-hand priority rule.

OSM centerlines do not describe lanes. We construct local right-hand lane paths
with a visible 2.2 m offset; classify merging exits and intersecting swept paths.
This is a geometric model, not an authoritative lane survey.
"""
from __future__ import annotations
import math

LANE_OFFSET_M = 2.2
COMBINED_HALF_WIDTH_M = 2.0


def xy(point, center):
    return ((point[1] - center[1]) * 111111 * math.cos(math.radians(center[0])),
            (point[0] - center[0]) * 111111)


def latlon(point, center):
    return [center[0] + point[1] / 111111,
            center[1] + point[0] / (111111 * math.cos(math.radians(center[0])))]


def pose(points, center, incoming):
    coords = [xy(p, center) for p in (reversed(points) if incoming else points)]
    available = sum(math.dist(a, b) for a, b in zip(coords, coords[1:]))
    reach = min(20.0, available * .8)
    remaining = reach
    for a, b in zip(coords, coords[1:]):
        length = math.dist(a, b)
        if not length:
            continue
        if remaining <= length:
            position = (a[0] + (b[0] - a[0]) * remaining / length,
                        a[1] + (b[1] - a[1]) * remaining / length)
            sign = -1 if incoming else 1
            heading = (sign * (b[0] - a[0]) / length, sign * (b[1] - a[1]) / length)
            return position, heading, reach
        remaining -= length
    raise ValueError('Road geometry needs two distinct points')


def maneuver_path(incoming, outgoing, center):
    a, ah, reach_in = pose(incoming, center, True)
    b, bh, reach_out = pose(outgoing, center, False)
    # Right of a forward vector (east, north) is (north, -east).
    a = (a[0] + ah[1] * LANE_OFFSET_M, a[1] - ah[0] * LANE_OFFSET_M)
    b = (b[0] + bh[1] * LANE_OFFSET_M, b[1] - bh[0] * LANE_OFFSET_M)
    c = (a[0] + ah[0] * reach_in * .7, a[1] + ah[1] * reach_in * .7)
    d = (b[0] - bh[0] * reach_out * .7, b[1] - bh[1] * reach_out * .7)
    points = []
    for i in range(41):
        t = i / 40
        points.append(tuple((1-t)**3*a[j] + 3*(1-t)**2*t*c[j] + 3*(1-t)*t*t*d[j] + t**3*b[j] for j in (0, 1)))
    return points, reach_in, reach_out


def point_segment_distance(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = max(0, min(1, ((p[0]-a[0])*dx + (p[1]-a[1])*dy) / (dx*dx+dy*dy))) if dx or dy else 0
    return math.dist(p, (a[0]+t*dx, a[1]+t*dy))


def segments_intersect(a, b, c, d):
    def cross(p, q, r):
        return (q[0]-p[0])*(r[1]-p[1]) - (q[1]-p[1])*(r[0]-p[0])
    ab_c, ab_d, cd_a, cd_b = cross(a, b, c), cross(a, b, d), cross(c, d, a), cross(c, d, b)
    # Strict crossings; collinear/endpoint cases are handled by distance below.
    return ab_c * ab_d < 0 and cd_a * cd_b < 0


def paths_conflict(ego_in, ego_out, traffic_in, traffic_out, center, same_exit=False):
    ego, _, _ = maneuver_path(ego_in, ego_out, center)
    traffic, reach_in, reach_out = maneuver_path(traffic_in, traffic_out, center)
    fraction = None
    for j, (c, d) in enumerate(zip(traffic, traffic[1:])):
        for a, b in zip(ego, ego[1:]):
            if segments_intersect(a, b, c, d) or min(point_segment_distance(a, c, d), point_segment_distance(b, c, d),
                                                    point_segment_distance(c, a, b), point_segment_distance(d, a, b)) < COMBINED_HALF_WIDTH_M:
                fraction = (j + .5) / 40
                break
        if fraction is not None:
            break
    kind = 'merge' if same_exit else 'crossing' if fraction is not None else None
    return {'conflicts': bool(kind), 'kind': kind or 'separate',
            'ego_path': [latlon(p, center) for p in ego],
            'traffic_path': [latlon(p, center) for p in traffic],
            'traffic_conflict_fraction': fraction if fraction is not None else 1.0,
            'traffic_in_distance_m': reach_in, 'traffic_out_distance_m': reach_out,
            'assumption': 'Illustrerte høyrekjørefelt, 2.2 m fra OSM-senterlinjen; ikke verifisert feltgeometri.'}
