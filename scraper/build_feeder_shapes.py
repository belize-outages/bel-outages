"""
Derive approximate feeder service areas from the geocoded areas in data/areas.json
and write them to data/feeder_shapes.json as GeoJSON.

Run:  python scraper/build_feeder_shapes.py

These are NOT BEL's feeder boundaries. BEL does not publish those. Each shape is
the hull enclosing the places BEL has named in notices for that feeder, which is
a floor on the feeder's extent, never the true edge. Every feature carries
point_count and confidence so the map can say so out loud.

Geometry by point count:
  >= 3 points : Polygon, convex hull
     2 points : LineString, the corridor between them
     1 point  : Point
     0 points : no feature emitted

No dependencies. Convex hull is Andrew's monotone chain.
"""

import io
import json
import math
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")


def convex_hull(pts):
    """Andrew's monotone chain. Input/returns list of (lon, lat). CCW, closed."""
    pts = sorted(set(pts))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def centroid(pts):
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def km_between(a, b):
    dx = (b[0] - a[0]) * 111.32 * math.cos(math.radians((a[1] + b[1]) / 2))
    dy = (b[1] - a[1]) * 110.57
    return math.hypot(dx, dy)


def median_center(pts):
    lons = sorted(p[0] for p in pts)
    lats = sorted(p[1] for p in pts)
    m = len(pts) // 2
    return (lons[m], lats[m])


# A Belize distribution feeder does not run 45km from the middle of its own
# cluster. Anything past this is a geocoding collision, not a service area.
OUTLIER_KM = 45.0


def reject_outliers(pts, names, anchor=None):
    """Drop points implausibly far from the feeder. Returns (kept, rejected).

    GeoNames alternate names collide across districts: "Santa Ana" resolves to a
    "Santana" in Belize District, "Santa Marta" to "Santa Martha" in Orange Walk,
    and Belmopan's own "Maya Mopan" neighbourhood to a village in Stann Creek.
    Each collision dragged a shape tens of kilometres across the country.
    District matching alone does not catch these, because plenty of feeders
    genuinely do cross a district line, so distance is the honest test.

    The anchor is the load centre, which is where the feeder physically starts.
    That works even for a two-point feeder, where there is no cluster to take a
    median of and the earlier version simply gave up.
    """
    if len(pts) < 2:
        return pts, []
    c = anchor or median_center(pts)
    kept, bad = [], []
    for p, n in zip(pts, names):
        (kept if km_between(c, p) <= OUTLIER_KM else bad).append((p, n))
    if not kept:
        # Every point failed, so the anchor is more likely wrong than the data.
        return pts, []
    return [p for p, _ in kept], [(n, round(km_between(c, p), 1)) for p, n in bad]


def span_km(pts):
    """Rough greatest distance between any two points, for a size sanity check."""
    if len(pts) < 2:
        return 0.0
    best = 0.0
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            dx = (pts[j][0] - pts[i][0]) * 111.32 * math.cos(math.radians(pts[i][1]))
            dy = (pts[j][1] - pts[i][1]) * 110.57
            best = max(best, math.hypot(dx, dy))
    return round(best, 1)


def main():
    with io.open(os.path.join(DATA, "areas.json"), encoding="utf-8") as f:
        areas = json.load(f)["areas"]

    # A feeder starts at its load centre's substation, so that is the anchor
    # the distance test is measured from.
    def key(s):
        return re.sub(r"[^a-z0-9]+", "", (s or "").lower())
    anchors = {}
    for a in areas:
        if a.get("is_load_center") and a.get("lat") is not None:
            anchors[key(a["name"])] = (a["lon"], a["lat"])
    with io.open(os.path.join(DATA, "feeders.json"), encoding="utf-8") as f:
        fdoc = json.load(f)

    groups = {g["id"]: g for g in fdoc["feeders"]}
    groups.update({g["id"]: g for g in fdoc.get("unassigned_groups", [])})

    pts_by_feeder = defaultdict(list)
    names_by_feeder = defaultdict(list)
    for a in areas:
        if a["lat"] is None:
            continue
        for fid in a["feeders_seen"]:
            pts_by_feeder[fid].append((a["lon"], a["lat"]))
            names_by_feeder[fid].append(a["name"])

    features = []
    all_rejects = []
    for fid, raw_pts in sorted(pts_by_feeder.items()):
        g = groups.get(fid, {})

        # Deduplicate while keeping each point paired with its place name.
        seen, pairs = set(), []
        for p, nm in zip(raw_pts, names_by_feeder[fid]):
            if p not in seen:
                seen.add(p)
                pairs.append((p, nm))

        anchor = anchors.get(key(g.get("load_center")))
        if anchor is None and g.get("load_center"):
            for k, v in anchors.items():
                if k.startswith(key(g["load_center"])) or key(g["load_center"]).startswith(k):
                    anchor = v
                    break
        pts, rejected = reject_outliers(
            [p for p, _ in pairs], [nm for _, nm in pairs], anchor
        )
        if rejected:
            all_rejects.append((fid, rejected))
        kept_names = [nm for p, nm in pairs if p in set(pts)]
        names_by_feeder[fid] = kept_names
        n = len(pts)
        if n == 0:
            continue

        if n >= 3:
            ring = convex_hull(pts)
            if len(ring) >= 3:
                geom = {"type": "Polygon", "coordinates": [ring + [ring[0]]]}
            else:
                geom = {"type": "LineString", "coordinates": ring}
        elif n == 2:
            geom = {"type": "LineString", "coordinates": pts}
        else:
            geom = {"type": "Point", "coordinates": pts[0]}

        if n >= 6:
            conf = "indicative"
        elif n >= 3:
            conf = "sparse"
        else:
            conf = "too few points to enclose an area"

        total = len(g.get("areas", []))
        features.append(
            {
                "type": "Feature",
                "id": fid,
                "geometry": geom,
                "properties": {
                    "feeder_id": fid,
                    "load_center": g.get("load_center"),
                    "feeder": g.get("feeder"),
                    "district": g.get("district"),
                    "point_count": n,
                    "named_areas_total": total,
                    "geocoded_fraction": round(n / total, 2) if total else None,
                    "span_km": span_km(pts),
                    "centroid": [round(c, 5) for c in centroid(pts)],
                    "confidence": conf,
                    "places": sorted(set(names_by_feeder[fid])),
                    "source_confidence": g.get("confidence"),
                    "areas_list_truncated": g.get("truncated"),
                    "rejected_outliers": [
                        {"name": nm, "km_from_cluster": d}
                        for nm, d in dict(all_rejects).get(fid, [])
                    ],
                },
            }
        )

    out = {
        "type": "FeatureCollection",
        "meta": {
            "schema_version": 1,
            "built_by": "scraper/build_feeder_shapes.py",
            "warning": (
                "Approximate. Each polygon is the convex hull of the settlements BEL "
                "has named in outage notices for that feeder. It is a lower bound on "
                "the feeder's real extent, not a boundary. A convex hull also fills in "
                "gaps between named places, so it can cover ground the feeder does not "
                "serve. Never present these as BEL's official feeder areas."
            ),
            "feature_count": len(features),
        },
        "features": features,
    }

    with io.open(os.path.join(DATA, "feeder_shapes.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write("\n")

    print("features written :", len(features))
    print()
    print("%-28s %5s %6s %8s  %s" % ("feeder", "pts", "span", "geom", "confidence"))
    for ft in features:
        p = ft["properties"]
        print(
            "%-28s %5d %5.1fkm %8s  %s"
            % (
                p["feeder_id"],
                p["point_count"],
                p["span_km"],
                ft["geometry"]["type"],
                p["confidence"],
            )
        )
    if all_rejects:
        print()
        print("Rejected as geocoding collisions (too far from the feeder's cluster):")
        for fid, rej in all_rejects:
            for nm, d in rej:
                print("  %-28s %-24s %6.1f km" % (fid, nm, d))

    poly = sum(1 for f in features if f["geometry"]["type"] == "Polygon")
    print()
    print("polygons: %d, lines: %d, points: %d" % (
        poly,
        sum(1 for f in features if f["geometry"]["type"] == "LineString"),
        sum(1 for f in features if f["geometry"]["type"] == "Point"),
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
