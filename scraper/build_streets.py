"""
Build data/streets.json, a street index so people can search by their own road.

Run:  python scraper/build_streets.py

Why this exists:

    The gazetteer built from BEL's notices contains about 40 streets, and every
    one is there only because BEL happened to name it in an outage notice.
    Someone on Sarstoon Street in Belmopan searched for it and got nothing,
    which fails the single question this site is meant to answer.

    OpenStreetMap has around 3,300 named roads in Belize. This pulls them,
    attaches each to the nearest load centre, and lets the site answer at the
    level it honestly can.

What this does NOT do:

    It does not claim to know which feeder a street is on. BEL does not publish
    that, and guessing would be worse than saying nothing. A street resolves to
    its load centre, and the page says plainly that it is showing the town's
    feeders rather than the street's.

Source: OpenStreetMap via Overpass, ODbL. Attribution belongs in the README and
the site footer. Fetched once at build time and committed; nothing at runtime.
"""

import io
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(HERE, "cache")

OVERPASS = "https://overpass-api.de/api/interpreter"
QUERY = """
[out:json][timeout:180];
area["ISO3166-1"="BZ"][admin_level=2]->.bz;
way["highway"]["name"](area.bz);
out tags center;
"""

# Beyond this a street is not plausibly served by that load centre, so it is
# kept for search but carries no feeder link.
MAX_LC_KM = 30.0

# Tracks, paths and driveways are not addresses anyone searches by.
SKIP_HIGHWAY = {"track", "path", "footway", "cycleway", "steps", "bridleway",
                "construction", "proposed", "raceway", "escape", "corridor"}

# Street coordinates are stored as integer offsets from the load centre at
# 1/SCALE degree. 1e-3 degrees is about 110 metres, enough to put the map on
# the right street without paying for precision nobody needs.
SCALE = 1000


def km(a, b):
    dx = (b[0] - a[0]) * 111.32 * math.cos(math.radians((a[1] + b[1]) / 2))
    dy = (b[1] - a[1]) * 110.57
    return math.hypot(dx, dy)


def fetch():
    os.makedirs(CACHE, exist_ok=True)
    cached = os.path.join(CACHE, "bz-roads.json")
    if os.path.exists(cached):
        with io.open(cached, encoding="utf-8") as f:
            return json.load(f)
    req = urllib.request.Request(
        OVERPASS,
        data=urllib.parse.urlencode({"data": QUERY}).encode(),
        headers={"User-Agent": "bel-outages/0.1"},
    )
    raw = urllib.request.urlopen(req, timeout=300).read()
    with io.open(cached, "wb") as f:
        f.write(raw)
    return json.loads(raw)


def main():
    j = fetch()

    with io.open(os.path.join(DATA, "areas.json"), encoding="utf-8") as f:
        areas = json.load(f)["areas"]
    with io.open(os.path.join(DATA, "feeders.json"), encoding="utf-8") as f:
        fdoc = json.load(f)

    load_centres = []
    for a in areas:
        if a.get("is_load_center") and a.get("lat") is not None:
            fids = [
                g["id"] for g in fdoc["feeders"]
                if (g.get("load_center") or "").lower().split(" and ")[0]
                in a["name"].lower()
            ]
            load_centres.append(
                {"name": a["name"], "district": a["district"],
                 "pt": (a["lon"], a["lat"]), "feeders": fids, "id": a["id"]}
            )

    # One entry per distinct street name per load centre. A "Main Street" in
    # Corozal and one in Dangriga are different streets and both must be found.
    best = {}
    for e in j.get("elements", []):
        t = e.get("tags", {})
        name = (t.get("name") or "").strip()
        if not name or t.get("highway") in SKIP_HIGHWAY:
            continue
        if len(name) < 3 or not re.search(r"[A-Za-z]", name):
            continue
        c = e.get("center")
        if not c:
            continue
        pt = (c["lon"], c["lat"])

        near, d = None, 1e9
        for lc in load_centres:
            dd = km(pt, lc["pt"])
            if dd < d:
                near, d = lc, dd
        if near is None:
            continue

        key = (name.lower(), near["name"] if d <= MAX_LC_KM else "")
        prev = best.get(key)
        if prev is None or d < prev["km"]:
            best[key] = {
                "name": name,
                "lc": near["name"] if d <= MAX_LC_KM else None,
                "district": near["district"] if d <= MAX_LC_KM else None,
                "feeders": near["feeders"] if d <= MAX_LC_KM else [],
                "km": round(d, 1),
                "lon": round(pt[0], 5),
                "lat": round(pt[1], 5),
            }

    streets = sorted(best.values(), key=lambda s: (s["lc"] or "~", s["name"]))

    # Grouped by load centre, with coordinates stored as integer offsets from
    # that load centre at 1/SCALE degree, roughly 110 metres.
    #
    # Searching an address should move the map to the address. Absolute
    # coordinates at full precision would nearly double this file for accuracy
    # nobody needs: the job is to put the map on the right street, and every
    # street here is within 30km of its load centre, so the offsets stay small.
    centre_pt = {c["name"]: c["pt"] for c in load_centres}
    centres, packed, unanchored = {}, {}, []

    for s in streets:
        if not s["lc"]:
            unanchored.append([s["name"], round(s["lon"], 4), round(s["lat"], 4)])
            continue
        lc = s["lc"]
        if lc not in centres:
            cx0, cy0 = centre_pt[lc]
            centres[lc] = [round(cx0, 4), round(cy0, 4)]
        cx, cy = centres[lc]
        packed.setdefault(lc, []).append([
            s["name"],
            int(round((s["lon"] - cx) * SCALE)),
            int(round((s["lat"] - cy) * SCALE)),
        ])
    for lc in packed:
        packed[lc].sort(key=lambda r: r[0])

    out = {
        "meta": {
            "schema_version": 2,
            "built_by": "scraper/build_streets.py",
            "source": "OpenStreetMap via Overpass, ODbL",
            "note": (
                "A street is attached to its nearest load centre, not to a feeder. "
                "BEL does not publish which feeder serves which street, so the site "
                "answers at load-centre level and says so."
            ),
            "coordinates": (
                "by_load_centre rows are [name, dlon, dlat], integer offsets from "
                "centres[lc] at 1/%d degree, so lon = centres[lc][0] + dlon/%d. "
                "unanchored rows are [name, lon, lat] absolute, for streets with "
                "no load centre within %g km."
            ) % (SCALE, SCALE, MAX_LC_KM),
            "scale": SCALE,
            "max_load_centre_km": MAX_LC_KM,
            "count": sum(len(v) for v in packed.values()) + len(unanchored),
        },
        "centres": centres,
        "by_load_centre": packed,
        "unanchored": unanchored,
    }
    path = os.path.join(DATA, "streets.json")
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"), ensure_ascii=False)
        f.write("\n")

    print("streets indexed : %d" % out["meta"]["count"])
    print("file size       : %.1f KB" % (os.path.getsize(path) / 1024))
    for k, v in sorted(packed.items(), key=lambda kv: -len(kv[1])):
        print("  %-34s %5d" % (k, len(v)))
    if unanchored:
        print("  %-34s %5d" % ("(no load centre within %gkm)" % MAX_LC_KM,
                               len(unanchored)))

    probe = [s for s in streets if "sarstoon" in s["name"].lower()]
    print()
    print("Sarstoon check:")
    for s in probe:
        print("  %-22s -> %-14s %-12s %5.1f km  %.4f %.4f"
              % (s["name"], s["lc"], s["district"], s["km"], s["lon"], s["lat"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
