"""
Put the site into a state where every feature can be seen at once.

    python scraper/make_demo_data.py --apply     # swap in demo data
    python scraper/make_demo_data.py --restore    # put the real data back
    python scraper/make_demo_data.py --stale      # demo data, but 20 hours old

Real data usually shows one quiet notice, so most of the site cannot be checked
by looking at it: nothing is off, nothing is cancelled, nothing is tentative.
This generates one record for every state, anchored to the clock so "off now"
really is off now, and wired to real areas and feeders so the search, the map
colours and the panels all resolve properly.

The real files are copied aside on --apply and put back on --restore. Nothing
here is ever committed as live data.
"""

import argparse
import datetime as dt
import io
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")

TZ = dt.timezone(dt.timedelta(hours=-6), "America/Belize")
SRC = "https://www.bel.com.bz/PowerUpdates/"

PAIRS = [("outages.json", "outages.real.json"),
         ("loadshedding.json", "loadshedding.real.json")]


def hhmm(t):
    return t.strftime("%H:%M")


def areas_named(names, district=None):
    """Ids for these place names, one per name, in the right district.

    Belize has a San Antonio in four districts. Taking every match put the same
    notice in Toledo and Corozal at once and made the demo look like a bug in
    the site rather than a bug in this helper.
    """
    with io.open(os.path.join(DATA, "areas.json"), encoding="utf-8") as f:
        areas = json.load(f)["areas"]
    out = []
    for n in names:
        hits = [a for a in areas if a["name"].lower() == n.lower()]
        if district:
            same = [a for a in hits if a["district"] == district]
            hits = same or hits
        if hits:
            out.append(hits[0]["id"])
    return out


def build(now, stale_hours=0):
    today = now.date().isoformat()
    soon = (now.date() + dt.timedelta(days=2)).isoformat()
    stamp = (now - dt.timedelta(hours=stale_hours)).replace(microsecond=0).isoformat()

    def rec(i, **kw):
        base = {
            "id": "demo%02d" % i,
            "type": "planned",
            "status": "announced",
            "timezone": "America/Belize",
            "tentative": False,
            "source_url": SRC,
            "published_at": None,
            "ingested_at": stamp,
            "area_ids": [],
        }
        base.update(kw)
        return base

    outages = [
        # 1. running right now
        rec(1,
            district="Portion of Stann Creek District, Rural",
            load_center="Dangriga", feeder="3", zone="ALL",
            date=today,
            start=hhmm(now - dt.timedelta(hours=1)),
            end=hhmm(now + dt.timedelta(hours=2)),
            area_ids=areas_named(["Hopkins", "Sittee River", "Silk Grass", "Maya Centre"], "Stann Creek"),
            purpose="DEMO DATA. Verifying the OFF NOW state.",
            raw_text="Load Center: Dangriga. Feeder: 3. Zone: All. Areas to be affected: "
                     "Hopkins, Sittee River, Silk Grass, Maya Center. Outage type: Planned. "
                     "Purpose of outage: DEMO DATA."),

        # 2. later today
        rec(2,
            district="Portion of Belize District",
            load_center="Belize City", feeder="8", zone="ALL",
            date=today,
            start=hhmm(now + dt.timedelta(hours=3)),
            end=hhmm(now + dt.timedelta(hours=6)),
            area_ids=areas_named(["Kelly Street", "Barrack Road", "Wilson Street"], "Belize"),
            purpose="DEMO DATA. Verifying the OFF TODAY state.",
            raw_text="Load Center: Belize City. Feeder: 8. Zone: All. Areas to be affected: "
                     "Kelly Street, Fuller's Lane, Wilson Street, Barrack Road. "
                     "Outage type: Planned. Purpose of outage: DEMO DATA."),

        # 3. unscheduled, running now, so the type chip differs
        rec(3, type="unscheduled",
            district="Toledo District",
            load_center="Punta Gorda", feeder="2", zone="ALL",
            date=today,
            start=hhmm(now - dt.timedelta(minutes=30)),
            end=hhmm(now + dt.timedelta(hours=1)),
            area_ids=areas_named(["San Antonio", "Big Fall", "Silver Creek"], "Toledo"),
            purpose="DEMO DATA. Verifying the unscheduled chip.",
            raw_text="Load Center: Punta Gorda. Feeder: 2. Zone: All. Areas to be affected: "
                     "San Antonio, Big Falls, Silver Creek. Outage type: Unscheduled. "
                     "Purpose of outage: DEMO DATA."),

        # 4. days ahead
        rec(4,
            district="Portion of Corozal District, Rural",
            load_center="Corozal", feeder="6", zone="2",
            date=soon, start="10:00", end="12:00",
            area_ids=areas_named(["Santa Clara", "San Roman"], "Corozal"),
            purpose="DEMO DATA. Verifying the scheduled state.",
            raw_text="Load Center: Corozal. Feeder: 6. Zone: 2. Areas to be affected: "
                     "Santa Clara and San Roman. Outage type: Planned. "
                     "Purpose of outage: DEMO DATA."),

        # 5. Feeder ALL, which is a real value and must not read as missing
        rec(5,
            district="Portion of Stann Creek and Toledo District",
            load_center="Independence", feeder="ALL", zone="ALL",
            date=today,
            start=hhmm(now + dt.timedelta(hours=1)),
            end=hhmm(now + dt.timedelta(hours=8)),
            area_ids=areas_named(["Placencia", "Seine Bight Village", "Maya Beach",
                                  "Georgetown", "Red Bank Village"], "Stann Creek"),
            purpose="DEMO DATA. Verifying Feeder ALL.",
            raw_text="Load Center: Independence. Feeder: ALL. Zone: ALL. Areas to be "
                     "affected: entire Placencia Peninsula including Placencia Village and "
                     "Seine Bight, Maya Beach, Georgetown, Red Bank. Outage type: Planned. "
                     "Purpose of outage: DEMO DATA."),

        # 6. cancelled, which must stay visible for 24 hours
        rec(6, status="cancelled",
            cancelled_at=(now - dt.timedelta(hours=2)).replace(microsecond=0).isoformat(),
            district="Cayo District",
            load_center="San Ignacio", feeder="1", zone="ALL",
            date=today,
            start=hhmm(now + dt.timedelta(hours=4)),
            end=hhmm(now + dt.timedelta(hours=7)),
            area_ids=areas_named(["Santa Elena", "Bullet Tree Falls"], "Cayo"),
            purpose="DEMO DATA. Verifying that a cancelled notice stays visible.",
            raw_text="Load Center: San Ignacio. Feeder: 1. Zone: All. Areas to be affected: "
                     "downtown San Ignacio, Santa Elena. Outage type: Planned. "
                     "Purpose of outage: DEMO DATA."),
    ]

    def ls(i, **kw):
        base = {
            "id": "demols%02d" % i,
            "kind": "load_shedding",
            "tentative": True,
            "timezone": "America/Belize",
            "source_name": "Demo News",
            "source_url": "https://example.invalid/demo",
            "ingested_at": stamp,
            "area_ids": [],
        }
        base.update(kw)
        return base

    shedding = [
        # 7. load shedding running right now
        ls(1, load_center="Orange Walk", feeder="2", zone="ALL",
           date=today,
           start=hhmm(now - dt.timedelta(minutes=20)),
           end=hhmm(now + dt.timedelta(hours=1, minutes=40)),
           area_ids=areas_named(["Orange Walk", "Trial Farm", "San Estevan"], "Orange Walk"),
           areas_text="Portions of Orange Walk Town, Trial Farm and San Estevan. DEMO DATA.",
           raw_text="Orange Walk District\nTime: DEMO\nFeeders & Zones: Feeder 2 (Zone: All)\n"
                    "Areas Affected: Portions of Orange Walk Town, Trial Farm and "
                    "San Estevan. DEMO DATA."),

        # 8. load shedding later tonight
        ls(2, load_center="Belmopan", feeder="4", zone="ALL",
           date=today,
           start=hhmm(now + dt.timedelta(hours=5)),
           end=hhmm(now + dt.timedelta(hours=8)),
           area_ids=areas_named(["Camalote", "Teakettle", "Roaring Creek"], "Cayo"),
           areas_text="Portions of Belmopan including University Heights, Las Flores; "
                      "Roaring Creek, Camalote, Teakettle. DEMO DATA.",
           raw_text="Belmopan & Surrounding Villages\nTime: DEMO\n"
                    "Feeders & Zones: Feeder 4 (Zone: All)\nAreas Affected: Portions of "
                    "Belmopan including University Heights, Las Flores; Roaring Creek, "
                    "Camalote, Teakettle. DEMO DATA."),
    ]

    odoc = {
        "meta": {"schema_version": 1, "source_url": SRC, "timezone": "America/Belize",
                 "checked_at": stamp, "note": "DEMO DATA, not real BEL notices."},
        "outages": outages,
    }
    ldoc = {
        "meta": {"schema_version": 1, "timezone": "America/Belize", "checked_at": stamp,
                 "warning": "DEMO DATA, not real notices.", "count": len(shedding)},
        "schedules": shedding,
    }
    return odoc, ldoc


def apply(stale_hours):
    for live, saved in PAIRS:
        lp, sp = os.path.join(DATA, live), os.path.join(DATA, saved)
        if os.path.exists(lp) and not os.path.exists(sp):
            shutil.copy2(lp, sp)

    now = dt.datetime.now(TZ)
    odoc, ldoc = build(now, stale_hours)
    for name, doc in (("outages.json", odoc), ("loadshedding.json", ldoc)):
        with io.open(os.path.join(DATA, name), "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1, ensure_ascii=False)
            f.write("\n")

    print("demo data applied%s" % (", aged %dh" % stale_hours if stale_hours else ""))
    print()
    print("%-9s %-8s %-14s %-6s %-11s %s"
          % ("id", "kind", "load centre", "feeder", "when", "expected state"))
    for o in odoc["outages"]:
        exp = ("cancelled" if o["status"] == "cancelled"
               else "OFF NOW" if o["date"] == now.date().isoformat()
               and o["start"] <= hhmm(now) <= o["end"]
               else "OFF TODAY" if o["date"] == now.date().isoformat()
               else "scheduled")
        print("%-9s %-8s %-14s %-6s %-11s %s"
              % (o["id"], o["type"][:8], o["load_center"], o["feeder"],
                 o["start"] + "-" + o["end"], exp))
    for r in ldoc["schedules"]:
        exp = ("MAY BE OFF NOW" if r["start"] <= hhmm(now) <= r["end"]
               else "load shedding planned")
        print("%-9s %-8s %-14s %-6s %-11s %s"
              % (r["id"], "shed", r["load_center"], r["feeder"],
                 r["start"] + "-" + r["end"], exp))
    print()
    print("now run: python scraper/build_bundle.py")


def restore():
    n = 0
    for live, saved in PAIRS:
        lp, sp = os.path.join(DATA, live), os.path.join(DATA, saved)
        if os.path.exists(sp):
            shutil.move(sp, lp)
            n += 1
    print("restored %d real data file(s)" % n if n else "nothing to restore")
    print("now run: python scraper/build_bundle.py")


def main():
    ap = argparse.ArgumentParser(description="Demo data for checking every state.")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--stale", action="store_true",
                    help="age the timestamps 20 hours to trigger the out-of-date banner")
    args = ap.parse_args()
    if args.restore:
        restore()
    elif args.apply or args.stale:
        apply(20 if args.stale else 0)
    else:
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
