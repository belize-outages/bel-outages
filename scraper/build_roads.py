"""
Build data/roads.json, Belize's highway network for the map.

Run:  python scraper/build_roads.py

Why roads matter here specifically: BEL writes its outage areas in terms of
highways. "All areas from Mile 23 to Mile 38 on Hummingbird Highway" is
meaningless on a map with no Hummingbird Highway on it.

Scope is trunk and primary only. Secondary and tertiary roads exist in
OpenStreetMap for Belize (1,478 ways, 28,232 points) and are left out because
they would triple the payload for detail nobody needs to answer "is my power
off". The named highways are the ones BEL actually references.

Contiguous ways sharing a name are stitched into one polyline before
simplifying. Douglas-Peucker over a whole highway removes far more points than
running it over 140 separate fragments of the same road.

Source: OpenStreetMap via Overpass, ODbL. Fetched at build time and committed.
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
way["highway"~"^(motorway|trunk|primary)$"](area.bz);
out geom;
"""

TOLERANCE = 0.002       # degrees, about 220m
PRECISION = 4           # about 11m
MIN_PTS = 2

# Highways BEL names in its notices. These get a label on the map.
MAJOR = {
    "Philip Goldson Highway",
    "George Price Highway",
    "Hummingbird Highway",
    "Thomas Vincent Ramos Highway",   # the Southern Highway
    "Coastal Plain Highway",
}


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


def stitch(segments):
    """Join way fragments that share endpoints into continuous polylines."""
    pool = [list(s) for s in segments if len(s) >= 2]
    lines = []
    while pool:
        cur = pool.pop(0)
        joined = True
        while joined:
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
        lines.append(cur)
    return lines


def length_km(pts):
    tot = 0.0
    for i in range(len(pts) - 1):
        dx = (pts[i + 1][0] - pts[i][0]) * 111.32 * math.cos(math.radians(pts[i][1]))
        dy = (pts[i + 1][1] - pts[i][1]) * 110.57
        tot += math.hypot(dx, dy)
    return tot


def fetch():
    os.makedirs(CACHE, exist_ok=True)
    cached = os.path.join(CACHE, "bz-highways.json")
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
        except Exception as e:
            last = e
            wait = 25 * (attempt + 1)
            print("  overpass busy (%s), retrying in %ds" % (type(e).__name__, wait))
            time.sleep(wait)
    raise last


def main():
    j = fetch()

    groups = {}
    raw_pts = 0
    for e in j.get("elements", []):
        g = e.get("geometry")
        if not g:
            continue
        raw_pts += len(g)
        t = e.get("tags", {})
        key = (t.get("name") or "", t.get("highway"))
        groups.setdefault(key, []).append(
            [(round(p["lon"], 7), round(p["lat"], 7)) for p in g]
        )

    roads = []
    for (name, cls), segs in groups.items():
        for line in stitch(segs):
            pts = simplify(line, TOLERANCE)
            if len(pts) < MIN_PTS:
                continue
            km = length_km(pts)
            if km < 0.4 and not name:
                continue          # unnamed stub, not worth the bytes
            roads.append(
                {
                    "n": name or None,
                    "c": "trunk" if cls in ("motorway", "trunk") else "primary",
                    "major": 1 if name in MAJOR else 0,
                    "km": round(km, 1),
                    "g": [[round(x, PRECISION), round(y, PRECISION)] for x, y in pts],
                }
            )

    roads.sort(key=lambda r: (-r["major"], -r["km"]))

    out = {
        "meta": {
            "schema_version": 1,
            "built_by": "scraper/build_roads.py",
            "source": "OpenStreetMap via Overpass, ODbL",
            "scope": "trunk and primary only; secondary and tertiary omitted for size",
            "simplified": "Douglas-Peucker %s deg (~220m), coords at %d dp"
            % (TOLERANCE, PRECISION),
            "count": len(roads),
        },
        "roads": roads,
    }

    path = os.path.join(DATA, "roads.json")
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"), ensure_ascii=False)
        f.write("\n")

    kept = sum(len(r["g"]) for r in roads)
    print("ways in            : %d (%d points)" % (len(j.get("elements", [])), raw_pts))
    print("polylines out      : %d (%d points, %.0f%% removed)"
          % (len(roads), kept, 100 * (1 - kept / raw_pts)))
    print("file size          : %.1f KB" % (os.path.getsize(path) / 1024))
    print()
    print("Named highways, longest first:")
    seen = set()
    for r in roads:
        if r["n"] and r["n"] not in seen and len(seen) < 12:
            seen.add(r["n"])
            total = sum(x["km"] for x in roads if x["n"] == r["n"])
            print("  %-34s %6.1f km  %s"
                  % (r["n"], total, "labelled" if r["major"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
