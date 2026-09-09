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

import hashlib
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")


def load(name):
    with io.open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def check_js(path):
    """Flag any quoted string left open at the end of a line.

    A JavaScript string in single or double quotes cannot contain a raw
    newline, so an unterminated one is always a bug. It is also the bug that
    broke app.js three times in a row, each time from an apostrophe in a word
    like "BEL's" inside a single-quoted string, and each time it hid behind a
    cached copy in the browser before anyone noticed. Backticks are skipped
    because a template literal may legitimately span lines.
    """
    with io.open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")

    bad = []
    quote = None
    tick = False
    bcomment = False

    # A slash starts a regex rather than a division when what came before it
    # cannot end an expression. Without this, /[&<>"]/ reads as the start of a
    # string and the whole rest of the line looks unterminated.
    REGEX_AFTER = set("(,=:[!&|?{};+-*%~^<>") | {""}

    for no, text in enumerate(lines, 1):
        i, n = 0, len(text)
        prev = ""
        while i < n:
            c = text[i]
            nxt = text[i + 1] if i + 1 < n else ""
            if bcomment:
                if c == "*" and nxt == "/":
                    bcomment = False
                    i += 1
            elif quote:
                if c == "\\":
                    i += 1
                elif c == quote:
                    quote = None
            elif tick:
                if c == "\\":
                    i += 1
                elif c == "`":
                    tick = False
            elif c == "/" and nxt == "/":
                break                       # rest of the line is a comment
            elif c == "/" and nxt == "*":
                bcomment = True
                i += 1
            elif c == "/" and (prev in REGEX_AFTER
                               or text[:i].rstrip().endswith("return")):
                i += 1                      # skip the regex body
                while i < n:
                    if text[i] == "\\":
                        i += 1
                    elif text[i] == "[":
                        while i < n and text[i] != "]":
                            i += 1 if text[i] != "\\" else 2
                    elif text[i] == "/":
                        break
                    i += 1
            elif c == "'" or c == '"':
                quote = c
            elif c == "`":
                tick = True
            if not c.isspace():
                prev = c
            i += 1

        if quote:                           # a string cannot cross a newline
            bad.append((no, text.strip()[:70]))
            quote = None
    return bad


def stamp_index(version):
    """Point index.html at the versioned assets."""
    p = os.path.join(ROOT, "index.html")
    if not os.path.exists(p):
        return
    with io.open(p, encoding="utf-8") as f:
        html = f.read()
    pat = r'((?:href|src)="(?:style[.]css|app[.]js|data/bundle[.]js))([?]v=[0-9a-f]+)?"'
    out = re.sub(pat,
                 lambda m: m.group(1) + "?v=" + version + '"', html)
    if out != html:
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(out)


def main():
    areas = load("areas.json")
    outages = load("outages.json")
    shapes = load("feeder_shapes.json")
    districts = load("belize.json")
    feeders = load("feeders.json")

    # A street BEL named often has no coordinates in the gazetteer, because
    # GeoNames does not carry streets. The OpenStreetMap street index does, so
    # borrow them: otherwise searching "Kelly Street" finds the record BEL's
    # notices created and reports that it cannot be drawn, while the very same
    # street sits in streets.json with a position.
    street_pt = {}
    sj = os.path.join(DATA, "streets.json")
    if os.path.exists(sj):
        with io.open(sj, encoding="utf-8") as f:
            sd = json.load(f)
        scale = sd["meta"].get("scale", 1000)
        for lc, rows in sd.get("by_load_centre", {}).items():
            c = sd["centres"].get(lc, [0, 0])
            for row in rows:
                street_pt.setdefault(row[0].lower(),
                                     (c[0] + row[1] / scale, c[1] + row[2] / scale))
        for row in sd.get("unanchored", []):
            street_pt.setdefault(row[0].lower(), (row[1], row[2]))

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
        else:
            base = re.sub(r"^(portion of|part of|all areas along|areas along|"
                          r"sections of|all areas north of)\s+", "",
                          a["name"], flags=re.I).strip().lower()
            pt = street_pt.get(a["name"].lower()) or street_pt.get(base)
            if pt:
                rec["x"] = round(pt[0], 4)
                rec["y"] = round(pt[1], 4)
                rec["approx"] = 1
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
                "lp": p.get("label_point"),
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
            "lp": None,
            "g": None,
        }
        for g in feeders["feeders"] + feeders.get("unassigned_groups", [])
        if g["id"] not in shaped
    ]

    # Load shedding: a separate, tentative feed. Kept distinct from the planned
    # outages all the way to the page so it can never be shown as certain.
    ls = []
    lp = os.path.join(DATA, "loadshedding.json")
    if os.path.exists(lp):
        with io.open(lp, encoding="utf-8") as f:
            ldoc = json.load(f)
        ls = ldoc.get("schedules", [])

    bundle = {
        "generated": outages["meta"]["checked_at"],
        "loadshedding": ls,
        "loadshedding_checked": (ldoc["meta"]["checked_at"] if ls else None),
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

    # Cache busting.
    #
    # GitHub Pages serves static files with its own cache headers, so once the
    # hourly job commits a new bundle.js a returning visitor keeps the old one
    # and reads stale outages. The same thing bit development twice: a syntax
    # error in app.js stayed invisible while the browser kept rendering a
    # cached copy. The stamp changes only when the content does.
    stamp = hashlib.sha1(payload.encode("utf-8"))
    for n in ("app.js", "style.css"):
        fp = os.path.join(ROOT, n)
        if os.path.exists(fp):
            with io.open(fp, "rb") as f:
                stamp.update(f.read())
    version = stamp.hexdigest()[:8]

    with io.open(path, "w", encoding="utf-8") as f:
        f.write("window.BELV=" + json.dumps(version) + ";\n")
        f.write("window.BEL=" + payload + ";\n")

    bad = check_js(os.path.join(ROOT, "app.js"))
    if bad:
        print("app.js has an unterminated string, refusing to build:", file=sys.stderr)
        for no, text in bad:
            print("  line %d: %s" % (no, text), file=sys.stderr)
        return 1

    stamp_index(version)

    size = os.path.getsize(path)
    print("bundle.js      : %.1f KB" % (size / 1024))
    print("  districts    : %d" % len(bundle["districts"]))
    print("  feeders      : %d (%d drawable, %d without geometry)"
          % (len(bundle["feeders"]), bundle["counts"]["mapped_feeders"], len(unmapped)))
    print("  areas        : %d (%d geocoded)"
          % (bundle["counts"]["areas"], bundle["counts"]["geocoded"]))
    print("  outages      : %d" % len(bundle["outages"]))
    print("  load shedding: %d (tentative)" % len(bundle["loadshedding"]))
    print("  asset version: %s (cache busting)" % version)

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
