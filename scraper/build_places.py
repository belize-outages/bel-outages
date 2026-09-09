"""
Build data/places.json, the outlines of Belize's cities, towns and villages.

Run:  python scraper/build_places.py

Source: OpenStreetMap via Overpass, ODbL. Fetched once at build time and
committed. Nothing is requested at runtime.

Coverage is partial and that is stated in the output rather than hidden.
OpenStreetMap has a named boundary for roughly 110 settlements in Belize:
3 cities, 8 towns, and a mix of villages, suburbs and neighbourhoods. Belize
has many more villages than that, so anywhere without a polygon keeps the dot
it already had. Drawing an invented circle around a village to make the map
look complete would be worse than an honest dot.

landuse=residential was considered and rejected: 2,237 areas exist but only 35
carry a name, so they cannot be attributed to a settlement.
"""

import io
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(HERE, "cache")

OVERPASS = "https://overpass-api.de/api/interpreter"
QUERY = """
[out:json][timeout:240];
area["ISO3166-1"="BZ"][admin_level=2]->.bz;
(
  way["place"~"^(city|town|village|suburb|hamlet|neighbourhood)$"](area.bz);
  rel["place"~"^(city|town|village|suburb|hamlet|neighbourhood)$"](area.bz);
);
out geom;
"""

TOLERANCE = 0.0006      # degrees, about 65m
PRECISION = 4           # about 11m
MIN_RING = 5


def perp(p, a, b):
    if a == b:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def simplify(pts, tol):
    if len(pts) < 3:
        return pts
    worst, idx = 0.0, 0
    for i in range(1, len(pts) - 1):
        d = perp(pts[i], pts[0], pts[-1])
        if d > worst:
            worst, idx = d, i
    if worst <= tol:
        return [pts[0], pts[-1]]
    return simplify(pts[: idx + 1], tol)[:-1] + simplify(pts[idx:], tol)


def assemble_rings(segments):
    """Stitch OSM multipolygon member ways into closed rings.

    Overpass returns a relation's boundary as separate ways, often only two
    points long. Treating each one as its own polygon produced shapes with
    nonsense areas: Spanish Lookout came out at 761,355 sq km, which is 33
    times the size of Belize. Rings have to be joined end to end first.
    """
    pool = [list(s) for s in segments if len(s) >= 2]
    rings = []
    while pool:
        cur = pool.pop(0)
        joined = True
        while cur[0] != cur[-1] and joined:
            joined = False
            for i, s in enumerate(pool):
                if s[0] == cur[-1]:
                    cur = cur + s[1:]
                elif s[-1] == cur[-1]:
                    cur = cur + s[-2::-1]
                elif s[-1] == cur[0]:
                    cur = s[:-1] + cur
                elif s[0] == cur[0]:
                    cur = s[:0:-1] + cur
                else:
                    continue
                pool.pop(i)
                joined = True
                break
        if cur[0] == cur[-1] and len(cur) >= 4:
            rings.append(cur)
    return rings


def ring_area_km2(ring):
    """Spherical excess, for sanity-checking a settlement's size."""
    if len(ring) < 4:
        return 0.0
    R, s = 6371.0, 0.0
    for i in range(len(ring) - 1):
        x1, y1 = math.radians(ring[i][0]), math.radians(ring[i][1])
        x2, y2 = math.radians(ring[i + 1][0]), math.radians(ring[i + 1][1])
        s += (x2 - x1) * (2 + math.sin(y1) + math.sin(y2))
    return abs(s * R * R / 2)


# Belize's largest settlement is well under this. Anything bigger is a stitching
# failure, not a town, and is dropped rather than drawn.
MAX_PLACE_KM2 = 400.0


def ring_from(pts):
    if len(pts) < 4:
        return None
    out = simplify(pts, TOLERANCE)
    out = [[round(x, PRECISION), round(y, PRECISION)] for x, y in out]
    if len(out) < MIN_RING:
        return None
    if out[0] != out[-1]:
        out.append(out[0])
    return out


def fetch():
    os.makedirs(CACHE, exist_ok=True)
    cached = os.path.join(CACHE, "bz-places.json")
    if os.path.exists(cached):
        with io.open(cached, encoding="utf-8") as f:
            return json.load(f)
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(
                OVERPASS,
                data=urllib.parse.urlencode({"data": QUERY}).encode(),
                headers={"User-Agent": "bel-outages/0.1"},
            )
            raw = urllib.request.urlopen(req, timeout=300).read()
            with io.open(cached, "wb") as f:
                f.write(raw)
            return json.loads(raw)
        except Exception as e:          # Overpass rate-limits with a 429
            last = e
            wait = 20 * (attempt + 1)
            print("  overpass busy (%s), retrying in %ds" % (type(e).__name__, wait))
            time.sleep(wait)
    raise last


def main():
    j = fetch()
    seen, places, dropped = {}, [], []

    for e in j.get("elements", []):
        t = e.get("tags", {})
        name = (t.get("name") or "").strip()
        kind = t.get("place")
        if not name:
            continue

        raw_rings = []
        if e.get("type") == "way" and e.get("geometry"):
            raw_rings = [[(round(g["lon"], 7), round(g["lat"], 7)) for g in e["geometry"]]]
        elif e.get("type") == "relation":
            segs = [
                [(round(g["lon"], 7), round(g["lat"], 7)) for g in m["geometry"]]
                for m in e.get("members", [])
                if m.get("role") in ("outer", "") and m.get("geometry")
            ]
            raw_rings = assemble_rings(segs)

        rings = []
        for rr in raw_rings:
            if rr[0] != rr[-1]:
                continue                       # never closed, so not an area
            a = ring_area_km2(rr)
            if a <= 0 or a > MAX_PLACE_KM2:
                dropped.append((name, kind, round(a, 1)))
                continue
            r = ring_from(rr)
            if r:
                rings.append(r)
        if not rings:
            continue

        key = (name.lower(), kind)
        if key in seen:
            continue
        seen[key] = 1

        flat = [p for r in rings for p in r]
        places.append(
            {
                "n": name,
                "k": kind,
                "c": [
                    round(sum(p[0] for p in flat) / len(flat), 4),
                    round(sum(p[1] for p in flat) / len(flat), 4),
                ],
                "r": rings,
            }
        )

    order = {"city": 0, "town": 1, "village": 2, "hamlet": 3,
             "suburb": 4, "neighbourhood": 5}
    places.sort(key=lambda p: (order.get(p["k"], 9), p["n"]))

    out = {
        "meta": {
            "schema_version": 1,
            "built_by": "scraper/build_places.py",
            "source": "OpenStreetMap via Overpass, ODbL",
            "note": (
                "Settlement outlines as mapped by OpenStreetMap contributors. "
                "Coverage is partial: Belize has far more villages than have a "
                "boundary drawn. Settlements without one keep a point marker "
                "rather than an invented shape."
            ),
            "simplified": "Douglas-Peucker %s deg, coords at %d dp" % (TOLERANCE, PRECISION),
            "count": len(places),
        },
        "places": places,
    }

    path = os.path.join(DATA, "places.json")
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"), ensure_ascii=False)
        f.write("\n")

    from collections import Counter
    pts = sum(len(r) for p in places for r in p["r"])
    print("settlements outlined : %d" % len(places))
    print("vertices             : %d" % pts)
    print("file size            : %.1f KB" % (os.path.getsize(path) / 1024))
    for k, v in Counter(p["k"] for p in places).most_common():
        print("  %-16s %4d" % (k, v))
    if dropped:
        print()
        print("Rings dropped as implausible (> %g sq km, so a stitching failure):"
              % MAX_PLACE_KM2)
        for n, k, a in sorted(dropped, key=lambda d: -d[2])[:10]:
            print("  %-30s %-8s %10.1f sq km" % (n, k, a))
        print("  (%d in total)" % len(dropped))
    biggest = sorted(places, key=lambda p: -sum(ring_area_km2([tuple(c) for c in r]) for r in p["r"]))[:6]
    print()
    print("Largest kept:")
    for p in biggest:
        print("  %-30s %-8s %8.1f sq km" %
              (p["n"], p["k"], sum(ring_area_km2([tuple(c) for c in r]) for r in p["r"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
