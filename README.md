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

**BEL does not publish feeder service boundaries.** Nobody does. So the shapes are derived:

1. Every area BEL names in a notice is geocoded against the GeoNames Belize gazetteer.
2. Points are grouped by the load centre and feeder that named them.
3. Each group gets a convex hull.

**A hull is a floor on where a feeder reaches, not its edge.** Two things follow. A notice lists the
areas affected *that day*, not everything on the feeder, so the real service area is larger. And a
convex hull fills the gaps between named places, so it can cover ground the feeder does not serve.
Records carry a `truncated` flag where BEL's own list was partial, and the page says so in the footer.

Three safeguards worth knowing about, because each one caught a real error:

- **District-aware disambiguation.** Belize has a San Antonio in four districts and a Santa Elena in
  two. Candidates are scored against the district of the feeder that named them.
- **Outlier rejection.** GeoNames alternate names collide across districts: "Santa Ana" resolves to a
  *Santana* in Belize District, "Santa Marta" to a *Santa Martha* in Orange Walk. Each collision
  stretched a hull over 100km. Points more than 45km from their feeder's cluster are dropped and
  logged in `feeder_shapes.json`.
- **Refusing to plot.** Where the only candidate for a name sits in the wrong district, the record
  keeps its name for search but gets no coordinates. Plotting a village in the wrong district is worse
  than plotting nothing.

Feeders with one or two named places render as a dot or a line rather than an area, because that is
what the evidence supports. Belize City is the thinnest: its feeders are described as "Northside
Belize City", which is a phrase, not an area.

---

## Searching by street

The gazetteer built from BEL's notices contains about 40 streets, and each one is there only because
BEL happened to name it. Someone searching for their own road got nothing back, which fails the one
question this site exists to answer. So `build_streets.py` pulls about 3,400 named roads for Belize
from OpenStreetMap and attaches each to its nearest load centre.

**A street resolves to its town, not to a feeder.** BEL does not publish which feeder serves which
street, so the panel says plainly that it is showing everything listed for that load centre and that
your street may be on any of its feeders. That is a prompt to check, not a confirmation.

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

## Load shedding is not covered

Load shedding is announced on BEL's Facebook page and reaches news sites hours later. It is **not** on
the Power Updates page, so this site does not cover it. v1 is planned and unscheduled outages only.
Nothing here scrapes Facebook.

The feeder inventory in `data/feeders.json` was partly compiled from load-shedding notices republished
by Belize news outlets, but that was a one-off research pass, not an automated feed.

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
