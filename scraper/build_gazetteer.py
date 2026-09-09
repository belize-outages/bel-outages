"""
Build data/areas.json by geocoding the area names in data/feeders.json
against the GeoNames Belize gazetteer.

Run:  python scraper/build_gazetteer.py

Downloads GeoNames BZ.zip on first run and caches it in scraper/cache/.
GeoNames data is CC BY 4.0. Attribution belongs in the README and the site footer.

The important detail here is district-aware disambiguation. Belize has a
San Antonio in Toledo, Orange Walk, Cayo and Corozal, and a Santa Elena in
both Cayo and Corozal. Matching on name alone puts the wrong village on the
map, so every candidate is scored against the district of the feeder that
mentioned it.
"""

import io
import json
import os
import re
import sys
import unicodedata
import urllib.request
import zipfile
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(HERE, "cache")

GEONAMES_URL = "https://download.geonames.org/export/dump/BZ.zip"

# GeoNames admin1 code -> Belize district name
ADMIN1 = {
    "01": "Belize",
    "02": "Cayo",
    "03": "Corozal",
    "04": "Orange Walk",
    "05": "Stann Creek",
    "06": "Toledo",
}

# Feature codes worth keeping. P = populated places, plus a few locality and
# admin types that show up in outage notices.
KEEP_CODES = {
    "PPL", "PPLA", "PPLA2", "PPLA3", "PPLC", "PPLL", "PPLX", "PPLF",
    "LCTY", "AREA", "RGN", "ISL", "ISLS", "PRK", "AIRP", "AIRF",
}

# Words that mean "this is a road, not a settlement"
STREET_WORDS = re.compile(
    r"\b(street|st\.|road|rd\.|drive|lane|avenue|ave\.|boulevard|blvd|highway|"
    r"hwy|alley|canal|extension|walk|way)\b",
    re.I,
)
SEGMENT_WORDS = re.compile(r"\b(from|to|between|mile)\b", re.I)
LANDMARK_WORDS = re.compile(
    r"\b(layout|subdivision|development|site|heights|estate|zone|area|"
    r"community|substation|airstrip|centre|center|market)\b",
    re.I,
)

# Noise stripped before matching a whole string
QUALIFIERS = re.compile(
    r"^(the\s+|entire\s+|all\s+areas?\s+(along|north\s+of|from)?\s*|"
    r"portions?\s+of\s+|parts?\s+of\s+|sections?\s+of\s+|areas?\s+(along|within|from)\s+|"
    r"a\s+portion\s+of\s+)+",
    re.I,
)
TRAILING = re.compile(
    r"\s+(village|town|city|district|area|areas|and\s+surrounding\s+areas)$", re.I
)


def norm(s):
    """Casefold, strip accents and punctuation, collapse whitespace."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def fetch_geonames():
    os.makedirs(CACHE, exist_ok=True)
    cached = os.path.join(CACHE, "BZ.txt")
    if os.path.exists(cached):
        with io.open(cached, encoding="utf-8") as f:
            return f.read()
    req = urllib.request.Request(GEONAMES_URL, headers={"User-Agent": "bel-outages/0.1"})
    raw = urllib.request.urlopen(req, timeout=120).read()
    txt = zipfile.ZipFile(io.BytesIO(raw)).read("BZ.txt").decode("utf-8")
    with io.open(cached, "w", encoding="utf-8") as f:
        f.write(txt)
    return txt


def load_places(txt):
    places = []
    for line in txt.rstrip("\n").split("\n"):
        c = line.split("\t")
        if len(c) < 19 or c[7] not in KEEP_CODES:
            continue
        alts = [a for a in c[3].split(",") if a.strip()]
        places.append(
            {
                "geonameid": c[0],
                "name": c[1],
                "aliases": alts,
                "lat": float(c[4]),
                "lon": float(c[5]),
                "fcode": c[7],
                "district": ADMIN1.get(c[10]),
                "population": int(c[14] or 0),
            }
        )
    return places


def build_index(places):
    """normalized name -> list of place records (a name can repeat by district)."""
    idx = defaultdict(list)
    for p in places:
        for n in [p["name"]] + p["aliases"]:
            k = norm(n)
            if len(k) >= 3:
                idx[k].append(p)
    return idx


def classify(raw):
    if SEGMENT_WORDS.search(raw) and STREET_WORDS.search(raw):
        return "street_segment"
    if STREET_WORDS.search(raw):
        return "street"
    if LANDMARK_WORDS.search(raw):
        return "landmark"
    return None


def pick(candidates, district):
    """Prefer a candidate in the feeder's district, then the most populous."""
    if not candidates:
        return None, None
    same = [c for c in candidates if c["district"] == district]
    pool = same or candidates
    best = sorted(pool, key=lambda c: (-c["population"], c["fcode"] != "PPL"))[0]
    if same and len(candidates) > 1:
        conf = "district_match"
    elif len(candidates) == 1:
        conf = "unique_name"
    else:
        conf = "ambiguous_district_mismatch"
    return best, conf


def district_of(pt):
    """Which district contains this point, by ray casting on belize.json."""
    p = os.path.join(DATA, "belize.json")
    if not os.path.exists(p):
        return None
    global _districts
    if _districts is None:
        with io.open(p, encoding="utf-8") as f:
            _districts = json.load(f)["features"]
    x, y = pt
    for feat in _districts:
        g = feat["geometry"]
        polys = ([g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"])
        for poly in polys:
            ring = poly[0]
            inside = False
            j = len(ring) - 1
            for i in range(len(ring)):
                xi, yi = ring[i]
                xj, yj = ring[j]
                if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                    inside = not inside
                j = i
            if inside:
                return feat["properties"]["name"]
    return None


_districts = None


def main():
    txt = fetch_geonames()
    places = load_places(txt)
    idx = build_index(places)
    # Longest names first so "Santa Cruz" wins over "Santa" inside a phrase.
    names_by_len = sorted(idx.keys(), key=len, reverse=True)

    with io.open(os.path.join(DATA, "feeders.json"), encoding="utf-8") as f:
        feeders_doc = json.load(f)

    groups = list(feeders_doc["feeders"]) + list(feeders_doc.get("unassigned_groups", []))

    areas = {}          # area_id -> record
    unmatched = []      # (raw, feeder_id, reason)

    for grp in groups:
        fid = grp["id"]
        district = grp.get("district")
        for raw in grp.get("areas", []):
            # "entire Toledo District except Monkey River, Bella Vista, ..."
            # Everything after "except" is explicitly NOT affected. Reading it
            # as a served area tells someone their power is off when BEL said
            # it is on, which is the worst error this file can make.
            raw = re.split(r"\bexcept\b", raw, flags=re.I)[0].strip()
            if not raw:
                continue
            cleaned = TRAILING.sub("", QUALIFIERS.sub("", raw)).strip()
            key = norm(cleaned)

            hit, conf = pick(idx.get(key, []), district)
            matched_on = cleaned

            if not hit:
                # Scan the phrase for any known place name as a whole-word run.
                #
                # Reject a candidate that is immediately followed by a street word.
                # Orange Walk Town has a "Dangriga Street"; without this guard it
                # geocodes to the town of Dangriga in the next district but one.
                for cand in names_by_len:
                    m = re.search(
                        r"(?<![a-z0-9])" + re.escape(cand) + r"(?![a-z0-9])", key
                    )
                    if not m:
                        continue
                    tail = key[m.end():].lstrip()
                    if STREET_WORDS.match(tail):
                        continue
                    hit, conf = pick(idx[cand], district)
                    matched_on = cand
                    conf = (conf or "") + "_substring"
                    break

            if not hit:
                unmatched.append((raw, fid, classify(raw) or "no_geonames_entry"))
                continue

            # An ambiguous match means the only candidate sits in a different
            # district from the feeder that named it. Keep the record so search
            # still finds it, but withhold coordinates: plotting a village in
            # the wrong district is worse than plotting nothing.
            plot = not conf.startswith("ambiguous")

            aid = "gn-" + hit["geonameid"]
            rec = areas.setdefault(
                aid,
                {
                    "id": aid,
                    "name": hit["name"],
                    "aliases": sorted(set(hit["aliases"]))[:8],
                    "district": hit["district"],
                    "type": "town" if hit["population"] >= 2000 else "village",
                    "lat": hit["lat"] if plot else None,
                    "lon": hit["lon"] if plot else None,
                    "feeders_seen": [],
                    "source_phrases": [],
                    "match": conf,
                },
            )
            if fid not in rec["feeders_seen"]:
                rec["feeders_seen"].append(fid)
            if raw not in rec["source_phrases"]:
                rec["source_phrases"].append(raw)
            if matched_on.lower() != rec["name"].lower():
                al = rec["aliases"]
                if matched_on not in al:
                    al.append(matched_on)

    # Every load centre must be searchable by its own name.
    #
    # BEL's notices name Belmopan's neighbourhoods (Market Area, Site 7,
    # Mountain View) but never "Belmopan", so the capital was absent from the
    # gazetteer and a search for it fuzzy-matched "Maya Mopan", a village in
    # another district. Wrong answers are worse than no answer here, so each
    # load centre is added explicitly and wired to its own feeders.
    for lc in feeders_doc["load_centers"]:
        fids = [
            g["id"] for g in groups
            if norm(g.get("load_center") or "") == norm(lc["name"])
        ]
        hit, _ = pick(idx.get(norm(lc["name"]), []), lc["district"])
        if not hit and lc["name"] == "Belize City":
            hit, _ = pick(idx.get("belize city", []), "Belize")
        if not hit:
            print("  note: no coordinates found for load centre", lc["name"])
            continue
        aid = "gn-" + hit["geonameid"]
        rec = areas.setdefault(
            aid,
            {
                "id": aid,
                "name": hit["name"],
                "aliases": sorted(set(hit["aliases"]))[:8],
                "district": hit["district"],
                "type": "town",
                "lat": hit["lat"],
                "lon": hit["lon"],
                "feeders_seen": [],
                "source_phrases": [],
                "match": "load_centre",
            },
        )
        rec["type"] = "town"
        rec["is_load_center"] = True
        for fid in fids:
            if fid not in rec["feeders_seen"]:
                rec["feeders_seen"].append(fid)

    # Every settlement OpenStreetMap outlines is searchable, whether or not BEL
    # has ever named it. Without this the map drew Camalote and Trial Farm but
    # a search for either fell through to a road of a similar name, and any
    # notice naming the village could not reach the person looking for it.
    pp = os.path.join(DATA, "places.json")
    if os.path.exists(pp):
        with io.open(pp, encoding="utf-8") as f:
            known = {norm(a["name"]) for a in areas.values()}
            for pl in json.load(f)["places"]:
                if pl["k"] in ("suburb", "neighbourhood"):
                    continue
                if norm(pl["n"]) in known:
                    continue
                known.add(norm(pl["n"]))
                aid = "osm-" + re.sub(r"[^a-z0-9]+", "-", norm(pl["n"]))[:48]
                areas[aid] = {
                    "id": aid,
                    "name": pl["n"],
                    "aliases": [],
                    "district": district_of(pl["c"]),
                    "type": "town" if pl["k"] in ("city", "town") else "village",
                    "lat": pl["c"][1],
                    "lon": pl["c"][0],
                    "feeders_seen": [],
                    "source_phrases": [],
                    "match": "osm_settlement",
                }

    # Unmatched phrases still belong in the gazetteer, without coordinates.
    for raw, fid, reason in unmatched:
        aid = "txt-" + re.sub(r"[^a-z0-9]+", "-", norm(raw)).strip("-")[:60]
        rec = areas.setdefault(
            aid,
            {
                "id": aid,
                "name": raw,
                "aliases": [],
                "district": next(
                    (g.get("district") for g in groups if g["id"] == fid), None
                ),
                "type": classify(raw) or "landmark",
                "lat": None,
                "lon": None,
                "feeders_seen": [],
                "source_phrases": [raw],
                "match": "ungeocoded:" + reason,
            },
        )
        if fid not in rec["feeders_seen"]:
            rec["feeders_seen"].append(fid)

    out = {
        "meta": {
            "schema_version": 1,
            "built_by": "scraper/build_gazetteer.py",
            "coordinate_source": "GeoNames (CC BY 4.0), https://download.geonames.org/export/dump/BZ.zip",
            "note": (
                "Coordinates are point locations for settlements, taken from GeoNames. "
                "They are not BEL infrastructure locations. Entries with lat/lon null are "
                "streets, corridors, subdivisions and mile-markers that GeoNames does not "
                "carry; they stay in the file because the search box must still find them."
            ),
            "disambiguation": (
                "Where a name exists in more than one district, the candidate in the "
                "feeder's own district wins. Records marked ambiguous_district_mismatch "
                "had no candidate in the right district and need review."
            ),
        },
        "areas": sorted(areas.values(), key=lambda r: (r["district"] or "", r["name"])),
    }

    with io.open(os.path.join(DATA, "areas.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
        f.write("\n")

    geocoded = [a for a in out["areas"] if a["lat"] is not None]
    print("areas total      :", len(out["areas"]))
    print("geocoded         :", len(geocoded))
    print("ungeocoded       :", len(out["areas"]) - len(geocoded))
    byconf = defaultdict(int)
    for a in out["areas"]:
        byconf[a["match"].split(":")[0]] += 1
    for k, v in sorted(byconf.items(), key=lambda kv: -kv[1]):
        print("  %-32s %d" % (k, v))
    amb = [a for a in out["areas"] if a["match"].startswith("ambiguous")]
    if amb:
        print("\nNeeds review (no candidate in the feeder's district):")
        for a in amb:
            print("  %-28s %-12s %s" % (a["name"], a["district"], a["feeders_seen"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
