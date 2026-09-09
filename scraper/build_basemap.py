"""
Build data/belize.json, the district outlines the map draws on.

Run:  python scraper/build_basemap.py

Source: geoBoundaries gbOpen ADM1 for Belize (CC BY 4.0), pinned to commit
9469f09 so the shape never changes underneath us. Downloaded once, simplified,
and committed. The site makes no request for it at runtime.

Simplification is Douglas-Peucker with coordinates rounded to 4 decimal places,
about 11 metres. That is far finer than anyone needs on a phone-sized map of a
whole country, and it cuts the file to a fraction of the original.
"""

import io
import json
import math
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(HERE, "cache")

URL = (
    "https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/"
    "gbOpen/BLZ/ADM1/geoBoundaries-BLZ-ADM1_simplified.geojson"
)

TOLERANCE = 0.003   # degrees, roughly 330m
PRECISION = 4       # decimal places, roughly 11m
MIN_RING = 6        # drop slivers with fewer vertices than this


def perp_distance(p, a, b):
    if a == b:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def douglas_peucker(pts, tol):
    if len(pts) < 3:
        return pts
    worst, idx = 0.0, 0
    for i in range(1, len(pts) - 1):
        d = perp_distance(pts[i], pts[0], pts[-1])
        if d > worst:
            worst, idx = d, i
    if worst <= tol:
        return [pts[0], pts[-1]]
    left = douglas_peucker(pts[: idx + 1], tol)
    right = douglas_peucker(pts[idx:], tol)
    return left[:-1] + right


def simplify_ring(ring):
    out = douglas_peucker([tuple(p) for p in ring], TOLERANCE)
    out = [[round(x, PRECISION), round(y, PRECISION)] for x, y in out]
    # A ring must stay closed after rounding.
    if out and out[0] != out[-1]:
        out.append(out[0])
    return out


def simplify_geom(geom):
    if geom["type"] == "Polygon":
        rings = [simplify_ring(r) for r in geom["coordinates"]]
        rings = [r for r in rings if len(r) >= MIN_RING]
        return {"type": "Polygon", "coordinates": rings} if rings else None
    polys = []
    for poly in geom["coordinates"]:
        rings = [simplify_ring(r) for r in poly]
        rings = [r for r in rings if len(r) >= MIN_RING]
        if rings:
            polys.append(rings)
    return {"type": "MultiPolygon", "coordinates": polys} if polys else None


def count(geom):
    if geom["type"] == "Polygon":
        return sum(len(r) for r in geom["coordinates"])
    return sum(len(r) for poly in geom["coordinates"] for r in poly)


def main():
    os.makedirs(CACHE, exist_ok=True)
    cached = os.path.join(CACHE, "blz-adm1-raw.geojson")
    if os.path.exists(cached):
        with io.open(cached, encoding="utf-8") as f:
            gj = json.load(f)
    else:
        req = urllib.request.Request(URL, headers={"User-Agent": "bel-outages/0.1"})
        raw = urllib.request.urlopen(req, timeout=120).read()
        with io.open(cached, "wb") as f:
            f.write(raw)
        gj = json.loads(raw)

    before = after = 0
    features = []
    for f in gj["features"]:
        before += count(f["geometry"])
        g = simplify_geom(f["geometry"])
        if not g:
            continue
        after += count(g)
        features.append(
            {
                "type": "Feature",
                "properties": {"name": f["properties"].get("shapeName")},
                "geometry": g,
            }
        )

    out = {
        "type": "FeatureCollection",
        "meta": {
            "source": "geoBoundaries gbOpen ADM1 Belize, commit 9469f09",
            "licence": "CC BY 4.0, geoBoundaries (William & Mary geoLab)",
            "simplified": "Douglas-Peucker tolerance %s deg, coords rounded to %d dp"
            % (TOLERANCE, PRECISION),
        },
        "features": features,
    }

    path = os.path.join(DATA, "belize.json")
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"), ensure_ascii=False)
        f.write("\n")

    size = os.path.getsize(path)
    print("districts   :", len(features))
    print("vertices    : %d -> %d (%.0f%% removed)" % (before, after, 100 * (1 - after / before)))
    print("file size   : %.1f KB" % (size / 1024))
    for ft in features:
        print("  %-14s %-13s %4d pts" % (ft["properties"]["name"], ft["geometry"]["type"], count(ft["geometry"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
