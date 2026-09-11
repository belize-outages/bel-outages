# Belize power outages

A one-page map that answers one question: **does my area have a scheduled power outage, and when.**

Search a village, town or street. The map shows which BEL feeders have an outage running now, later
today, or scheduled. Everything comes from BEL's own public notices.

Static HTML, CSS and vanilla JS. No framework, no build step for the page itself, no server, no
runtime network calls. It works opened as a local file.

---

## Status

The site works and the data is real. **The hourly refresh is not connected yet.** The workflow exists
but its schedule is commented out, so `data/outages.json` only changes when you run the scraper by
hand. See [Connecting the refresh](#connecting-the-refresh).

---

## What is in here

```
index.html            the page
about.html            what the site is, where the data comes from, what it cannot tell you
style.css             light and dark, mobile first, 44px tap targets
app.js                map projection, search, result states
vendor/fuse.min.js    fuzzy search, vendored (Fuse.js 7.0.0, Apache 2.0)
data/
  outages.json        current outages, written by the scraper
  history.json        every notice seen finish, append-only evidence
  areas.json          gazetteer: place names, aliases, coordinates, feeders
  feeders.json        load centres, substations, feeder inventory
  feeder_shapes.json  derived feeder service-area shapes
  streets.json        3,451 streets with coordinates, grouped by load centre
  streets.js          the street index, loaded only when someone searches
  places.json         110 settlement outlines
  roads.json          177 highway polylines
  detail.js           outlines + roads, loaded once the page is idle
  belize.json         district outlines for the base map
  bundle.js           the four files above, packed for the browser
scraper/
  scrape.py           BEL scraper and parser, with its own tests
  build_gazetteer.py  geocodes area names against GeoNames
  build_feeder_shapes.py  derives feeder hulls
  build_basemap.py    downloads and simplifies district outlines
  build_streets.py    pulls Belize street names from OpenStreetMap
  build_places.py     pulls town and village outlines from OpenStreetMap
  build_roads.py      pulls the trunk and primary highway network
  build_bundle.py     packs data/ into bundle.js
  fixtures/           saved pages the tests run against
research/             source PDFs, not used at runtime
.github/workflows/refresh.yml   hourly refresh (currently disabled)
```

### The app.js syntax guard

`build_bundle.py` refuses to build when `app.js` has a quoted string left open
at the end of a line, and names the line. A JavaScript string in single or
double quotes cannot contain a raw newline, so this is always a bug, and it is
the bug that broke the file three times, every time from an apostrophe in a word
like "BEL's" inside a single-quoted string. Each time it hid behind a cached copy
in the browser before anyone noticed. The check skips regex literals, so
`/[&<>"]/` does not trip it.

### Cache busting

`build_bundle.py` stamps a content hash into `index.html` as `?v=<hash>` on
`style.css`, `app.js` and `data/bundle.js`, and `app.js` appends the same stamp
to the files it loads on demand. Without it a returning visitor keeps the old
`bundle.js` after the hourly job commits a new one and reads stale outages. It
bit development too: a syntax error in `app.js` stayed invisible through several
rounds because the browser kept rendering a cached copy while the real file threw.
The hash changes only when the content does, so unchanged deploys stay cached.

### Why `bundle.js` exists

Browsers block `fetch()` against `file://` URLs, and the site has to work as a local file. So the data
reaches the page through a `<script>` tag instead. The JSON files stay canonical, the scraper keeps
writing them, and `build_bundle.py` derives the bundle. It also drops research fields the browser does
not need, which matters when someone loads this on mobile data during an outage.

---

## Running it

Any static file server, or just open `index.html` in a browser.

```bash
python -m http.server 8765
```

## Running the scraper by hand

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r scraper/requirements.txt

python scraper/scrape.py --self-test    # parser tests, no network
python scraper/scrape.py --dry-run      # fetch and print, write nothing
python scraper/scrape.py                # fetch and update data/outages.json
python scraper/build_bundle.py          # repack for the browser
```

Run `--self-test` first. If BEL changes the page shape, the tests fail there rather than writing a file
full of nulls.

Rebuilding the geodata is only needed when `feeders.json` changes:

```bash
python scraper/build_gazetteer.py
python scraper/build_feeder_shapes.py
python scraper/build_bundle.py
```

---

## Connecting the refresh

`.github/workflows/refresh.yml` runs on `workflow_dispatch` only. To turn on the hourly schedule:

1. Uncomment the two `schedule:` lines in the workflow.
2. **Settings > Actions > General > Workflow permissions**, select **Read and write permissions** so
   the job can push its commit.
3. Run it once by hand from the Actions tab and read the log. A run that reports no changes is the
   healthy case when BEL has not moved anything.

The job runs the parser tests, scrapes, and commits only when `data/outages.json` actually changed.

**Actions cron drifts**, sometimes by 30 minutes or more. Nothing in the UI promises minute accuracy,
and nothing should.

**Scheduled workflows are disabled after 60 days of repository inactivity.** This workflow's own data
commits count as activity, so once connected it keeps itself alive. If BEL published nothing for two
solid months there would be no commits, and the schedule could lapse. If the site's "checked" time goes
quiet for weeks, look at the Actions tab.

---

## Enabling GitHub Pages

1. Push to a public repo.
2. **Settings > Pages**.
3. Under **Source**, choose **Deploy from a branch**.
4. Branch `main`, folder `/ (root)`. Save.

The site is at `https://<user>.github.io/<repo>/` in a minute or two. No build step, no Jekyll
config needed, because everything is already static.

---

## How the feeder map is built, and what it is not

**BEL does not publish feeder boundaries**, and a feeder boundary is electrical
anyway: set by switchgear positions and load balancing, and reconfigurable. Two
houses on one street can sit on different feeders. So the shapes are derived.

Each area BEL names in a notice is resolved to its real geometry and buffered by
a plausible service distance, then the results are unioned:

| what BEL named | what gets drawn | buffer |
| --- | --- | --- |
| a village or town | its OpenStreetMap boundary | 150m |
| a street | that street's real line, nearest the load centre | 250m |
| anything else | its point | 500-800m |

Today that is 22 areas from real settlement outlines, 29 from real street
geometry, and 80 still falling back to a point. The urban feeders gained the
most: Belize City Feeder 8 was a single dot and is now a 2.2 sq km shape traced
along Kelly Street, Fuller's Lane, Wilson Street, Barrack Road and Princess
Margaret Drive.

An earlier version drew a convex hull around a scatter of points. That both
missed the real footprint and filled in ground between named places that the
feeder may not serve at all.

**A shape is still a floor, not an edge.** A notice lists the areas affected
*that day*, not everything on the feeder, so the real service area is larger.
Records carry a `truncated` flag where BEL's own list was partial.

Three safeguards, each of which caught a real error:

- **District-aware disambiguation.** Belize has a San Antonio in four districts
  and a Santa Elena in two. Candidates are scored against the district of the
  feeder that named them.
- **Outlier rejection anchored on the load centre.** GeoNames alternate names
  collide across districts: "Santa Ana" resolves to a *Santana* in Belize
  District, "Santa Marta" to a *Santa Martha* in Orange Walk. Each collision
  stretched a shape over 100km. Anything past 45km from its load centre is
  dropped and logged.
- **Refusing to plot.** Where the only candidate for a name sits in the wrong
  district, the record keeps its name for search but gets no coordinates.

A feeder whose named areas all fail to resolve is still drawn at its load centre
and labelled "load centre only", so the legend never quietly omits a feeder.

---

## Searching by street

The gazetteer built from BEL's notices contains about 40 streets, and each one is there only because
BEL happened to name it. Someone searching for their own road got nothing back, which fails the one
question this site exists to answer. So `build_streets.py` pulls about 3,400 named roads for Belize
from OpenStreetMap and attaches each to its nearest load centre.

**A street never inherits its town's outage.** BEL does not publish which feeder serves which street,
so a street search answers "Your street is not named", explains why, and then lists what BEL did
publish for that town with the places each notice actually names. Saying "Outage scheduled" told every
street in Corozal Town it was affected by a Feeder 6 notice covering two rural villages fifteen
kilometres away. A village BEL names by name still gets a definite answer; only the guess is removed.

Searching an address moves the map to that address. Street coordinates are stored as integer offsets
from the load centre at 1/1000 degree, about 110 metres, which is enough to land on the right street
and keeps the index at 93KB instead of the ~135KB absolute coordinates would cost. The page
reconstructs `lon = centres[lc][0] + dlon/1000`.

Most visits are a glance at the map that never needs any of this, so the index loads on the
Three different towns have a Sarstoon Street, so results are labelled by town. OpenStreetMap also
carries occasional typos, "Sartsoon Street" in Belmopan among them, which the fuzzy search absorbs.

Rebuild it with:

```bash
python scraper/build_streets.py
python scraper/build_bundle.py
```

---

## Town and village outlines

`build_places.py` pulls settlement boundaries from OpenStreetMap: 3 cities, 8 towns, 41 villages,
8 hamlets and 50 suburbs and neighbourhoods, 110 in all. They draw as built-up areas under the feeder
shading, with labels that appear as you zoom in.

**Coverage is partial.** Belize has far more villages than have a boundary drawn in OpenStreetMap, and
Corozal Town, San Pedro and Independence are three notable gaps. Anywhere without an outline keeps its
point marker. Drawing an invented circle to make the map look complete would be worse than an honest
dot, so the footer says what the outlines are and where they came from.

Two traps handled here, both of which produced silently wrong maps first:

- **Overpass returns a relation's boundary as separate ways**, often two points long, not as closed
  rings. Treating each fragment as its own polygon gave Spanish Lookout an area of 761,355 sq km,
  which is 33 times the size of Belize. The segments have to be stitched end to end first.
- **An area sanity check** now rejects any ring over 400 sq km as a stitching failure rather than
  drawing it.

Like the street index, this file is loaded on demand rather than at page load, because at the
full-country view the outlines are specks. It arrives when you zoom past roughly 1.8x or pick a place
out of the search.

```bash
python scraper/build_places.py
python scraper/build_bundle.py
```

---

## Roads

BEL writes its outage areas in terms of highways. "All areas from Mile 23 to Mile 38 on Hummingbird
Highway" means nothing on a map with no Hummingbird Highway drawn on it, so `build_roads.py` pulls the
trunk and primary network from OpenStreetMap: 177 polylines covering Philip Goldson, George Price,
Hummingbird, Thomas Vincent Ramos (the Southern Highway) and Coastal Plain, plus the primary roads
feeding them. Names are drawn along the road with SVG `textPath`.

**Secondary and tertiary roads are deliberately left out.** They exist in OpenStreetMap for Belize,
1,478 ways and 28,232 points, and would roughly triple the payload for detail that does not help
anyone answer "is my power off".

Contiguous ways sharing a name are stitched into one polyline before simplifying, because
Douglas-Peucker over a whole highway removes far more than running it over 140 separate fragments of
the same road. That takes the network from 13,970 points to 595, a 96% reduction, for 22.9KB.

Roads and settlement outlines ship together as `detail.js` and load once the page is idle, so first
paint never waits on them.

```bash
python scraper/build_roads.py
python scraper/build_bundle.py
```

---

## Learning which feeder serves which street

BEL does not publish it, and no amount of map geometry can infer it: a feeder
boundary is electrical, set by switchgear positions and load balancing, and
utilities reconfigure them. Two houses on the same street can sit on different
feeders.

What does work is BEL's own notices. Each one states a load centre, a feeder, a
zone and the areas it cut power to, which is a labelled observation of what that
feeder serves. One notice tells you little. A year of them is a dataset.

`data/history.json` is that corpus. Every notice the scraper sees finish is
appended before it leaves the live file. It is append-only and nothing is
removed. Until this existed the scraper deleted finished notices outright, which
threw away the only evidence that could ever answer the question.

Coverage today: 34 feeders, about 220 area-to-feeder observations, and roughly
40 of 3,451 streets tied to a feeder. That grows on its own once the refresh
workflow is connected.

Google Maps was considered as a shortcut and rejected. It carries nothing
OpenStreetMap lacks for this, and its terms forbid deriving and caching a
dataset from it, which would break the zero-cost, no-account model.

---

## Checking every state

Real data usually shows one quiet notice, so most of the site cannot be checked
by looking at it: nothing is off, nothing is cancelled, nothing is tentative.

```bash
python scraper/make_demo_data.py --apply    # one record per state, clock-anchored
python scraper/build_bundle.py
python scraper/make_demo_data.py --stale    # same, but 20 hours old
python scraper/make_demo_data.py --restore  # put the real data back
```

It generates an outage running now, one later today, an unscheduled one, one days
ahead, a `Feeder: ALL` notice, a cancelled notice, load shedding running now and
load shedding tonight, all wired to real areas and feeders. The real files are
copied aside on apply and moved back on restore.

This found four real bugs that no amount of reading the code had surfaced:

- **An outage spread to feeders BEL never named.** One Orange Walk Feeder 2
  notice lit all four Orange Walk feeders, and a Punta Gorda notice lit Corozal
  Feeder 1, because both load centres serve somewhere called San Antonio. When
  BEL names a feeder, that is now the answer.
- **A numbered-feeder notice lit the "all feeders" group** for its load centre.
- **Villages were not searchable.** The 110 settlement outlines were drawn on the
  map but absent from the index, so "Camalote" and "Trial Farm" fell through to
  roads of a similar name and a notice naming the village never reached the
  person searching for it.
- **The out-of-date banner sat on top of the search box**, because its offset was
  hard-coded at 52px and the banner wraps to three lines on a phone.

---

## Load shedding

Load shedding is the rolling blackout BEL runs when generation falls short. It is
**not** on BEL's Power Updates page, and through 2026 it has been the thing people
actually need to check. The site used to be silent during exactly those events,
and silence reads as "you are fine".

BEL announces it on Facebook. Facebook is not scraped here. But Belize news
outlets republish the schedule in plain HTML within minutes, and those pages are
ordinary WordPress articles that answer a plain `requests.get`. `scrape_loadshedding.py`
discovers them through each site's search page and parses two shapes:

```
Orange Walk District                          7:00 p.m. to 10:00 p.m.
Time: 6:00 PM - 9:00 PM              or       Belmopan Feeder 3: Market Area,
Feeders & Zones: Feeder 2 (Zone: All)         Central Site, Site 7
Areas Affected: ...
```

**It is always tentative, and the site says so everywhere it appears.** BEL states
the schedule depends on real-time demand and generation and changes at short
notice, and this reaches us second-hand, so a transcription error at the outlet
becomes an error here. Records carry `tentative: true`, the outlet name and a link
to the article, and the headline reads "MAY BE OFF NOW", never "OFF NOW".

Schedules older than three days are dropped: an old schedule is not a forecast.

```bash
python scraper/scrape_loadshedding.py --self-test
python scraper/scrape_loadshedding.py
python scraper/build_bundle.py
```

---

## Data notes

- **Times are Belize local, exactly as BEL printed them.** Belize is UTC-6 all year and has had no
  daylight saving since 1983. Nothing round-trips through UTC.
- **`Feeder: ALL` and `Zone: ALL` are real values**, not missing data.
- **`raw_text` is mandatory** on every record. When the parsing is wrong, you need to see what BEL
  actually wrote.
- **Cancelled outages stay visible for 24 hours**, marked cancelled. BEL removes rows without comment,
  and a notice that silently disappears reads to a user like a bug in this site.
- Running the scraper twice produces no duplicates. Records are keyed by a stable hash of date, start,
  load centre, feeder and zone.

## Sources and licences

- Outage notices: [bel.com.bz/PowerUpdates](https://www.bel.com.bz/PowerUpdates/)
- Place coordinates: [GeoNames](https://www.geonames.org/), CC BY 4.0
- Street names and settlement outlines: [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors, ODbL
- District outlines: [geoBoundaries](https://www.geoboundaries.org/) gbOpen ADM1, CC BY 4.0
- Network context: BEL Integrated Resource Plan, filed with the Belize PUC
- Search: [Fuse.js](https://fusejs.io/) 7.0.0, Apache 2.0

**Not affiliated with Belize Electricity Limited.** Compiled from public notices. Verify with the
BEL 24-7 app before relying on it.
