"""
Pack the committed JSON into data/bundle.js, the single payload the page loads.

Run:  python scraper/build_bundle.py

Why a .js file and not fetch() over the JSON:

    Browsers block fetch() against file:// URLs. The brief requires the site to
    work when opened as a local file with no server running, so the data has to
    arrive through a <script> tag. The JSON files stay canonical, the scraper
    keeps writing them, and this step derives the bundle from them.

It also trims. areas.json carries research fields (source phrases, match
provenance) that matter when auditing the gazetteer and are dead weight to a
phone on mobile data during an outage.
"""

import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")


def load(name):
    with io.open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def main():
    areas = load("areas.json")
    outages = load("outages.json")
    shapes = load("feeder_shapes.json")
    districts = load("belize.json")
    feeders = load("feeders.json")

    # Only areas that a user could plausibly type. Keep ungeocoded ones: the
    # search must still answer for a street it cannot draw.
    slim_areas = []
    for a in areas["areas"]:
        rec = {
            "id": a["id"],
            "n": a["name"],
            "d": a["district"],
            "t": a["type"],
            "f": a["feeders_seen"],
        }
        al = [x for x in a.get("aliases", []) if x.lower() != a["name"].lower()]
        if al:
            rec["a"] = al[:4]
        if a["lat"] is not None:
            rec["y"] = round(a["lat"], 4)
            rec["x"] = round(a["lon"], 4)
        if a.get("is_load_center"):
            rec["lc"] = 1
        slim_areas.append(rec)

    # 4 decimal places is about 11 metres. On a map of a whole country drawn at
    # phone size that is far below one pixel, and it trims several KB.
    def round_geom(g):
        if not g:
            return g
        def r(c):
            if isinstance(c[0], (int, float)):
                return [round(c[0], 4), round(c[1], 4)]
            return [r(x) for x in c]
        return {"type": g["type"], "coordinates": r(g["coordinates"])}

    slim_shapes = []
    for f in shapes["features"]:
        p = f["properties"]
        slim_shapes.append(
            {
                "id": p["feeder_id"],
                "lc": p["load_center"],
                "f": p["feeder"],
                "d": p["district"],
                "n": p["point_count"],
                "total": p["named_areas_total"],
                "conf": p["confidence"],
                "trunc": p["areas_list_truncated"],
                "g": round_geom(f["geometry"]),
            }
        )

    # Feeders with no geocoded point at all still need to exist in the legend,
    # otherwise the map silently pretends they are not there.
    shaped = {s["id"] for s in slim_shapes}
    unmapped = [
        {
            "id": g["id"],
            "lc": g.get("load_center"),
            "f": g.get("feeder"),
            "d": g.get("district"),
            "n": 0,
            "total": len(g.get("areas", [])),
            "conf": "no geocoded places",
            "trunc": g.get("truncated"),
            "g": None,
        }
        for g in feeders["feeders"] + feeders.get("unassigned_groups", [])
        if g["id"] not in shaped
    ]

    bundle = {
        "generated": outages["meta"]["checked_at"],
        "source_url": outages["meta"]["source_url"],
        "timezone": outages["meta"]["timezone"],
        "districts": districts["features"],
        "feeders": slim_shapes + unmapped,
        "areas": slim_areas,
        "outages": outages["outages"],
        "counts": {
            "load_centers": len(feeders["load_centers"]),
            "feeders": len(feeders["feeders"]),
            "areas": len(slim_areas),
            "geocoded": sum(1 for a in slim_areas if "y" in a),
            "mapped_feeders": len([s for s in slim_shapes if s["g"]]),
        },
    }

    path = os.path.join(DATA, "bundle.js")
    payload = json.dumps(bundle, separators=(",", ":"), ensure_ascii=False)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write("window.BEL=" + payload + ";\n")

    size = os.path.getsize(path)
    print("bundle.js      : %.1f KB" % (size / 1024))
    print("  districts    : %d" % len(bundle["districts"]))
    print("  feeders      : %d (%d drawable, %d without geometry)"
          % (len(bundle["feeders"]), bundle["counts"]["mapped_feeders"], len(unmapped)))
    print("  areas        : %d (%d geocoded)"
          % (bundle["counts"]["areas"], bundle["counts"]["geocoded"]))
    print("  outages      : %d" % len(bundle["outages"]))

    # The street index is a separate file the page loads only when someone
    # starts searching. Most visits are a glance at the map and never need it.
    spath = os.path.join(DATA, "streets.json")
    if os.path.exists(spath):
        with io.open(spath, encoding="utf-8") as f:
            sdoc = json.load(f)
        jspath = os.path.join(DATA, "streets.js")
        with io.open(jspath, "w", encoding="utf-8") as f:
            f.write("window.BEL_STREETS=" + json.dumps(
                {
                    "scale": sdoc["meta"]["scale"],
                    "centres": sdoc["centres"],
                    "byLc": sdoc["by_load_centre"],
                    "loose": sdoc["unanchored"],
                },
                separators=(",", ":"), ensure_ascii=False) + ";\n")
        print("streets.js     : %.1f KB (%d streets, lazy loaded)"
              % (os.path.getsize(jspath) / 1024, sdoc["meta"]["count"]))

    # Detail layer: settlement outlines plus the highway network. One file,
    # one request, loaded when the map is zoomed in or a place is picked. At
    # the full-country view the outlines are specks not worth the bytes.
    detail, bits = {}, []
    for key, fname in (("places", "places.json"), ("roads", "roads.json")):
        fp = os.path.join(DATA, fname)
        if os.path.exists(fp):
            with io.open(fp, encoding="utf-8") as f:
                d = json.load(f)
            detail[key] = d[key]
            bits.append("%d %s" % (d["meta"]["count"], key))
    if detail:
        jspath = os.path.join(DATA, "detail.js")
        with io.open(jspath, "w", encoding="utf-8") as f:
            f.write("window.BEL_DETAIL=" + json.dumps(
                detail, separators=(",", ":"), ensure_ascii=False) + ";\n")
        print("detail.js      : %.1f KB (%s, lazy loaded)"
              % (os.path.getsize(jspath) / 1024, ", ".join(bits)))

    fuse = os.path.join(ROOT, "vendor", "fuse.min.js")
    total = size + (os.path.getsize(fuse) if os.path.exists(fuse) else 0)
    for n in ("index.html", "style.css", "app.js"):
        p = os.path.join(ROOT, n)
        if os.path.exists(p):
            total += os.path.getsize(p)
    print("page total     : %.1f KB (bundle + fuse + html/css/js)" % (total / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
