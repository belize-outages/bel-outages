"""
Scrape BEL's Power Updates table into data/outages.json.

    python scraper/scrape.py                  # fetch the live page, write data/outages.json
    python scraper/scrape.py --dry-run        # fetch and print, write nothing
    python scraper/scrape.py --fixture FILE   # parse a saved page instead of fetching
    python scraper/scrape.py --self-test      # run the fixture tests, no network

Requires: requests, beautifulsoup4.

Notes that matter:

* The page is a plain ASP.NET GridView. The rows are in the raw HTML. No
  JavaScript rendering, so no browser automation, so this runs free on Actions.

* BEL prints times as Belize local with no timezone marker. Belize is UTC-6 all
  year and has had no daylight saving since 1983, so local time is stored as
  written and tagged America/Belize. Nothing round-trips through UTC.

* "Feeder: ALL" and "Zone: All" are real values. They are not missing data and
  must not be normalised to null.

* Running twice produces no duplicates: records are keyed by a stable hash of
  date, start, load centre, feeder and zone, and merged into the existing file.

* BEL removes rows from the page without comment. A record that disappears
  before its end time is marked cancelled and kept for 24 hours, because a
  notice that silently vanishes reads to a user like a bug in this site.
"""

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
FIXTURES = os.path.join(HERE, "fixtures")

SOURCE_URL = "https://www.bel.com.bz/PowerUpdates/"
TIMEZONE = "America/Belize"
BELIZE_UTC_OFFSET = dt.timedelta(hours=-6)   # fixed, no DST since 1983

# A real fixed-offset zone, so every timestamp we write carries -06:00 and says
# what it means. Shifting a UTC clock and leaving it labelled +00:00 produces a
# stamp that reads as six hours old the moment it is written.
BELIZE_TZ = dt.timezone(BELIZE_UTC_OFFSET, TIMEZONE)

CANCELLED_RETENTION_HOURS = 24

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

def now_belize():
    return dt.datetime.now(BELIZE_TZ)


def parse_date(s):
    """'Tuesday 08 Sep 2026' or 'Thu 27 Aug 2026' -> '2026-09-08'."""
    s = re.sub(r"\s+", " ", s).strip()
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})", s)
    if not m:
        return None
    day, mon, year = int(m.group(1)), m.group(2)[:3].lower(), int(m.group(3))
    if mon not in MONTHS:
        return None
    return "%04d-%02d-%02d" % (year, MONTHS[mon], day)


def parse_time(s):
    """'8:00AM', '2:45 PM', '12:00PM' -> 24h 'HH:MM'."""
    s = re.sub(r"[\s ]+", "", s).upper()
    m = re.match(r"^(\d{1,2}):(\d{2})(AM|PM)$", s)
    if not m:
        m = re.match(r"^(\d{1,2})(AM|PM)$", s)
        if not m:
            return None
        hour, minute, ap = int(m.group(1)), 0, m.group(2)
    else:
        hour, minute, ap = int(m.group(1)), int(m.group(2)), m.group(3)
    if ap == "AM" and hour == 12:
        hour = 0
    elif ap == "PM" and hour != 12:
        hour += 12
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return "%02d:%02d" % (hour, minute)


def _field(text, label):
    """Pull 'Label: value' up to the next label or a full stop."""
    pat = (
        r"%s\s*:?\s*(.+?)(?=\s*(?:Load\s*Cent(?:er|re)|Feeder|Zone|"
        r"Areas?\s+to\s+be\s+affected|Outage\s+type|Purpose\s+of\s+outage)\s*:|\Z)"
        % label
    )
    m = re.search(pat, text, re.I | re.S)
    if not m:
        return None
    val = re.sub(r"\s+", " ", m.group(1)).strip()
    val = val.rstrip(".").strip()
    return val or None


def parse_areas_cell(text):
    """Split BEL's areas cell into its labelled parts.

    Real shape:
      'Load Center: Punta Gorda. Feeder: 2. Zone: All. Areas to be affected:
       San Antonio, ... Outage type: Unscheduled. Purpose of outage: ...'
    """
    text = re.sub(r"\s+", " ", text).strip()
    out = {
        "load_center": _field(text, r"Load\s*Cent(?:er|re)"),
        "feeder": _field(text, r"Feeder"),
        "zone": _field(text, r"Zone"),
        "areas_text": _field(text, r"Areas?\s+to\s+be\s+affected"),
        "outage_type": _field(text, r"Outage\s+type"),
        "purpose": _field(text, r"Purpose\s+of\s+outage"),
    }
    # 'ALL' is a real value. Normalise its casing but never to null.
    for k in ("feeder", "zone"):
        if out[k] and out[k].strip().lower() == "all":
            out[k] = "ALL"
    return out


def normalise_type(raw):
    if not raw:
        return "planned"
    r = raw.strip().lower()
    if r.startswith("unplan") or r.startswith("unsched") or "emergency" in r:
        return "unscheduled"
    return "planned"


def make_id(date, start, load_center, feeder, zone):
    key = "|".join(
        [date or "", start or "", (load_center or "").lower(),
         (feeder or "").lower(), (zone or "").lower()]
    )
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def parse_table(html):
    """Return the raw row dicts from the Power Updates GridView."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="GridView1") or soup.find("table")
    if table is None:
        return []

    rows = []
    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 5:
            continue                      # header row, or a layout table
        district = cells[0].get_text(" ", strip=True)
        date_s = cells[1].get_text(" ", strip=True)
        start_s = cells[2].get_text(" ", strip=True)
        end_s = cells[3].get_text(" ", strip=True)
        areas_raw = cells[4].get_text(" ", strip=True)
        if not (district or areas_raw):
            continue

        # The GridView renders a pager row as a run of page-number links, which
        # has enough cells to look like data. One reached the live file as a
        # record whose every field was null, district "1 2 3 4". A real notice
        # always carries a parseable date, so that is the test.
        if not parse_date(date_s):
            continue
        rows.append(
            {
                "district": district,
                "date_raw": date_s,
                "start_raw": start_s,
                "end_raw": end_s,
                "raw_text": areas_raw,
            }
        )
    return rows


# --------------------------------------------------------------------------
# area matching against the gazetteer
# --------------------------------------------------------------------------

def norm(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def load_gazetteer():
    path = os.path.join(DATA, "areas.json")
    if not os.path.exists(path):
        return {"areas": []}
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def build_area_index(gaz):
    idx = {}
    for a in gaz["areas"]:
        for n in [a["name"]] + a.get("aliases", []):
            k = norm(n)
            if len(k) >= 3:
                idx.setdefault(k, a["id"])
    return idx


def split_areas(text):
    """Chop the prose area list into candidate place names."""
    if not text:
        return []
    # Anything after "except" is stated as NOT affected, so it must never be
    # matched as a served area. "entire Toledo District except Monkey River,
    # Bella Vista, San Isidro, Bladen and Trio Village" names five places that
    # keep their power, and reading them as affected is the worst error here.
    text = re.split(r"\bexcept\b", text, flags=re.I)[0]
    t = re.sub(r"\b(which|that)\s+(includes?|covers?)\b", ",", text, flags=re.I)
    t = re.sub(r"\b(including|includes|and surrounding areas|entire|portion of|"
               r"portions of|all areas|areas)\b", ",", t, flags=re.I)
    parts = [p.strip(" .") for p in re.split(r"[,;]| and ", t)]

    # A bare district name is the scope of the notice, not a place on the feeder.
    districts = {"belize", "cayo", "corozal", "orange walk", "stann creek", "toledo"}
    out = []
    for p in parts:
        if len(p) < 3:
            continue
        k = norm(p)
        k = re.sub(r"\s+(district|district which|which|rural)$", "", k).strip()
        if not k or k in districts:
            continue
        out.append(p)
    return out


def match_areas(text, idx):
    """Return (matched_area_ids, unmatched_phrases)."""
    ids, unknown = [], []
    for phrase in split_areas(text):
        k = norm(phrase)
        aid = idx.get(k)
        if not aid:
            for suffix in (" village", " town", " area", " city"):
                if k.endswith(suffix):
                    aid = idx.get(k[: -len(suffix)])
                    if aid:
                        break
        if aid:
            if aid not in ids:
                ids.append(aid)
        elif phrase not in unknown:
            unknown.append(phrase)
    return ids, unknown


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------

def derive_status(rec, now):
    """announced -> active -> restored, from the clock. cancelled is set on merge."""
    if rec.get("status") == "cancelled":
        return "cancelled"
    try:
        start = dt.datetime.strptime(rec["date"] + " " + rec["start"], "%Y-%m-%d %H:%M")
        end = dt.datetime.strptime(rec["date"] + " " + rec["end"], "%Y-%m-%d %H:%M")
    except (ValueError, KeyError, TypeError):
        return "announced"
    if end <= start:                       # an outage that crosses midnight
        end += dt.timedelta(days=1)
    naive_now = now.replace(tzinfo=None)
    if naive_now < start:
        return "announced"
    if naive_now <= end:
        return "active"
    return "restored"


def build_records(rows, idx, now):
    records, unknown_all = [], []
    stamp = now.replace(microsecond=0).isoformat()
    for r in rows:
        parts = parse_areas_cell(r["raw_text"])
        date = parse_date(r["date_raw"])
        start = parse_time(r["start_raw"])
        end = parse_time(r["end_raw"])
        area_ids, unknown = match_areas(parts["areas_text"], idx)
        unknown_all.extend(unknown)

        rec = {
            "id": make_id(date, start, parts["load_center"], parts["feeder"], parts["zone"]),
            "type": normalise_type(parts["outage_type"]),
            "status": "announced",
            "district": r["district"],
            "load_center": parts["load_center"],
            "feeder": parts["feeder"],
            "zone": parts["zone"],
            "area_ids": area_ids,
            "date": date,
            "start": start,
            "end": end,
            "timezone": TIMEZONE,
            "tentative": False,
            "purpose": parts["purpose"],
            "source_url": SOURCE_URL,
            "published_at": None,
            "ingested_at": stamp,
            "raw_text": r["raw_text"],
        }
        rec["status"] = derive_status(rec, now)
        records.append(rec)
    return records, unknown_all


def read_history():
    path = os.path.join(DATA, "history.json")
    if not os.path.exists(path):
        return {"meta": {}, "notices": []}
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


_history = None


def archive(rec):
    """Append a finished notice to data/history.json, keyed by id.

    This is the corpus. Each notice states a load centre, a feeder, a zone and
    the areas BEL cut power to, which is a labelled observation of what that
    feeder serves. One notice tells you little. A year of them is the only
    honest way to learn which streets sit on which feeder, because BEL does not
    publish that and no amount of map geometry can infer an electrical boundary.
    """
    global _history
    if _history is None:
        _history = read_history()
    seen = {n["id"] for n in _history["notices"]}
    if rec["id"] in seen:
        return
    _history["notices"].append({
        k: rec.get(k) for k in (
            "id", "type", "district", "load_center", "feeder", "zone",
            "area_ids", "date", "start", "end", "purpose", "raw_text",
            "first_seen", "last_seen",
        )
    })


def write_history(now):
    if _history is None:
        return False
    _history["meta"] = {
        "schema_version": 1,
        "what": (
            "Every BEL notice this scraper has seen finish, kept as evidence of "
            "which areas sit on which feeder. Append-only; nothing is removed."
        ),
        "updated": now.replace(microsecond=0).isoformat(),
        "count": len(_history["notices"]),
    }
    _history["notices"].sort(key=lambda n: (n.get("date") or "", n.get("start") or ""))
    path = os.path.join(DATA, "history.json")
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(_history, f, indent=1, ensure_ascii=False)
        f.write("\n")
    return True


def merge(existing, scraped, now):
    """Merge scraped rows over the committed file.

    Anything in the old file that is missing from the page and had not yet
    finished is marked cancelled and kept for 24 hours.
    """
    stamp = now.replace(microsecond=0).isoformat()
    by_id = {r["id"]: r for r in existing}
    seen = set()

    for rec in scraped:
        seen.add(rec["id"])
        prev = by_id.get(rec["id"])
        if prev:
            # Keep the first ingest time; the record is the same notice.
            rec["ingested_at"] = prev.get("ingested_at", rec["ingested_at"])
            rec["first_seen"] = prev.get("first_seen", prev.get("ingested_at"))
        else:
            rec["first_seen"] = stamp
        rec["last_seen"] = stamp
        by_id[rec["id"]] = rec

    for rid, rec in list(by_id.items()):
        if rid in seen:
            continue
        status = derive_status(rec, now)
        if status in ("announced", "active") and rec.get("status") != "cancelled":
            rec["status"] = "cancelled"
            rec["cancelled_at"] = stamp
        elif rec.get("status") != "cancelled":
            rec["status"] = status

        if rec.get("status") == "cancelled":
            try:
                c = dt.datetime.fromisoformat(rec.get("cancelled_at", stamp))
                if (now.replace(tzinfo=None) - c.replace(tzinfo=None)) > dt.timedelta(
                    hours=CANCELLED_RETENTION_HOURS
                ):
                    del by_id[rid]
            except ValueError:
                pass
        elif rec.get("status") == "restored":
            # Finished and gone from the page. Nothing left to tell a visitor,
            # but every notice is a labelled example of which areas sit on
            # which feeder, and that is the only route to a real street-level
            # feeder map. Archive it before dropping it from the live file.
            archive(rec)
            del by_id[rid]

    return sorted(
        by_id.values(),
        key=lambda r: (r.get("date") or "", r.get("start") or "", r["id"]),
    )


# --------------------------------------------------------------------------
# io
# --------------------------------------------------------------------------

def fetch(url):
    import requests

    resp = requests.get(
        url,
        timeout=45,
        headers={"User-Agent": "bel-outages/0.1 (+https://github.com/)"},
    )
    resp.raise_for_status()
    return resp.text


def read_outages():
    path = os.path.join(DATA, "outages.json")
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as f:
        doc = json.load(f)
    return doc["outages"] if isinstance(doc, dict) else doc


def write_outages(records, now):
    path = os.path.join(DATA, "outages.json")
    doc = {
        "meta": {
            "schema_version": 1,
            "source_url": SOURCE_URL,
            "timezone": TIMEZONE,
            "checked_at": now.replace(microsecond=0).isoformat(),
            "note": (
                "Times are Belize local exactly as BEL printed them. "
                "'ALL' for feeder or zone is a real value, not missing data."
            ),
        },
        "outages": records,
    }
    new = json.dumps(doc, indent=1, ensure_ascii=False, sort_keys=False)

    # Only rewrite when the outages themselves changed, so the hourly workflow
    # does not commit a new timestamp every run.
    if os.path.exists(path):
        with io.open(path, encoding="utf-8") as f:
            old_doc = json.load(f)
        if old_doc.get("outages") == records:
            return False
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(new + "\n")
    return True


# --------------------------------------------------------------------------
# self test
# --------------------------------------------------------------------------

def self_test():
    failures = []

    def check(label, got, want):
        if got != want:
            failures.append("%s\n     got:  %r\n     want: %r" % (label, got, want))

    check("date full weekday", parse_date("Tuesday 08 Sep 2026"), "2026-09-08")
    check("date short weekday", parse_date("Thu 27 Aug 2026"), "2026-08-27")
    check("date sunday", parse_date("Sunday 30 Aug 2026"), "2026-08-30")
    check("time am", parse_time("8:00AM"), "08:00")
    check("time pm", parse_time("2:45PM"), "14:45")
    check("time noon", parse_time("12:00PM"), "12:00")
    check("time midnight", parse_time("12:00AM"), "00:00")
    check("time spaced", parse_time("4:30 PM"), "16:30")

    cell = (
        "Load Center: Independence. Feeder: ALL. Zone: ALL. Areas to be affected: "
        "entire Placencia Peninsula including Placencia Village and Seine Bight, "
        "Independence, Maya King. Outage type: Planned. Purpose of outage: "
        "BEL to replace utility poles."
    )
    p = parse_areas_cell(cell)
    check("load centre", p["load_center"], "Independence")
    check("feeder ALL kept", p["feeder"], "ALL")
    check("zone ALL kept", p["zone"], "ALL")
    check("outage type", p["outage_type"], "Planned")
    check("purpose", p["purpose"], "BEL to replace utility poles")
    check("areas text starts", p["areas_text"].startswith("entire Placencia"), True)
    check("areas text stops before type", "Outage type" in p["areas_text"], False)

    p2 = parse_areas_cell(
        "Load Center: Dangriga. Feeder: 2. Zone: 3 & 4 (portion). "
        "Areas to be affected: Middlesex, Santa Marta. Outage type: Planned."
    )
    check("numbered feeder", p2["feeder"], "2")
    check("compound zone", p2["zone"], "3 & 4 (portion)")

    # A stamp that says +00:00 while holding Belize local time reads as six
    # hours stale the instant it is written, and the site's freshness line and
    # out-of-date banner both run off it.
    check("timestamp carries -06:00", now_belize().isoformat().endswith("-06:00"), True)
    check("timestamp not tagged UTC", "+00:00" in now_belize().isoformat(), False)

    # BEL sometimes states an area list as an exclusion. Those places keep
    # their power, so they must never come back as affected.
    excl = split_areas(
        "entire Toledo District except Monkey River, Bella Vista, San Isidro, "
        "Bladen and Trio Village"
    )
    check("exclusion list dropped", [p for p in excl if "Monkey" in p], [])
    check("exclusion drops all five", len(excl), 0)
    incl = split_areas("Placencia Village and Seine Bight, Independence")
    check("normal list still splits", len(incl) >= 3, True)

    # A finished notice must survive into the archive. It is the only record
    # of which areas that feeder actually serves.
    global _history
    _history = {"meta": {}, "notices": []}
    done = {"id": "zz1", "date": "2026-01-01", "start": "08:00", "end": "09:00",
            "status": "announced", "load_center": "Dangriga", "feeder": "3",
            "area_ids": ["gn-1"], "raw_text": "x"}
    after = merge([done], [], dt.datetime(2026, 6, 1, 12, 0))
    check("finished notice leaves live file", len(after), 0)
    check("finished notice archived", len(_history["notices"]), 1)
    check("archive keeps the feeder", _history["notices"][0]["feeder"], "3")
    check("archive keeps the areas", _history["notices"][0]["area_ids"], ["gn-1"])
    _history = None

    # A pager row has five cells and no date. It must not become a record.
    pager = (
        '<table id="GridView1"><tr><td>Districts</td><td>Date</td><td>a</td>'
        '<td>b</td><td>c</td></tr>'
        '<tr><td>1</td><td>2</td><td>3</td><td>4</td><td>5</td></tr>'
        '<tr><td>Toledo District</td><td>Sunday 30 Aug 2026</td><td>7:00AM</td>'
        '<td>3:00PM</td><td>Load Center: Punta Gorda. Feeder: ALL. Zone: ALL. '
        'Areas to be affected: entire Toledo District.</td></tr></table>'
    )
    rows = parse_table(pager)
    check("pager row skipped", len(rows), 1)
    if rows:
        check("real row kept", rows[0]["date_raw"], "Sunday 30 Aug 2026")

    check("type planned", normalise_type("Planned"), "planned")
    check("type unscheduled", normalise_type("Unscheduled"), "unscheduled")
    check("type unplanned", normalise_type("Unplanned"), "unscheduled")
    check("type missing defaults planned", normalise_type(None), "planned")

    a = make_id("2026-08-30", "07:00", "Independence", "ALL", "ALL")
    b = make_id("2026-08-30", "07:00", "independence", "all", "all")
    check("id is case stable", a, b)
    c = make_id("2026-08-30", "14:45", "Punta Gorda", "ALL", "ALL")
    check("id differs by start", a != c, True)

    now = dt.datetime(2026, 8, 30, 10, 0)
    rec = {"date": "2026-08-30", "start": "07:00", "end": "15:00"}
    check("status active", derive_status(rec, now), "active")
    check("status announced", derive_status(rec, dt.datetime(2026, 8, 30, 6, 0)), "announced")
    check("status restored", derive_status(rec, dt.datetime(2026, 8, 30, 16, 0)), "restored")

    # Fixtures, and the no-duplicates guarantee.
    fixture = os.path.join(FIXTURES, "powerupdates-2026-08-29.html")
    if os.path.exists(fixture):
        with io.open(fixture, encoding="utf-8") as f:
            html = f.read()
        rows = parse_table(html)
        check("fixture row count", len(rows), 3)
        idx = build_area_index(load_gazetteer())
        recs, _ = build_records(rows, idx, now)
        check("fixture ids unique", len(set(r["id"] for r in recs)), 3)
        check("raw_text never empty", all(r["raw_text"] for r in recs), True)

        placencia = [r for r in recs if "Placencia" in (r["raw_text"] or "")]
        check("placencia row found", len(placencia) >= 1, True)
        if placencia:
            r = placencia[0]
            check("placencia date", r["date"], "2026-08-30")
            check("placencia start", r["start"], "07:00")
            check("placencia end", r["end"], "15:00")
            check("placencia feeder ALL", r["feeder"], "ALL")
            check("placencia has areas", len(r["area_ids"]) > 0, True)

        # Scraping twice must not duplicate.
        once = merge([], recs, now)
        twice = merge(once, build_records(rows, idx, now)[0], now)
        check("twice is same count", len(twice), len(once))
        check("twice has no dup ids", len(set(r["id"] for r in twice)), len(twice))
    else:
        failures.append("fixture missing: %s" % fixture)

    if failures:
        print("SELF TEST FAILED (%d)" % len(failures))
        for f in failures:
            print("  - " + f)
        return 1
    print("self test passed")
    return 0


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Scrape BEL Power Updates.")
    ap.add_argument("--fixture", help="parse a saved HTML file instead of fetching")
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing")
    ap.add_argument("--self-test", action="store_true", help="run tests, no network")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    now = now_belize()
    html = (
        io.open(args.fixture, encoding="utf-8").read()
        if args.fixture
        else fetch(SOURCE_URL)
    )

    rows = parse_table(html)
    if not rows:
        print("no rows found. the page shape may have changed.", file=sys.stderr)
        return 2

    idx = build_area_index(load_gazetteer())
    scraped, unknown = build_records(rows, idx, now)
    merged = merge(read_outages(), scraped, now)

    print("rows on page   : %d" % len(rows))
    print("records merged : %d" % len(merged))
    for r in merged:
        print("  %-12s %s %s-%s  %-14s F%-4s Z%-12s %s"
              % (r["id"], r["date"], r["start"], r["end"],
                 (r["load_center"] or "?")[:14], (r["feeder"] or "?")[:4],
                 (r["zone"] or "?")[:12], r["status"]))
    if unknown:
        print("\nplace names not in the gazetteer (%d):" % len(unknown))
        for u in sorted(set(unknown))[:25]:
            print("  " + u)

    if args.dry_run:
        print("\ndry run, nothing written")
        return 0

    changed = write_outages(merged, now)
    print("\ndata/outages.json %s" % ("updated" if changed else "unchanged"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
