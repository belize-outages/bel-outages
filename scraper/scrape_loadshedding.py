"""
Scrape BEL's load-shedding schedules into data/loadshedding.json.

    python scraper/scrape_loadshedding.py             # discover and parse
    python scraper/scrape_loadshedding.py --dry-run   # print, write nothing
    python scraper/scrape_loadshedding.py --self-test # fixture tests, no network

Why this exists, and why it is separate from scrape.py:

    Load shedding is not on BEL's Power Updates page. It is the rolling
    blackouts BEL runs when generation falls short, and through 2026 it has
    been the thing people actually need to check. The site was silent during
    exactly those events, and silence reads as "you are fine".

    BEL announces it on Facebook. Facebook is not scraped here, per the brief
    and for good reason. But Belize news outlets republish the schedule in
    plain HTML within minutes, and those pages are ordinary WordPress articles
    that answer a plain requests.get. That is the route used.

What this data is NOT:

    Load shedding is tentative by BEL's own words: the schedule depends on
    real-time demand and generation and changes at short notice. Every record
    here carries tentative=true and the outlet it came from, and the site must
    present it as "may affect you", never as fact. It is also second-hand, so
    a transcription error at the outlet becomes an error here. raw_text keeps
    what the article actually said.
"""

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scrape import (                                   # noqa: E402
    BELIZE_TZ, TIMEZONE, MONTHS, build_area_index, load_gazetteer,
    match_areas, norm, now_belize, parse_time,
)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
FIXTURES = os.path.join(HERE, "fixtures")

UA = {"User-Agent": "bel-outages/0.1 (+https://github.com/)"}

SOURCES = [
    {"name": "Breaking Belize News",
     "search": "https://www.breakingbelizenews.com/?s=load+shedding",
     "pattern": r"breakingbelizenews\.com/\d{4}/\d{2}/\d{2}/[^\"']*(?:load-shedding|blackout)"},
    {"name": "Love FM",
     "search": "https://lovefm.com/?s=load+shedding",
     "pattern": r"lovefm\.com/[^\"']*load-shedding[^\"']*"},
]

# Only the last few days matter. An old schedule is not a forecast.
MAX_AGE_DAYS = 3
MAX_ARTICLES = 8

LOAD_CENTRES = [
    "Belize City", "Ladyville", "Belmopan", "San Ignacio", "Corozal",
    "Orange Walk", "San Pedro", "Caye Caulker", "Dangriga", "Independence",
    "Punta Gorda", "Westlake",
]

MONTH_NAMES = ("january february march april may june july august september "
               "october november december").split()


def article_date(text, fallback):
    """'... Schedule for Tuesday, September 1, 2026' -> date."""
    m = re.search(r"(%s)\s+(\d{1,2}),?\s+(\d{4})" % "|".join(MONTH_NAMES),
                  text, re.I)
    if m:
        try:
            return dt.date(int(m.group(3)),
                           MONTH_NAMES.index(m.group(1).lower()) + 1,
                           int(m.group(2))).isoformat()
        except ValueError:
            pass
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})", text)
    if m and m.group(2)[:3].lower() in MONTHS:
        return "%04d-%02d-%02d" % (int(m.group(3)),
                                   MONTHS[m.group(2)[:3].lower()],
                                   int(m.group(1)))
    return fallback


def norm_time(s):
    """'6:00 PM', '7:00 p.m.' -> 24h."""
    s = re.sub(r"[\.\s ]", "", s or "").upper()
    s = s.replace("PM", "PM").replace("AM", "AM")
    return parse_time(s)


def find_load_centre(text):
    for lc in LOAD_CENTRES:
        if re.search(r"\b" + re.escape(lc) + r"\b", text, re.I):
            return lc
    return None


TIME_RANGE = (r"(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?))\s*"
              r"(?:to|[-\u2013\u2014])\s*"
              r"(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?))")


def parse_blocks(text):
    """The structured shape used by Breaking Belize News.

        Orange Walk District
        Time: 6:00 PM - 9:00 PM
        Feeders & Zones: Feeder 2 (Zone: All)
        Areas Affected: ...
    """
    out = []
    pat = re.compile(
        r"(?P<head>[^\n]{0,80})\n"
        r"Time:\s*(?P<times>[^\n]+)\n"
        r"Feeders?\s*&?\s*Zones?:\s*(?P<fz>[^\n]+)\n"
        r"Areas?\s*Affected:\s*(?P<areas>[^\n]+)",
        re.I)
    for m in pat.finditer(text):
        tm = re.search(TIME_RANGE, m.group("times"), re.I)
        if not tm:
            continue
        fz = m.group("fz")
        fm = re.search(r"Feeder\s*(?:#\s*)?([0-9]{1,2}(?:\s*(?:,|&|and)\s*[0-9]{1,2})*|all)",
                       fz, re.I)
        zm = re.search(r"Zone:?\s*([^)\n]+)", fz, re.I)
        out.append({
            "load_center": find_load_centre(m.group("head")) or find_load_centre(fz),
            "feeder": (fm.group(1).strip().upper() if fm else None),
            "zone": (zm.group(1).strip().rstrip(".)").upper() if zm else None),
            "start": norm_time(tm.group(1)),
            "end": norm_time(tm.group(2)),
            "areas_text": m.group("areas").strip(),
            "heading": m.group("head").strip(),
            "raw_text": m.group(0).strip(),
        })
    return out


def parse_inline(text):
    """The looser shape most outlets use.

        7:00 p.m. to 10:00 p.m. Belmopan Feeder 3: Market Area, Central Site...
    """
    out = []
    pat = re.compile(
        TIME_RANGE +
        r"[\s:\-\u2013]*"
        r"(?P<lc>[A-Z][A-Za-z' ]{2,24}?)\s*"
        r"Feeder\s*(?:#\s*)?(?P<f>[0-9]{1,2}|ALL)\s*"
        r"(?:\(?Zone:?\s*(?P<z>[^)\n:]+)\)?)?\s*[:\-]\s*"
        r"(?P<areas>[^\n]{10,600})",
        re.I)
    for m in pat.finditer(text):
        lc = find_load_centre(m.group("lc"))
        if not lc:
            continue
        out.append({
            "load_center": lc,
            "feeder": m.group("f").strip().upper(),
            "zone": (m.group("z").strip().upper() if m.group("z") else None),
            "start": norm_time(m.group(1)),
            "end": norm_time(m.group(2)),
            "areas_text": m.group("areas").strip(),
            "heading": None,
            "raw_text": m.group(0).strip(),
        })
    return out


def article_text(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
        t.decompose()
    art = (soup.find("article")
           or soup.find("div", class_=re.compile("entry-content|post-content|td-post-content")))
    txt = (art or soup).get_text("\n", strip=True)
    return re.sub(r"\n{2,}", "\n", txt)


def make_id(date, start, lc, feeder, zone):
    key = "|".join([date or "", start or "", (lc or "").lower(),
                    str(feeder or "").lower(), str(zone or "").lower(), "ls"])
    return "ls" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def discover(session):
    urls = []
    for src in SOURCES:
        try:
            r = session.get(src["search"], headers=UA, timeout=40)
            for m in re.finditer(src["pattern"], r.text, re.I):
                u = "https://" + m.group(0)
                if u not in urls:
                    urls.append(u)
        except Exception as e:
            print("  %s unreachable (%s)" % (src["name"], type(e).__name__),
                  file=sys.stderr)
    return urls[:MAX_ARTICLES]


def outlet_for(url):
    for src in SOURCES:
        if re.search(src["pattern"].split("/")[0], url):
            return src["name"]
    return "unknown"


def records_from(text, url, now):
    fallback = now.date().isoformat()
    date = article_date(text, fallback)
    blocks = parse_blocks(text) or parse_inline(text)
    idx = build_area_index(load_gazetteer())
    stamp = now.replace(microsecond=0).isoformat()
    recs = []
    for b in blocks:
        if not (b["load_center"] and b["start"] and b["end"]):
            continue
        ids, _ = match_areas(b["areas_text"], idx)
        recs.append({
            "id": make_id(date, b["start"], b["load_center"], b["feeder"], b["zone"]),
            "kind": "load_shedding",
            "tentative": True,
            "load_center": b["load_center"],
            "feeder": b["feeder"],
            "zone": b["zone"],
            "date": date,
            "start": b["start"],
            "end": b["end"],
            "timezone": TIMEZONE,
            "area_ids": ids,
            "areas_text": b["areas_text"],
            "raw_text": b["raw_text"],
            "source_url": url,
            "source_name": outlet_for(url),
            "ingested_at": stamp,
        })
    return recs


def write(recs, now):
    path = os.path.join(DATA, "loadshedding.json")
    cutoff = (now.date() - dt.timedelta(days=MAX_AGE_DAYS)).isoformat()
    recs = [r for r in recs if (r.get("date") or "") >= cutoff]
    recs.sort(key=lambda r: (r.get("date") or "", r.get("start") or ""))
    doc = {
        "meta": {
            "schema_version": 1,
            "timezone": TIMEZONE,
            "checked_at": now.replace(microsecond=0).isoformat(),
            "warning": (
                "Load shedding is tentative. BEL states the schedule depends on "
                "real-time demand and generation and can change at short notice. "
                "This is also second-hand, republished by news outlets from BEL's "
                "announcements, so it must never be shown as certain."
            ),
            "not_from": "bel.com.bz/PowerUpdates, which does not carry load shedding",
            "count": len(recs),
        },
        "schedules": recs,
    }
    if os.path.exists(path):
        with io.open(path, encoding="utf-8") as f:
            if json.load(f).get("schedules") == recs:
                return False
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
        f.write("\n")
    return True


def self_test():
    fails = []

    def check(label, got, want):
        if got != want:
            fails.append("%s\n     got:  %r\n     want: %r" % (label, got, want))

    check("time pm", norm_time("6:00 PM"), "18:00")
    check("time dotted", norm_time("7:00 p.m."), "19:00")
    check("time am", norm_time("9:00 AM"), "09:00")
    check("date long", article_date("Schedule for Tuesday, September 1, 2026", "x"),
          "2026-09-01")
    check("load centre from heading", find_load_centre("Belmopan & Surrounding Villages"),
          "Belmopan")
    check("load centre absent", find_land := find_load_centre("Some Random Heading"), None)

    block = (
        "Orange Walk District\n"
        "Time: 6:00 PM \u2013 9:00 PM\n"
        "Feeders & Zones: Feeder 2 (Zone: All)\n"
        "Areas Affected: Portions of Orange Walk Town from the Philip Goldson "
        "Highway to Guadalupe Street, Main Street, and Pettville.\n"
    )
    b = parse_blocks(block)
    check("block parsed", len(b), 1)
    if b:
        check("block load centre", b[0]["load_center"], "Orange Walk")
        check("block feeder", b[0]["feeder"], "2")
        check("block zone", b[0]["zone"], "ALL")
        check("block start", b[0]["start"], "18:00")
        check("block end", b[0]["end"], "21:00")
        check("block areas kept", "Guadalupe Street" in b[0]["areas_text"], True)

    inline = "7:00 p.m. to 10:00 p.m. Belmopan Feeder 3: Market Area, Central Site, Site 7."
    i = parse_inline(inline)
    check("inline parsed", len(i), 1)
    if i:
        check("inline load centre", i[0]["load_center"], "Belmopan")
        check("inline feeder", i[0]["feeder"], "3")
        check("inline start", i[0]["start"], "19:00")

    now = dt.datetime(2026, 9, 1, 12, 0, tzinfo=BELIZE_TZ)
    recs = records_from(block, "https://www.breakingbelizenews.com/x", now)
    check("record built", len(recs), 1)
    if recs:
        r = recs[0]
        check("always tentative", r["tentative"], True)
        check("marked load shedding", r["kind"], "load_shedding")
        check("source recorded", r["source_name"], "Breaking Belize News")
        check("raw_text kept", bool(r["raw_text"]), True)
        check("id stable", r["id"], make_id("2026-09-01", "18:00", "Orange Walk", "2", "ALL"))

    fx = os.path.join(FIXTURES, "loadshedding-2026-09-01.txt")
    if os.path.exists(fx):
        with io.open(fx, encoding="utf-8") as f:
            recs = records_from(f.read(), "https://www.breakingbelizenews.com/x", now)
        check("fixture yields three blocks", len(recs), 3)
        check("fixture ids unique", len(set(r["id"] for r in recs)), len(recs))
        lcs = sorted(r["load_center"] for r in recs)
        check("fixture load centres", lcs, ["Belmopan", "Ladyville", "Orange Walk"])
    else:
        fails.append("fixture missing: %s" % fx)

    if fails:
        print("SELF TEST FAILED (%d)" % len(fails))
        for f in fails:
            print("  - " + f)
        return 1
    print("self test passed")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Scrape BEL load-shedding schedules.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--fixture", help="parse a saved article text file")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    now = now_belize()

    if args.fixture:
        with io.open(args.fixture, encoding="utf-8") as f:
            recs = records_from(f.read(), "file://" + args.fixture, now)
    else:
        import requests
        session = requests.Session()
        urls = discover(session)
        print("articles found : %d" % len(urls))
        recs = []
        for u in urls:
            try:
                html = session.get(u, headers=UA, timeout=40).text
            except Exception as e:
                print("  skip %s (%s)" % (u[:70], type(e).__name__))
                continue
            got = records_from(article_text(html), u, now)
            slug = [x for x in u.rstrip("/").split("/") if x][-1]
            print("  %-64s %d block(s)" % (slug[:64], len(got)))
            recs.extend(got)

    by_id = {}
    for r in recs:
        by_id.setdefault(r["id"], r)
    recs = list(by_id.values())

    print()
    print("schedules parsed : %d" % len(recs))
    for r in sorted(recs, key=lambda r: (r["date"], r["start"])):
        print("  %s %s-%s  %-14s F%-4s Z%-6s %s"
              % (r["date"], r["start"], r["end"], r["load_center"],
                 r["feeder"] or "?", (r["zone"] or "?")[:6],
                 r["areas_text"][:52]))

    if args.dry_run:
        print("\ndry run, nothing written")
        return 0
    print("\ndata/loadshedding.json %s"
          % ("updated" if write(recs, now) else "unchanged"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
