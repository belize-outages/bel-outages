/* Belize power outages. Vanilla JS, no framework, no build step, no network calls.
   Data arrives on window.BEL from data/bundle.js. */
(function () {
  "use strict";

  var BEL = window.BEL;
  var $ = function (id) { return document.getElementById(id); };

  /* ---------------------------------------------------------------- time
     Belize is UTC-6 all year and has had no daylight saving since 1983.
     Times from BEL are local as printed and never round-trip through UTC. */
  var OFFSET_MIN = -360;

  function belizeNow() {
    var d = new Date();
    return new Date(d.getTime() + (OFFSET_MIN - -d.getTimezoneOffset()) * 60000);
  }
  function todayISO() {
    var d = belizeNow();
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  }
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function minutesNow() { var d = belizeNow(); return d.getHours() * 60 + d.getMinutes(); }
  function toMin(hhmm) {
    if (!hhmm) return null;
    var p = hhmm.split(":");
    return parseInt(p[0], 10) * 60 + parseInt(p[1], 10);
  }
  function pretty(hhmm) {
    if (!hhmm) return "?";
    var p = hhmm.split(":"), h = parseInt(p[0], 10), ap = h < 12 ? "AM" : "PM";
    h = h % 12; if (h === 0) h = 12;
    return h + ":" + p[1] + ap;
  }
  function prettyDate(iso) {
    if (!iso) return "?";
    var d = new Date(iso + "T12:00:00"), t = todayISO();
    if (iso === t) return "today";
    var tm = new Date(t + "T12:00:00");
    if ((d - tm) / 86400000 === 1) return "tomorrow";
    return d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" });
  }
  function ago(iso) {
    if (!iso) return "at an unknown time";
    var then = new Date(iso.length <= 19 ? iso + "-06:00" : iso);
    var mins = Math.round((Date.now() - then.getTime()) / 60000);
    if (isNaN(mins)) return "at an unknown time";
    if (mins < 2) return "just now";
    if (mins < 60) return mins + " minutes ago";
    var h = Math.round(mins / 60);
    if (h < 24) return h === 1 ? "about an hour ago" : "about " + h + " hours ago";
    var d = Math.round(h / 24);
    return d === 1 ? "about a day ago" : "about " + d + " days ago";
  }
  function hoursSince(iso) {
    var then = new Date(iso.length <= 19 ? iso + "-06:00" : iso);
    return (Date.now() - then.getTime()) / 3600000;
  }

  /* ------------------------------------------------------- outage → feeder */
  function slug(s) {
    return (s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  }

  var areaById = {};
  BEL.areas.forEach(function (a) { areaById[a.id] = a; });
  var feederById = {};
  BEL.feeders.forEach(function (f) { feederById[f.id] = f; });

  /* Which feeders does one outage touch? BEL names a load centre and a feeder,
     and "ALL" is a real value meaning every feeder on that load centre. The
     area list is used as a second route in, because a notice sometimes names
     places that sit on a feeder it did not name. */
  function feedersFor(o) {
    var lc = slug(o.load_center), out = {};
    BEL.feeders.forEach(function (f) {
      if (slug(f.lc) !== lc) return;
      if (o.feeder === "ALL" || f.f === "ALL" || String(f.f) === String(o.feeder)) out[f.id] = 1;
    });
    (o.area_ids || []).forEach(function (aid) {
      var a = areaById[aid];
      if (a) (a.f || []).forEach(function (fid) { out[fid] = 1; });
    });
    return Object.keys(out);
  }

  /* Live status of one outage, from the clock. */
  function outageState(o) {
    if (o.status === "cancelled") return "cancelled";
    var t = todayISO(), s = toMin(o.start), e = toMin(o.end), now = minutesNow();
    if (o.date === t) {
      if (s !== null && e !== null && now >= s && now <= e) return "off";
      if (s !== null && now < s) return "today";
      return "past";
    }
    return o.date > t ? "soon" : "past";
  }

  var RANK = { off: 4, today: 3, soon: 2, cancelled: 1, none: 0, past: 0 };

  var outagesByFeeder = {};
  BEL.outages.forEach(function (o) {
    o._state = outageState(o);
    feedersFor(o).forEach(function (fid) {
      (outagesByFeeder[fid] = outagesByFeeder[fid] || []).push(o);
    });
  });

  function feederState(fid) {
    var list = outagesByFeeder[fid] || [], best = "none";
    list.forEach(function (o) { if (RANK[o._state] > RANK[best]) best = o._state; });
    return best === "past" ? "none" : best;
  }

  /* ------------------------------------------------------------ projection */
  var bbox = (function () {
    var b = [Infinity, Infinity, -Infinity, -Infinity];
    function scan(c) {
      if (typeof c[0] === "number") {
        if (c[0] < b[0]) b[0] = c[0]; if (c[1] < b[1]) b[1] = c[1];
        if (c[0] > b[2]) b[2] = c[0]; if (c[1] > b[3]) b[3] = c[1];
      } else { c.forEach(scan); }
    }
    BEL.districts.forEach(function (f) { scan(f.geometry.coordinates); });
    return b;
  })();

  var K = Math.cos((bbox[1] + bbox[3]) / 2 * Math.PI / 180);
  var PAD = 0.04;
  var W = (bbox[2] - bbox[0]) * K, H = bbox[3] - bbox[1];
  var VW = 1000, VH = Math.round(VW * (H / W));
  function px(lon) { return ((lon - bbox[0]) * K / W) * VW; }
  function py(lat) { return (1 - (lat - bbox[1]) / H) * VH; }

  var svg = $("map");
  svg.setAttribute("viewBox", [-VW * PAD, -VH * PAD, VW * (1 + 2 * PAD), VH * (1 + 2 * PAD)].join(" "));
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  var HOME = { x: -VW * PAD, y: -VH * PAD, w: VW * (1 + 2 * PAD), h: VH * (1 + 2 * PAD) };
  var view = Object.assign({}, HOME);

  function applyView() {
    svg.setAttribute("viewBox", view.x + " " + view.y + " " + view.w + " " + view.h);
    if (view.w < HOME.w * 0.98) loadPlaces();   /* any zoom in at all */
    sizeLabels();
  }

  /* Settlement outlines.
     44KB of OpenStreetMap boundaries for 86 cities, towns and villages. At the
     full-country view they would be specks, so this loads once the map is
     zoomed in far enough for them to mean anything, or when a search picks a
     place out. */
  var placeState = "idle";

  function loadPlaces() {
    if (placeState !== "idle") return;
    placeState = "loading";
    var s = document.createElement("script");
    s.src = "data/detail.js";
    s.onload = function () {
      placeState = "ready";
      drawRoads();
      drawPlaces();
      sizeLabels();
    };
    s.onerror = function () { placeState = "failed"; };
    document.head.appendChild(s);
  }

  /* Highways.
     BEL writes its outage areas in terms of roads, "Mile 23 to Mile 38 on
     Hummingbird Highway", so the map needs them to be readable at all. Names
     are drawn along the road with textPath rather than as floating labels. */
  function linePath(coords) {
    return coords.map(function (c, i) {
      return (i ? "L" : "M") + px(c[0]).toFixed(1) + " " + py(c[1]).toFixed(1);
    }).join("");
  }

  function drawRoads() {
    var list = (window.BEL_DETAIL || {}).roads || [];
    var gr = $("layerRoads"), gl = $("layerRoadLabels");
    if (gr.childNodes.length) return;
    var defs = el("defs");
    gr.appendChild(defs);

    /* A highway comes through as several stitched polylines. Label only the
       longest run of each, once, or the same name repeats down the map and
       short stubs get names their length cannot carry. */
    var longest = {};
    list.forEach(function (r, i) {
      if (!r.major || !r.n) return;
      if (!longest[r.n] || r.km > list[longest[r.n]].km) longest[r.n] = i;
    });
    var labelFor = {};
    Object.keys(longest).forEach(function (n) { labelFor[longest[n]] = 1; });

    list.forEach(function (r, i) {
      var node = el("path", { d: linePath(r.g), class: "road " + r.c });
      if (r.n) node.appendChild(el("title")).textContent = r.n;
      gr.appendChild(node);

      if (!labelFor[i]) return;

      /* textPath follows the path's direction, so a road digitised east to west
         would print its name upside down. Flip the copy used for the label. */
      var coords = r.g;
      if (px(coords[coords.length - 1][0]) < px(coords[0][0])) {
        coords = coords.slice().reverse();
      }
      var id = "rd" + i;
      defs.appendChild(el("path", { id: id, d: linePath(coords) }));

      var t = el("text", { class: "rlabel", "data-km": r.km });
      var tp = el("textPath", { startOffset: "50%" });
      tp.setAttributeNS("http://www.w3.org/1999/xlink", "href", "#" + id);
      tp.setAttribute("href", "#" + id);
      tp.textContent = r.n;
      t.appendChild(tp);
      gl.appendChild(t);
    });
  }

  var LABELLED = { city: 1, town: 1, village: 1 };

  function drawPlaces() {
    var list = (window.BEL_DETAIL || {}).places || [];
    var gp = $("layerPlaces"), gl = $("layerLabels");
    if (gp.childNodes.length) return;
    list.forEach(function (p) {
      var d = p.r.map(ringPath).join(" ");
      var node = el("path", { d: d, class: "place " + p.k });
      node.appendChild(el("title")).textContent = p.n + " (" + p.k + ")";
      gp.appendChild(node);

      if (LABELLED[p.k]) {
        var t = el("text", {
          x: px(p.c[0]).toFixed(1), y: py(p.c[1]).toFixed(1),
          class: "plabel " + p.k, "data-k": p.k
        });
        t.textContent = p.n;
        gl.appendChild(t);
      }
    });
  }

  /* Text does not honour vector-effect, so label size is recomputed from the
     current viewBox. Villages only appear once you are close enough that the
     names are not stacked on top of each other. */
  function sizeLabels() {
    var gl = $("layerLabels");
    if (!gl.childNodes.length) return;
    var zoom = HOME.w / view.w;
    var size = view.w / 46;
    var m = view.w * 0.04;   /* keep labels off the edge, where they get clipped */
    Array.prototype.forEach.call(gl.childNodes, function (t) {
      var k = t.getAttribute("data-k");
      var show = k === "city" ? zoom >= 1.8 : k === "town" ? zoom >= 3 : zoom >= 8;
      if (show) {
        var x = +t.getAttribute("x"), y = +t.getAttribute("y");
        show = x > view.x + m && x < view.x + view.w - m &&
               y > view.y + m && y < view.y + view.h - m;
      }
      t.setAttribute("font-size", size.toFixed(1));
      t.setAttribute("stroke-width", (size / 6).toFixed(2));
      t.style.display = show ? "" : "none";
    });

    var gr = $("layerRoadLabels");
    var rsize = view.w / 52;
    Array.prototype.forEach.call(gr.childNodes, function (t) {
      var km = +t.getAttribute("data-km");
      /* Short stretches only earn a name once you are close enough to read it. */
      var show = zoom >= (km >= 30 ? 1 : km >= 12 ? 2 : 3.5);
      t.setAttribute("font-size", rsize.toFixed(1));
      t.setAttribute("stroke-width", (rsize / 5).toFixed(2));
      t.style.display = show ? "" : "none";
    });
  }

  function ringPath(ring) {
    var d = "";
    for (var i = 0; i < ring.length; i++) {
      d += (i ? "L" : "M") + px(ring[i][0]).toFixed(1) + " " + py(ring[i][1]).toFixed(1);
    }
    return d + "Z";
  }
  function geomPath(g) {
    if (!g) return "";
    if (g.type === "Polygon") return g.coordinates.map(ringPath).join(" ");
    if (g.type === "MultiPolygon") {
      return g.coordinates.map(function (p) { return p.map(ringPath).join(" "); }).join(" ");
    }
    if (g.type === "LineString") {
      return g.coordinates.map(function (c, i) {
        return (i ? "L" : "M") + px(c[0]).toFixed(1) + " " + py(c[1]).toFixed(1);
      }).join("");
    }
    return "";
  }

  var NS = "http://www.w3.org/2000/svg";
  function el(name, attrs) {
    var e = document.createElementNS(NS, name);
    for (var k in attrs) if (attrs[k] != null) e.setAttribute(k, attrs[k]);
    return e;
  }

  /* --------------------------------------------------------------- drawing */
  BEL.districts.forEach(function (f) {
    var p = el("path", { d: geomPath(f.geometry), class: "district" });
    p.appendChild(el("title")).textContent = f.properties.name + " District";
    $("layerDistricts").appendChild(p);
  });

  var feederEls = {};
  BEL.feeders.slice().sort(function (a, b) {
    return RANK[feederState(a.id)] - RANK[feederState(b.id)];   // urgent drawn last
  }).forEach(function (f) {
    if (!f.g) return;
    var st = feederState(f.id);
    var isLine = f.g.type === "LineString";
    var node;
    if (f.g.type === "Point") {
      node = el("circle", {
        cx: px(f.g.coordinates[0]).toFixed(1), cy: py(f.g.coordinates[1]).toFixed(1),
        r: 7, class: "feeder " + st
      });
    } else {
      node = el("path", { d: geomPath(f.g), class: "feeder " + st + (isLine ? " line" : "") });
    }
    node.setAttribute("tabindex", "0");
    node.setAttribute("role", "button");
    node.setAttribute("data-id", f.id);
    var label = feederLabel(f);
    node.setAttribute("aria-label", label + ", " + stateWords(st));
    node.appendChild(el("title")).textContent = label + " — " + stateWords(st);
    node.addEventListener("click", function () { showFeeder(f.id); });
    node.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); showFeeder(f.id); }
    });
    feederEls[f.id] = node;
    $("layerFeeders").appendChild(node);
  });

  BEL.areas.forEach(function (a) {
    if (a.y == null) return;
    var c = el("circle", { cx: px(a.x).toFixed(1), cy: py(a.y).toFixed(1), r: 2.2, class: "dot" });
    c.appendChild(el("title")).textContent = a.n;
    $("layerPoints").appendChild(c);
  });

  /* "Independence" + "F" + "ALL" reads as "Independence FALL". */
  function feederLabel(f) {
    if (!f) return "";
    return f.f === "ALL" ? f.lc + ", all feeders" : f.lc + " Feeder " + f.f;
  }

  function stateWords(s) {
    return s === "off" ? "off now" : s === "today" ? "off later today"
      : s === "soon" ? "outage scheduled" : s === "cancelled" ? "cancelled" : "nothing listed";
  }

  /* ------------------------------------------------------------- pan, zoom */
  function zoomBy(k, cx, cy) {
    var nw = Math.min(HOME.w * 1.2, Math.max(HOME.w / 40, view.w * k));
    var nh = nw * (view.h / view.w);
    if (cx == null) { cx = view.x + view.w / 2; cy = view.y + view.h / 2; }
    view.x = cx - (cx - view.x) * (nw / view.w);
    view.y = cy - (cy - view.y) * (nh / view.h);
    view.w = nw; view.h = nh;
    applyView();
  }
  function svgPoint(evt) {
    var r = svg.getBoundingClientRect();
    return {
      x: view.x + (evt.clientX - r.left) / r.width * view.w,
      y: view.y + (evt.clientY - r.top) / r.height * view.h
    };
  }
  $("zin").onclick = function () { zoomBy(1 / 1.6); };
  $("zout").onclick = function () { zoomBy(1.6); };
  $("zfit").onclick = function () { view = Object.assign({}, HOME); applyView(); clearPin(); };

  svg.addEventListener("wheel", function (e) {
    e.preventDefault();
    var p = svgPoint(e);
    zoomBy(e.deltaY > 0 ? 1.15 : 1 / 1.15, p.x, p.y);
  }, { passive: false });

  var drag = null, pointers = {}, pinch = null;
  svg.addEventListener("pointerdown", function (e) {
    pointers[e.pointerId] = e;
    var ids = Object.keys(pointers);
    if (ids.length === 1) { drag = { p: svgPoint(e), x: view.x, y: view.y, moved: false }; }
    else if (ids.length === 2) { drag = null; pinch = spread(); }
    svg.setPointerCapture(e.pointerId);
  });
  function spread() {
    var p = Object.keys(pointers).map(function (k) { return pointers[k]; });
    return Math.hypot(p[0].clientX - p[1].clientX, p[0].clientY - p[1].clientY);
  }
  svg.addEventListener("pointermove", function (e) {
    if (!(e.pointerId in pointers)) return;
    pointers[e.pointerId] = e;
    if (pinch != null && Object.keys(pointers).length === 2) {
      var now = spread();
      if (now > 0 && pinch > 0) zoomBy(pinch / now);
      pinch = now;
      return;
    }
    if (!drag) return;
    var r = svg.getBoundingClientRect();
    var p = { x: view.x + (e.clientX - r.left) / r.width * view.w,
              y: view.y + (e.clientY - r.top) / r.height * view.h };
    var dx = p.x - drag.p.x, dy = p.y - drag.p.y;
    if (Math.abs(dx) + Math.abs(dy) > view.w / 200) drag.moved = true;
    view.x = drag.x - dx + (view.x - drag.x);
    view.y = drag.y - dy + (view.y - drag.y);
    view.x = drag.x - (p.x - drag.p.x);
    view.y = drag.y - (p.y - drag.p.y);
    applyView();
  });
  function endPointer(e) {
    delete pointers[e.pointerId];
    if (Object.keys(pointers).length < 2) pinch = null;
    if (Object.keys(pointers).length === 0) drag = null;
  }
  svg.addEventListener("pointerup", endPointer);
  svg.addEventListener("pointercancel", endPointer);

  function focusOn(lon, lat, span) {
    loadPlaces();
    span = span || HOME.w / 5;   /* enough of the surroundings to orient by */
    view.w = span; view.h = span * (HOME.h / HOME.w);
    view.x = px(lon) - view.w / 2;
    view.y = py(lat) - view.h / 2;
    applyView();
  }
  function pinAt(lon, lat) {
    clearPin();
    var g = $("layerPin"), x = px(lon), y = py(lat);
    g.appendChild(el("circle", { cx: x, cy: y, r: 11, class: "pin" }));
    g.appendChild(el("circle", { cx: x, cy: y, r: 3.2, class: "pin-core" }));
  }
  function clearPin() { $("layerPin").innerHTML = ""; }

  var selected = null;
  function select(fid) {
    if (selected && feederEls[selected]) feederEls[selected].classList.remove("sel");
    selected = fid;
    if (fid && feederEls[fid]) feederEls[fid].classList.add("sel");
  }

  /* ---------------------------------------------------------------- panels */
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function headline(st, o) {
    if (st === "off") return ['<p class="state off">OFF NOW</p>',
      '<p class="sub">Power expected back at ' + esc(pretty(o.end)) + '.</p>'].join("");
    if (st === "today") return ['<p class="state today">OFF TODAY</p>',
      '<p class="sub">' + esc(pretty(o.start)) + " to " + esc(pretty(o.end)) + '.</p>'].join("");
    if (st === "soon") return ['<p class="state soon">Outage scheduled</p>',
      '<p class="sub">' + esc(prettyDate(o.date)) + ", " + esc(pretty(o.start)) +
      " to " + esc(pretty(o.end)) + '.</p>'].join("");
    if (st === "cancelled") return ['<p class="state cancelled">Cancelled</p>',
      '<p class="sub">BEL removed this notice. It was listed for ' + esc(prettyDate(o.date)) +
      ", " + esc(pretty(o.start)) + " to " + esc(pretty(o.end)) + '.</p>'].join("");
    return "";
  }

  function outageBlock(o) {
    var rows = [
      ["Load centre", o.load_center],
      ["Feeder", o.feeder],
      ["Zone", o.zone],
      ["District", o.district],
      ["Date", (o.date || "?") + " (" + prettyDate(o.date) + ")"],
      ["Time", pretty(o.start) + " to " + pretty(o.end) + " Belize time"]
    ].map(function (r) {
      return r[1] ? "<div><dt>" + esc(r[0]) + "</dt><dd>" + esc(r[1]) + "</dd></div>" : "";
    }).join("");

    var tags = '<span class="tag ' + (o.type === "unscheduled" ? "unscheduled" : "") + '">' +
      esc(o.type) + "</span>" +
      (o.status === "cancelled" ? ' <span class="tag cancelled">cancelled</span>' : "");

    return headline(o._state, o) + tags +
      "<h3>Details</h3><dl class=\"kv\">" + rows + "</dl>" +
      (o.purpose ? "<h3>Why</h3><p class=\"kv\">" + esc(o.purpose) + "</p>" : "") +
      "<h3>What BEL actually wrote</h3><p class=\"raw\">" + esc(o.raw_text) + "</p>";
  }

  function openSheet(html) {
    $("sheetBody").innerHTML = html +
      '<p><a class="src" href="' + esc(BEL.source_url) + '" rel="noopener">Source: bel.com.bz/PowerUpdates</a></p>' +
      '<p class="caveat">Checked ' + esc(ago(BEL.generated)) +
      '. Times are as BEL printed them and can shift without notice.</p>';
    $("sheet").hidden = false;
    /* On a phone the answer lands below the map, off screen. Someone checking
       during an outage should not have to know to scroll for it. */
    if (typeof $("sheet").scrollIntoView === "function") {
      $("sheet").scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }
  $("sheetClose").onclick = function () { $("sheet").hidden = true; select(null); clearPin(); };

  function showFeeder(fid) {
    var f = feederById[fid];
    if (!f) return;
    select(fid);
    var list = (outagesByFeeder[fid] || []).slice().sort(function (a, b) {
      return RANK[b._state] - RANK[a._state] || (a.date || "").localeCompare(b.date || "");
    }).filter(function (o) { return o._state !== "past"; });

    var head = "<h2 class=\"state clear\" style=\"font-size:1.1rem\">" +
      esc(feederLabel(f)) + "</h2>" +
      '<p class="sub">' + esc(f.d || "") + " District. " +
      (f.n ? "Drawn from " + f.n + " place" + (f.n === 1 ? "" : "s") + " BEL has named"
           : "No mapped places yet") +
      (f.total ? " of " + f.total + " listed" : "") + ".</p>";

    var body = list.length
      ? list.map(outageBlock).join('<hr style="border:0;border-top:1px solid var(--line);margin:1rem 0">')
      : '<p class="state clear">No outage listed</p><p class="sub">Nothing on BEL\'s page for this feeder right now.</p>';

    var caveat = f.trunc
      ? '<p class="caveat">BEL\'s published area list for this feeder is partial, so this shape is smaller than the real service area.</p>'
      : "";
    openSheet(head + body + caveat);
  }

  function showArea(a) {
    var fids = a.f || [];
    var list = [];
    fids.forEach(function (fid) {
      (outagesByFeeder[fid] || []).forEach(function (o) {
        if (list.indexOf(o) === -1 && o._state !== "past") list.push(o);
      });
    });
    list.sort(function (x, y) { return RANK[y._state] - RANK[x._state]; });

    if (a.y != null) { focusOn(a.x, a.y); pinAt(a.x, a.y); } else { clearPin(); }
    select(fids[0] || null);

    var head = '<h2 class="state clear" style="font-size:1.1rem">' + esc(a.n) + "</h2>" +
      '<p class="sub">' + esc(a.d || "") + (a.d ? " District" : "") +
      (a.y == null ? ". No map location for this one, so it is not drawn." : "") + "</p>";

    if (!list.length) {
      openSheet(head +
        '<p class="state clear">No outage listed for this area</p>' +
        '<p class="sub">Nothing on BEL\'s page that names ' + esc(a.n) + " or its feeder.</p>" +
        (fids.length ? '<p class="caveat">Seen on: ' + esc(fids.map(function (i) {
          var f = feederById[i]; return f ? feederLabel(f) : i;
        }).join(", ")) + ".</p>" : ""));
      return;
    }
    openSheet(head + list.map(outageBlock).join(
      '<hr style="border:0;border-top:1px solid var(--line);margin:1rem 0">'));
  }

  /* A street from the OpenStreetMap index. We know the town it is in, and we do
     not know which feeder serves it, because BEL does not publish that. The
     panel says so rather than picking a feeder and hoping. */
  function showStreet(s) {
    var lc = lcByName[s.lc];
    var head = '<h2 class="state clear" style="font-size:1.1rem">' + esc(s.n) + "</h2>";

    if (!lc) {
      clearPin(); select(null);
      openSheet(head +
        '<p class="sub">Not near any BEL load centre in the data.</p>' +
        '<p class="state clear">No outage information</p>' +
        '<p class="sub">This road is more than 30km from the nearest load centre, ' +
        'so there is nothing here to match it against.</p>');
      return;
    }

    var fids = lc.f || [], list = [];
    fids.forEach(function (fid) {
      (outagesByFeeder[fid] || []).forEach(function (o) {
        if (list.indexOf(o) === -1 && o._state !== "past") list.push(o);
      });
    });
    list.sort(function (x, y) { return RANK[y._state] - RANK[x._state]; });

    if (lc.y != null) { focusOn(lc.x, lc.y); pinAt(lc.x, lc.y); }
    select(fids[0] || null);

    head += '<p class="sub">' + esc(lc.n) + (lc.d ? ", " + esc(lc.d) + " District" : "") + "</p>";

    var caveat = '<p class="caveat"><strong>BEL has not named this street in a notice.</strong> ' +
      "Showing everything listed for " + esc(lc.n) +
      ", which is " + fids.length + " feeder" + (fids.length === 1 ? "" : "s") +
      ". Your street may be on any of them, so treat this as a warning to check, " +
      "not a confirmation.</p>";

    if (!list.length) {
      openSheet(head +
        '<p class="state clear">No outage listed for ' + esc(lc.n) + "</p>" +
        '<p class="sub">Nothing on BEL\'s page for any ' + esc(lc.n) + " feeder right now.</p>" +
        caveat);
      return;
    }
    openSheet(head + caveat + list.map(outageBlock).join(
      '<hr style="border:0;border-top:1px solid var(--line);margin:1rem 0">'));
  }

  /* ---------------------------------------------------------------- search */
  var fuse = new Fuse(BEL.areas, {
    keys: [{ name: "n", weight: 3 }, { name: "a", weight: 2 }, { name: "d", weight: 0.5 }],
    threshold: 0.38, ignoreLocation: true, minMatchCharLength: 2, includeScore: true
  });

  /* Street index.
     BEL's notices name roughly 40 streets in the whole country, so a search for
     any ordinary address found nothing. The OpenStreetMap index covers about
     3,400 roads, but it is 63KB, and most visits are a glance at the map that
     never needs it. So it loads on the first keystroke, not on page load. */
  var streetFuse = null, streetState = "idle";

  function loadStreets(then) {
    if (streetState === "ready") { then && then(); return; }
    if (streetState === "loading") return;
    streetState = "loading";
    var s = document.createElement("script");
    s.src = "data/streets.js";          /* a script tag, so file:// works too */
    s.onload = function () {
      var flat = [], byLc = window.BEL_STREETS || {};
      Object.keys(byLc).forEach(function (lc) {
        byLc[lc].forEach(function (name) { flat.push({ n: name, lc: lc, _street: 1 }); });
      });
      streetFuse = new Fuse(flat, {
        keys: [{ name: "n", weight: 3 }, { name: "lc", weight: 0.4 }],
        threshold: 0.32, ignoreLocation: true, minMatchCharLength: 3, includeScore: true
      });
      streetState = "ready";
      then && then();
    };
    s.onerror = function () { streetState = "failed"; then && then(); };
    document.head.appendChild(s);
  }

  var lcByName = {};
  BEL.areas.forEach(function (a) { if (a.lc) lcByName[a.n] = a; });

  function combinedSearch(v) {
    var hits = fuse.search(v, { limit: 8 }).map(function (r) {
      return { item: r.item, score: r.score };
    });
    if (streetFuse) {
      /* Nudge streets slightly behind places at equal score: a village called
         X is a more likely target than a road called X. */
      streetFuse.search(v, { limit: 8 }).forEach(function (r) {
        hits.push({ item: r.item, score: r.score + 0.02 });
      });
    }
    hits.sort(function (a, b) { return a.score - b.score; });
    var out = [], seen = {};
    hits.forEach(function (h) {
      var k = (h.item._street ? "s:" : "a:") + h.item.n + "|" + (h.item.lc || h.item.d || "");
      if (!seen[k]) { seen[k] = 1; out.push(h.item); }
    });
    return out.slice(0, 8);
  }

  var q = $("q"), results = $("results"), cur = -1, shown = [];

  function renderResults(items) {
    shown = items; cur = -1;
    if (!items.length) {
      results.innerHTML = '<li class="none">Nothing matches that name.</li>';
      results.hidden = false; q.setAttribute("aria-expanded", "true");
      return;
    }
    results.innerHTML = items.map(function (a, i) {
      var fids = a._street ? ((lcByName[a.lc] || {}).f || []) : (a.f || []);
      var st = "none";
      fids.forEach(function (fid) {
        var s = feederState(fid); if (RANK[s] > RANK[st]) st = s;
      });
      var badge = st === "off" ? "Off now" : st === "today" ? "Off today"
        : st === "soon" ? "Scheduled" : "";
      var where = a._street ? (a.lc || "no nearby town") : (a.d || "");
      return '<li role="option" id="opt' + i + '" data-i="' + i + '" aria-selected="false">' +
        "<span>" + esc(a.n) + "</span><span class=\"where\">" +
        (badge ? esc(badge) + " &middot; " : "") + esc(where) + "</span></li>";
    }).join("");
    results.hidden = false;
    q.setAttribute("aria-expanded", "true");
  }
  function closeResults() {
    results.hidden = true; q.setAttribute("aria-expanded", "false"); cur = -1;
  }
  function highlight(i) {
    Array.prototype.forEach.call(results.children, function (li, n) {
      li.setAttribute("aria-selected", n === i ? "true" : "false");
    });
    if (i >= 0 && results.children[i]) results.children[i].scrollIntoView({ block: "nearest" });
  }
  function choose(i) {
    var a = shown[i];
    if (!a) return;
    q.value = a.n;
    closeResults();
    if (a._street) showStreet(a); else showArea(a);
  }

  q.addEventListener("input", function () {
    var v = q.value.trim();
    $("clear").hidden = !v;
    if (v.length < 2) { closeResults(); return; }
    /* Fetch the street index on the first keystroke and re-run the search when
       it lands, so the user does not have to type again to see their road. */
    loadStreets(function () {
      if (q.value.trim().length >= 2) renderResults(combinedSearch(q.value.trim()));
    });
    renderResults(combinedSearch(v));
  });
  q.addEventListener("keydown", function (e) {
    if (results.hidden) return;
    if (e.key === "ArrowDown") { e.preventDefault(); cur = Math.min(cur + 1, shown.length - 1); highlight(cur); }
    else if (e.key === "ArrowUp") { e.preventDefault(); cur = Math.max(cur - 1, 0); highlight(cur); }
    else if (e.key === "Enter") { e.preventDefault(); choose(cur < 0 ? 0 : cur); }
    else if (e.key === "Escape") { closeResults(); }
  });
  results.addEventListener("click", function (e) {
    var li = e.target.closest("li[data-i]");
    if (li) choose(parseInt(li.getAttribute("data-i"), 10));
  });
  $("clear").onclick = function () {
    q.value = ""; $("clear").hidden = true; closeResults(); q.focus();
    $("sheet").hidden = true; clearPin(); select(null);
  };
  document.addEventListener("click", function (e) {
    if (!e.target.closest(".search")) closeResults();
  });

  /* ------------------------------------------------------------- freshness */
  var offCount = BEL.outages.filter(function (o) { return o._state === "off"; }).length;
  var todayCount = BEL.outages.filter(function (o) { return o._state === "today"; }).length;
  $("freshness").textContent =
    (offCount ? offCount + " outage" + (offCount === 1 ? "" : "s") + " running now. "
      : todayCount ? todayCount + " outage" + (todayCount === 1 ? "" : "s") + " later today. "
      : BEL.outages.length ? BEL.outages.length + " outage" + (BEL.outages.length === 1 ? "" : "s") + " listed. "
      : "Nothing listed right now. ") +
    "Checked " + ago(BEL.generated) + ".";

  if (hoursSince(BEL.generated) > 12) {
    $("staleWhen").textContent = "Last checked " + ago(BEL.generated) + ".";
    $("stale").hidden = false;
  }
  $("srcLink").href = BEL.source_url;
  $("staleLink").href = BEL.source_url;

  /* Pull the roads and outlines in once the page has settled. Belize's map is
     hard to read without its highways on it, and BEL describes outage areas in
     terms of them, so this is worth 18KB gzipped shortly after first paint
     rather than only on zoom. First paint still happens without it. */
  if (window.requestIdleCallback) {
    window.requestIdleCallback(loadPlaces, { timeout: 2500 });
  } else {
    setTimeout(loadPlaces, 1200);
  }
})();
