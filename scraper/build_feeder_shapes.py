"""
Derive approximate feeder service areas and write them to data/feeder_shapes.json.

Run:  python scraper/build_feeder_shapes.py

These are NOT BEL's feeder boundaries. BEL does not publish those, and a feeder
boundary is electrical anyway: set by switchgear positions and load balancing,
and reconfigurable. Two houses on one street can sit on different feeders.

What this does instead is take what BEL actually names in a notice and mark out
the ground it covers, using the real shape of each named thing:

  a village or town  ->  its OpenStreetMap boundary, where one exists
  a street           ->  that street's real line, nearest the load centre
  anything else      ->  its point

Each is buffered by a plausible service distance and the results are unioned,
so the shape follows the settlements and roads BEL listed. The previous version
drew a convex hull around a scatter of points, which both missed the real
footprint and filled in ground between named places that the feeder may not
serve at all.

Requires shapely, a build-time dependency only. Nothing at runtime needs it.
"""

import io
import json
import math
import os
import re
import sys
from collections import defaultdict

from shapely.geometry import Point, LineString, Polygon, mapping
from shapely.ops import unary_union

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(HERE, "cache")

# Service distances in kilometres, added around each named thing. A feeder that
# runs down a street serves the buildings either side of it, not a bare line.
BUF_SETTLEMENT = 0.15
BUF_STREET = 0.25
BUF_POINT = 0.80
BUF_LANDMARK = 0.50

SIMPLIFY_KM = 0.12          # about 120m, well under a pixel at usable zooms

# Shapely's default buffer draws 32 points per circle. At the scale this map is
# ever drawn, 12 is indistinguishable and the file is a third of the size.
QUAD = 3
OUTLIER_KM = 45.0           # past this it is a geocoding collision, not a feeder

LAT0 = 17.15                # middle of Belize, for the local projection
KX = 111.32 * math.cos(math.radians(LAT0))
KY = 110.57


def to_km(lon, lat):
    return (lon * KX, lat * KY)


def to_deg(x, y):
    return (round(x / KX, 5), round(y / KY, 5))


def proj_ring(ring):
    return [to_km(c[0], c[1]) for c in ring]


def unproj(geom):
    """Shapely geometry in km back to lon/lat GeoJSON."""
    def ring(r):
        return [list(to_deg(x, y)) for x, y in r]
    gj = mapping(geom)
    if gj["type"] == "Polygon":
        return {"type": "Polygon",
                "coordinates": [ring(r) for r in gj["coordinates"]]}
    if gj["type"] == "MultiPolygon":
        return {"type": "MultiPolygon",
                "coordinates": [[ring(r) for r in p] for p in gj["coordinates"]]}
    return None


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def km_between(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def load(name, default=None):
    p = os.path.join(DATA, name)
    if not os.path.exists(p):
        return default
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


STRIP = re.compile(r"^(portion of|part of|all areas along|areas along|"
                   r"sections of|all areas north of)\s+", re.I)


def main():
    areas = load("areas.json")["areas"]
    fdoc = load("feeders.json")
    places = (load("places.json", {"places": []}) or {"places": []})["places"]

    street_ways = defaultdict(list)
    sp = os.path.join(CACHE, "bz-named-streets.json")
    if os.path.exists(sp):
        with io.open(sp, encoding="utf-8") as f:
            for e in json.load(f).get("elements", []):
                g = e.get("geometry")
                if g and e.get("tags", {}).get("name"):
                    street_ways[norm(e["tags"]["name"])].append(
                        [(p["lon"], p["lat"]) for p in g])

    place_polys = defaultdict(list)
    for p in places:
        place_polys[norm(p["n"])].append(p["r"])

    # Streets BEL names that GeoNames does not carry still have a position in
    # the OpenStreetMap street index. Used as a last resort before giving up,
    # which is what turns a downtown notice like Belize City Feeder 1 into a
    # shape instead of a single load-centre dot.
    street_pts = defaultdict(list)
    sjp = os.path.join(DATA, "streets.json")
    if os.path.exists(sjp):
        with io.open(sjp, encoding="utf-8") as f:
            sd = json.load(f)
        scale = sd["meta"].get("scale", 1000)
        for lc_name, rows in sd.get("by_load_centre", {}).items():
            cx, cy = sd["centres"].get(lc_name, [0, 0])
            for row in rows:
                street_pts[norm(row[0])].append((cx + row[1] / scale, cy + row[2] / scale))
        for row in sd.get("unanchored", []):
            street_pts[norm(row[0])].append((row[1], row[2]))

    by_phrase = {}
    for a in areas:
        for ph in a.get("source_phrases", []):
            by_phrase.setdefault(ph, a)

    anchors = {}
    for a in areas:
        if a.get("is_load_center") and a.get("lat") is not None:
            anchors[norm(a["name"])] = to_km(a["lon"], a["lat"])

    groups = list(fdoc["feeders"]) + list(fdoc.get("unassigned_groups", []))
    features, stats = [], defaultdict(int)

    for g in groups:
        anchor, lck = None, norm(g.get("load_center"))
        for k, v in anchors.items():
            if k.startswith(lck) or lck.startswith(k):
                anchor = v
                break

        parts, kinds, used, rejected = [], defaultdict(int), [], []

        for phrase in g.get("areas", []):
            rec = by_phrase.get(phrase)
            name = rec["name"] if rec else phrase
            key = norm(STRIP.sub("", name))
            geom, kind = None, None

            for rings in place_polys.get(key, []):
                try:
                    poly = Polygon(proj_ring(rings[0]))
                except Exception:
                    continue
                if not poly.is_valid:
                    poly = poly.buffer(0)
                d = km_between(poly.centroid.coords[0], anchor) if anchor else 0
                if anchor is None or d <= OUTLIER_KM:
                    if geom is None or d < geom[1]:
                        geom = (poly.buffer(BUF_SETTLEMENT, quad_segs=QUAD), d)
            if geom is not None:
                geom, kind = geom[0], "settlement"

            if geom is None and key in street_ways:
                lines = []
                for pts in street_ways[key]:
                    ln = LineString(proj_ring(pts))
                    d = km_between(ln.centroid.coords[0], anchor) if anchor else 0
                    if anchor is None or d <= OUTLIER_KM:
                        lines.append(ln)
                if lines:
                    geom = unary_union(lines).buffer(BUF_STREET, quad_segs=QUAD)
                    kind = "street"

            if geom is None and key in street_pts:
                best, bd = None, 1e9
                for lon, lat in street_pts[key]:
                    ptk = to_km(lon, lat)
                    d = km_between(ptk, anchor) if anchor else 0
                    if d < bd:
                        best, bd = ptk, d
                if best is not None and (anchor is None or bd <= OUTLIER_KM):
                    geom = Point(best).buffer(BUF_STREET, quad_segs=QUAD)
                    kind = "street"

            if geom is None and rec and rec.get("lat") is not None:
                pt = to_km(rec["lon"], rec["lat"])
                if anchor is None or km_between(pt, anchor) <= OUTLIER_KM:
                    r = BUF_LANDMARK if rec["type"] == "landmark" else BUF_POINT
                    geom, kind = Point(pt).buffer(r, quad_segs=QUAD), "point"
                else:
                    rejected.append((rec["name"], round(km_between(pt, anchor), 1)))

            if geom is not None:
                parts.append(geom)
                kinds[kind] += 1
                used.append(name)

        # A feeder whose named areas all failed to resolve still exists and must
        # stay on the map, or the legend quietly pretends it is not there. Mark
        # it at its load centre and say that is all this is.
        only_lc = False
        if not parts and anchor is not None:
            parts = [Point(anchor).buffer(BUF_POINT, quad_segs=QUAD)]
            only_lc = True
        if not parts:
            continue

        shape = unary_union(parts).simplify(SIMPLIFY_KM, preserve_topology=True)
        gj = unproj(shape)
        if gj is None:
            continue

        # A point guaranteed to lie inside the shape, so the map label never
        # floats outside a crescent-shaped or multi-part feeder the way a
        # centroid can.
        try:
            rp = shape.representative_point()
            label_pt = list(to_deg(rp.x, rp.y))
        except Exception:
            c = shape.centroid
            label_pt = list(to_deg(c.x, c.y))

        conf = ("load centre only, no area resolved" if only_lc
                else "indicative" if kinds["settlement"] + kinds["street"] >= 3
                else "sparse" if len(parts) >= 3
                else "thin, few named places")

        features.append({
            "type": "Feature",
            "id": g["id"],
            "geometry": gj,
            "properties": {
                "feeder_id": g["id"],
                "load_center": g.get("load_center"),
                "feeder": g.get("feeder"),
                "district": g.get("district"),
                "point_count": 0 if only_lc else len(parts),
                "load_centre_only": 1 if only_lc else 0,
                "named_areas_total": len(g.get("areas", [])),
                "from_settlement_outlines": kinds["settlement"],
                "from_street_geometry": kinds["street"],
                "from_points_only": kinds["point"],
                "area_km2": round(shape.area, 1),
                "label_point": label_pt,
                "confidence": conf,
                "places": sorted(set(used)),
                "source_confidence": g.get("confidence"),
                "areas_list_truncated": g.get("truncated"),
                "rejected_outliers": [{"name": n, "km_from_load_centre": d}
                                      for n, d in rejected],
            },
        })
        stats["settlement"] += kinds["settlement"]
        stats["street"] += kinds["street"]
        stats["point"] += kinds["point"]

    out = {
        "type": "FeatureCollection",
        "meta": {
            "schema_version": 2,
            "built_by": "scraper/build_feeder_shapes.py",
            "method": (
                "Each area BEL names is resolved to its real geometry where one "
                "exists (settlement boundary, street line) or to a point, buffered "
                "by a service distance, and unioned. Settlement %gkm, street %gkm, "
                "point %gkm, landmark %gkm."
                % (BUF_SETTLEMENT, BUF_STREET, BUF_POINT, BUF_LANDMARK)
            ),
            "warning": (
                "Approximate. BEL does not publish feeder boundaries, and a feeder "
                "boundary is electrical, not geographic. A notice also lists the "
                "areas affected that day, not everything on the feeder, so the real "
                "service area is larger than what is drawn here."
            ),
            "feature_count": len(features),
        },
        "features": features,
    }

    path = os.path.join(DATA, "feeder_shapes.json")
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"), ensure_ascii=False)
        f.write("\n")

    print("features                       : %d" % len(features))
    print("built from settlement outlines : %d" % stats["settlement"])
    print("built from street geometry     : %d" % stats["street"])
    print("fell back to a point           : %d" % stats["point"])
    print("file size                      : %.1f KB" % (os.path.getsize(path) / 1024))
    print()
    print("%-28s %5s %8s  %s" % ("feeder", "parts", "km2", "built from"))
    for ft in sorted(features, key=lambda f: -f["properties"]["area_km2"]):
        p = ft["properties"]
        print("%-28s %5d %8.1f  %d outline, %d street, %d point"
              % (p["feeder_id"], p["point_count"], p["area_km2"],
                 p["from_settlement_outlines"], p["from_street_geometry"],
                 p["from_points_only"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
