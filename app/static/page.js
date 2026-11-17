// Opening, drawing and saving a score. The other scripts load after this one, and editor.js starts the page.
const $ = id => document.getElementById(id);
const UNSURE_BELOW = 0.8;
let scoreId = null, score = null, selected = null, editing = null;
let sheets = []; // What the library lists: scores, and sheets still being read or that couldn't be.
let opened = 0; // Counts the scores opened and closed, so an answer about one that's gone is dropped.
let queue = Promise.resolve();
const UNITS_PER_SPACE = 250; // The music font's outlines count 1000 to four staff spaces.

// Lay music symbols side by side, and return their outlines with the box around them all.
function setGlyphs(codes) {
  let pen = 0, left = Infinity, right = -Infinity, top = -Infinity, bottom = Infinity, paths = "";
  for (const code of codes.split(" ")) {
    const [outline, x, y, width, height, advance] = GLYPHS[code];
    paths += `<path transform="translate(${pen} 0) scale(1 -1)" d="${outline}"/>`;
    left = Math.min(left, pen + x);
    right = Math.max(right, pen + x + width);
    top = Math.max(top, y + height);
    bottom = Math.min(bottom, y);
    pen += advance;
  }
  return { paths, left, right, top, bottom, advance: pen };
}

// A small picture of music symbols, drawn `space` pixels to the staff space. Symbols given the
// same `frame` share a baseline, so a row of notes stands on one line.
function glyphIcon(codes, space = 5, frame = null) {
  const set = setGlyphs(codes);
  const top = frame?.top ?? set.top, bottom = frame?.bottom ?? set.bottom;
  const scale = space / UNITS_PER_SPACE;
  const width = set.right - set.left, height = top - bottom;
  return `<svg class="glyphs" width="${(width * scale).toFixed(1)}" height="${(height * scale).toFixed(1)}" viewBox="${set.left} ${-top} ${width} ${height}" aria-hidden="true">${set.paths}</svg>`;
}

// Edits run one at a time, in the order they were asked for. Anything that changes where the
// next edit lands, such as a note length or a cursor move, has to wait its turn here too, or a
// quick typist's choice reaches notes that are still waiting to be sent.
function enqueue(task) {
  queue = queue.then(task).catch(error => message(error.message));
  return queue;
}

// Every request to the server comes through here, and gives the answer or throws a short reason:
// the server's own when it refused, as "That file is too large", or one of these. A request that
// never reached it showed "Failed to fetch", and a plain-text 500 "Unexpected token 'I'…", or
// nothing at all. `status` is 0 when nothing answered.
const NOT_ANSWERING = "xrnsheet isn't answering.";
const DIDNT_WORK = "That didn't work.";

class RequestFailed extends Error {
  constructor(text, status) {
    super(text);
    this.status = status;
  }
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new RequestFailed(NOT_ANSWERING, 0);
  }
  const data = await response.json().catch(() => undefined);
  if (!response.ok) throw new RequestFailed(typeof data?.detail === "string" ? data.detail : DIDNT_WORK, response.status);
  if (data === undefined) throw new RequestFailed(DIDNT_WORK, response.status);
  return data;
}

function postJson(path, body) {
  return request(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
}

// The window is the library while no score is open, the score once one is, and how a sheet is getting
// on while one still being read, or that couldn't be, is open. That doesn't hold up the rest of the
// app: the library can be shown beside it, as beside a score, and anything else opened.
function show(view) {
  const open = Boolean(score && scoreId);
  $("busy").hidden = view !== "busy";
  $("workspace").hidden = view !== "workspace";
  document.body.classList.toggle("atHome", view === "start");
  document.body.classList.toggle("busy", view === "busy");
  document.body.classList.toggle("hasScore", open);
  $("libraryToggle").setAttribute("aria-pressed", String(libraryShown()));
  if (view === "workspace") {
    $("original").hidden = score.blank || originalHidden();
    // A zoom that fits the page depends on how much room the pages have, which is only known now.
    restoreZoom();
    drawCursor();
  } else {
    stopPlaying();
    setInput(false);
  }
  drawDock();
  if (view === "start") drawHistory();
}

// A tile in the library: the score as a link to open it, and a ⋮ button, or a right-click, for
// what else can be done with it, such as Delete.
function tileFor(sheet) {
  const holder = document.createElement("div");
  holder.className = "tileBox";
  holder.setAttribute("role", "listitem");
  const link = document.createElement("a");
  link.className = "tile";
  link.href = `#${sheet.id}`;
  link.dataset.score = sheet.id;
  if (sheet.id === scoreId) link.setAttribute("aria-current", "true");
  link.addEventListener("click", event => { event.preventDefault(); if (sheet.id !== scoreId) openScore(sheet.id); closeDrawers(); });
  const more = document.createElement("button");
  more.type = "button";
  more.className = "tileMenu flat";
  more.setAttribute("aria-haspopup", "menu");
  more.innerHTML = icon("dots");
  more.addEventListener("click", () => { if (more.getAttribute("aria-expanded") === "true") closeMenus(); else openMenu(more, tileItems(sheet)); });
  holder.addEventListener("contextmenu", event => { event.preventDefault(); openMenuAt(event.clientX, event.clientY, tileItems(sheet)); });
  holder.append(link, more);
  if (sheet.status && sheet.status !== "ready") jobTile(sheet, holder, link);
  else drawScoreTile(sheet, link);
  more.setAttribute("aria-label", `More for ${link.querySelector(".name").textContent}`);
  more.title = "More: open or delete";
  return holder;
}

function tileItems(sheet) {
  const open = { label: "Open", run: () => { if (sheet.id !== scoreId) openScore(sheet.id); closeDrawers(); } };
  if (sheet.status === "processing") return [open, null, { label: "Cancel reading", run: () => removeSheet(sheet.id) }];
  if (sheet.status === "failed") return [open, { label: "Try again", run: () => readAgain(sheet.id) }, null, { label: "Remove", run: () => removeSheet(sheet.id) }];
  return [open, null, { label: "Delete", run: () => removeSheet(sheet.id) }];
}

function drawScoreTile(sheet, link) {
  const picture = document.createElement("span");
  picture.className = "sheet";
  const image = new Image();
  image.alt = "";
  image.loading = "lazy";
  image.decoding = "async";
  image.src = `/api/scores/${sheet.id}/thumbnail.svg?${sheet.saved}`;
  picture.append(image);
  const when = new Date(sheet.saved * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  const name = document.createElement("span");
  name.className = "name";
  name.textContent = sheet.title || "Untitled score";
  const state = document.createElement("span");
  state.className = "state";
  const made = sheet.opened ? "opened from a file" : sheet.blank ? "written here" : `read from ${sheet.pages} ${sheet.pages === 1 ? "page" : "pages"}, ${sheet.checked ? "checked" : "not checked"}`;
  state.textContent = `${when} · ${made}`;
  link.title = `${name.textContent}${sheet.composer ? `, by ${sheet.composer}` : ""}, ${state.textContent}`;
  link.append(picture, name, state);
}

// The name a sheet goes by until it's read: the file it came from.
function sheetName(sheet) {
  return sheet.name || (sheet.pdf ? "A PDF" : "A photo");
}

function elapsedText(seconds) {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

// How long a sheet has been read for, counted on from what the server last said.
function readingFor(sheet) {
  return typeof sheet.elapsed === "number" ? sheet.elapsed + (performance.now() - sheet.heard) / 1000 : null;
}

// What a sheet still to be read is doing, in a few words: waiting its turn, or being read, and for how long.
function readingState(sheet) {
  if (sheet.ahead > 0) return `Waiting: ${sheet.ahead} ${sheet.ahead === 1 ? "sheet" : "sheets"} ahead`;
  const doing = (sheet.message || "Reading").replace(/\.$/, "");
  const time = readingFor(sheet);
  return time === null ? doing : `${doing} · ${elapsedText(time)}`;
}

// A sheet that isn't a score yet: waiting its turn or being read, with Cancel, or one that couldn't be
// read, saying why, with Try again and Remove. It opens like a score, into how it's getting on.
function jobTile(sheet, holder, link) {
  const failed = sheet.status === "failed";
  holder.classList.add("jobTile");
  holder.dataset.state = failed ? "failed" : sheet.ahead > 0 ? "waiting" : "reading";
  const picture = document.createElement("span");
  picture.className = "sheet";
  picture.innerHTML = failed ? icon("alert") : sheet.ahead > 0 ? icon("clock") : `<span class="spinner"></span>`;
  const name = document.createElement("span");
  name.className = "name";
  name.textContent = sheetName(sheet);
  const state = document.createElement("span");
  state.className = "state";
  state.textContent = failed ? `Couldn't be read. ${sheet.message || ""}`.trim() : readingState(sheet);
  link.title = `${name.textContent}: ${state.textContent}`;
  link.append(picture, name, state);
  const actions = document.createElement("span");
  actions.className = "actions";
  const button = (label, title, run) => {
    const made = document.createElement("button");
    made.type = "button";
    made.textContent = label;
    made.title = title;
    made.addEventListener("click", () => run(sheet.id));
    return made;
  };
  if (failed) actions.append(button("Try again", "Read this sheet again", readAgain), button("Remove", "Remove it from the library", removeSheet));
  else actions.append(button("Cancel", "Stop reading this sheet and remove it", removeSheet));
  holder.append(actions);
}

// The library's picture of the open score follows its edits, a moment after the last one, and so
// does when it was saved. Hidden, the library is listed afresh when it's shown.
let tileTimer = 0;
const TILE_DELAY_MS = 1200;

function refreshTile() {
  clearTimeout(tileTimer);
  const id = scoreId;
  tileTimer = setTimeout(() => {
    if (id !== scoreId || !libraryShown()) return;
    const tile = document.querySelector(`#historyList a[data-score="${id}"]`);
    if (!tile) { drawHistory(); return; }
    const now = Date.now() / 1000;
    tile.querySelector("img").src = `/api/scores/${id}/thumbnail.svg?${now}`;
    const sheet = sheets.find(item => item.id === id);
    if (sheet) { sheet.saved = now; sheet.title = score?.title ?? sheet.title; sheet.composer = score?.composer ?? sheet.composer; }
    const state = tile.querySelector(".state");
    if (sheet && state) state.textContent = tileFor(sheet).querySelector(".state").textContent;
  }, TILE_DELAY_MS);
}

// A tile that would be drawn just as it was is kept, as the library is listed afresh every few
// seconds while a sheet is read, and drawing every picture again each time would make them flicker.
let tiles = new Map();

function drawTiles() {
  const wanted = $("homeSearch").value.trim().toLowerCase();
  const create = document.createElement("button");
  create.className = "tile";
  create.id = "newTile";
  create.innerHTML = `<span class="sheet">${icon("plus")}</span><span class="name">New score</span><span class="state">Piano, guitar, violin or anything</span>`;
  create.addEventListener("click", openNewScore);
  // A score is found by its title or by who wrote it, as a musician asks for "the Joplin".
  const called = sheet => sheet.status && sheet.status !== "ready" ? sheetName(sheet) : `${sheet.title || "Untitled score"} ${sheet.composer ?? ""}`;
  const shown = sheets.filter(sheet => !wanted || called(sheet).toLowerCase().includes(wanted));
  const drawn = new Map();
  $("historyList").replaceChildren(...(wanted ? [] : [create]), ...shown.map(sheet => {
    const { elapsed, heard, ...said } = sheet;
    const key = JSON.stringify([said, sheet.id === scoreId]);
    const tile = tiles.get(key) ?? tileFor(sheet);
    drawn.set(key, tile);
    return tile;
  }));
  tiles = drawn;
  $("libraryEmpty").hidden = sheets.length > 0;
  // A search that finds nothing says so, rather than leaving an empty space that looks like no library.
  $("libraryNone").hidden = !wanted || shown.length > 0 || !sheets.length;
  $("libraryNone").textContent = `No score in your library is called “${$("homeSearch").value.trim()}” or is by anyone of that name.`;
}

let listed = 0; // Counts the times the library is asked for, so an answer overtaken by a later one is dropped.

async function drawHistory() {
  const asked = ++listed;
  let found;
  try {
    found = await request("/api/scores");
  } catch {
    if (asked === listed) watchReading(true); // A sheet being read is asked about again, later.
    return;
  }
  if (asked !== listed) return;
  const before = new Map(sheets.map(sheet => [sheet.id, sheet.status]));
  const heard = performance.now();
  sheets = found.map(sheet => ({ ...sheet, heard }));
  drawTiles();
  tellWhatFinished(before);
  watchReading();
}

// A sheet that has been read while something else was open says so, and so does one that couldn't be.
function tellWhatFinished(before) {
  for (const sheet of sheets) {
    if (before.get(sheet.id) !== "processing" || sheet.status === "processing" || sheet.id === scoreId) continue;
    if (sheet.status === "ready") toast(`${sheet.title || sheet.name || "Your sheet"} has been read.`, { label: "Open", run: () => openScore(sheet.id) });
    else toast(`${sheetName(sheet)} couldn't be read. ${sheet.message || ""}`.trim(), { label: "Try again", run: () => readAgain(sheet.id) });
  }
}

// While a sheet is being read the library is asked again every few seconds, wherever the window is, so
// its tile keeps up and the sheet can say when it's done.
// While the server doesn't answer, it's asked less often each time, up to every half a minute.
let watchTimer = 0, watchWait = 0;
const WATCH_MS = 2000, LONGEST_WAIT_MS = 30000;

function watchReading(unanswered = false) {
  clearTimeout(watchTimer);
  watchWait = unanswered ? Math.min(Math.max(watchWait, WATCH_MS) * 2, LONGEST_WAIT_MS) : WATCH_MS;
  if (sheets.some(sheet => sheet.status === "processing")) watchTimer = setTimeout(drawHistory, watchWait);
}

// The time a sheet has been read for counts up on its tile, and where it's open.
function tickReading() {
  for (const sheet of sheets) {
    if (sheet.status !== "processing" || sheet.ahead !== 0) continue;
    const state = document.querySelector(`#historyList a[data-score="${sheet.id}"] .state`);
    if (state) state.textContent = readingState(sheet);
  }
  if (!$("busy").hidden) drawReadingTime();
}

let toastTimer = 0;

// A note that something has happened, with one thing to do about it, such as Undo, for a few seconds.
function toast(text, action = null) {
  clearTimeout(toastTimer);
  $("toastText").textContent = text;
  $("toastAction").hidden = !action;
  $("toastAction").textContent = action?.label ?? "";
  $("toastAction").onclick = action ? () => { hideToast(); action.run(); } : null;
  $("toast").hidden = false;
  toastTimer = setTimeout(hideToast, action ? 10000 : 5000);
}

function hideToast() {
  clearTimeout(toastTimer);
  $("toast").hidden = true;
}

function message(text) {
  $("message").textContent = text || "";
  $("message").hidden = !text;
}

function openScore(id) {
  opened++;
  forgetPlayback();
  // What was being written or chosen belongs to the score that was open. Notes are named the same
  // way in every score, note0, note1 and so on, so a cursor or a selection kept would land on other
  // notes in this one, and letters typed straight away would be written there.
  setInput(false);
  select(null);
  // It opens at its beginning, with its title in sight. The desk would otherwise keep the scroll it
  // had in the last score, as far down as this one goes, cutting its first system under the top bar.
  $("desk").scrollTop = $("desk").scrollLeft = 0;
  foldLibrary();
  scoreId = id;
  score = null;
  selected = null;
  chosenPart = null;
  barStep = chordStep = null; // The steps through what's to check were through the last score's.
  location.hash = id;
  document.querySelectorAll("#historyList .tile").forEach(tile => tile.toggleAttribute("aria-current", tile.dataset.score === id));
  return poll();
}

// The score stays where it is, as it's saved with every edit, and the library lists it. The library
// is an entry of its own in the window's history, so Back opens the score again, unless the score
// has gone, which takes its entry with it.
function closeScore(gone = false) {
  opened++;
  forgetPlayback();
  if (location.hash) history[gone ? "replaceState" : "pushState"](null, "", location.pathname + location.search);
  scoreId = null;
  score = null;
  selected = null;
  message("");
  show("start");
}

// Photos chosen or dropped together are the pages of one sheet, in the order of their names, so
// "page 2" comes before "page 10" whatever order they were picked or dropped in. A PDF or a MusicXML
// file is a score of its own. Only the first file of several used to be opened.
const OWN_SCORE = /\.(pdf|musicxml|xml|mxl)$/i;

function sentTogether(files) {
  const photos = files.filter(file => !OWN_SCORE.test(file.name)).sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
  return [...(photos.length ? [photos] : []), ...files.filter(file => OWN_SCORE.test(file.name)).map(file => [file])];
}

// The first score opens, and the others are in the library.
async function upload(files) {
  const groups = sentTogether([...(files ?? [])]);
  if (!groups.length) return;
  message("");
  const failed = [];
  let first = null;
  for (const group of groups) {
    toast(group.length > 1 ? `Opening ${group.length} pages…` : `Opening ${group[0].name}…`);
    const body = new FormData();
    for (const file of group) body.append("file", file);
    try {
      const made = await request("/api/scores", { method: "POST", body });
      first ??= made.id;
    } catch (error) {
      failed.push(`${group[0].name}: ${error.message}`); // Said where it stays, with the file's name.
    }
  }
  hideToast();
  if (first) await openScore(first);
  if (failed.length) message(failed.join(" "));
  drawHistory(); // A photo or a PDF is listed at once, as it's read.
}

// A sheet still to be read, or one that couldn't be, as it's shown open: its place in the line or how
// long it has been read for, and what can be done with it.
let shownSheet = null;

function showReading(data) {
  shownSheet = { ...data, heard: performance.now() };
  const failed = data.status === "failed";
  $("readingName").textContent = sheetName(data);
  $("readingIcon").innerHTML = failed ? icon("alert") : data.ahead > 0 ? icon("clock") : `<div class="spinner"></div>`;
  $("readingIcon").classList.toggle("failed", failed);
  $("busyText").textContent = failed ? `This sheet couldn't be read. ${data.message || ""}`.trim()
    : data.ahead > 0 ? `Waiting its turn, behind ${data.ahead} ${data.ahead === 1 ? "sheet" : "sheets"}: sheets are read one at a time.`
    : data.message || "Reading your sheet.";
  $("readingHint").hidden = failed;
  $("readingRetry").hidden = $("readingRemove").hidden = !failed;
  $("readingCancel").hidden = failed;
  drawReadingTime();
  show("busy");
}

function drawReadingTime() {
  const time = shownSheet?.status === "processing" ? readingFor(shownSheet) : null;
  $("readingTime").textContent = time === null ? "" : `${elapsedText(time)} so far. A page usually takes under a minute.`;
}

// The one way anything leaves the library: Delete on a score, Remove on a sheet that couldn't be
// read, Cancel on one being read, whose reader the server stops. Nothing asks first: a score or a
// failed sheet goes to the server's trash, and Undo brings it back, open again if it was open.
async function removeSheet(id) {
  const sheet = sheets.find(item => item.id === id);
  const named = !sheet || sheet.status === "ready" ? `“${(sheet ?? score)?.title || "Untitled score"}”` : sheetName(sheet);
  const open = id === scoreId && Boolean(score);
  let data;
  try {
    data = await request(`/api/scores/${id}`, { method: "DELETE" });
  } catch (error) {
    message(error.message);
    drawHistory();
    return;
  }
  if (id === scoreId) closeScore(true);
  sheets = sheets.filter(item => item.id !== id);
  drawTiles();
  drawHistory();
  if (data.undo) toast(`${named} ${sheet?.status === "failed" ? "removed" : "deleted"}.`, { label: "Undo", run: () => restoreSheet(id, open) });
}

async function restoreSheet(id, reopen) {
  try {
    await postJson(`/api/scores/${id}/restore`);
  } catch (error) {
    message(error.message);
    return;
  }
  await drawHistory();
  if (reopen) openScore(id);
}

async function readAgain(id) {
  try {
    await postJson(`/api/scores/${id}/retry`);
    if (id === scoreId && !score) poll();
  } catch (error) {
    message(error.message);
  }
  drawHistory();
}

// A sheet being read is asked about every 1.5 s. When the server doesn't answer, or answers with a
// fault, as while it restarts, it's asked again, less often each time, rather than given up on with
// its spinner turning for ever; and a score being opened is asked for again the same way.
const POLL_MS = 1500;
let pollTrouble = false;

async function poll(wait = POLL_MS) {
  const turn = opened;
  let data;
  try {
    data = await request(`/api/scores/${scoreId}`);
  } catch (error) {
    if (turn !== opened) return; // Something else has been opened meanwhile.
    if (error.status === 404) { closeScore(true); drawHistory(); return; }
    pollTrouble = true;
    message(error.message);
    setTimeout(() => { if (turn === opened) poll(Math.min(wait * 2, LONGEST_WAIT_MS)); }, wait);
    return;
  }
  if (turn !== opened) return;
  if (pollTrouble) { pollTrouble = false; message(""); }
  if (data.status !== "ready") {
    showReading(data);
    if (data.status === "processing") setTimeout(() => { if (turn === opened) poll(); }, POLL_MS);
    return;
  }
  shownSheet = null;
  message(data.message);
  score = data;
  drawOriginals();
  $("xml").href = `/api/scores/${scoreId}/score.musicxml`;
  $("midi").href = `/api/scores/${scoreId}/score.mid`;
  $("pdf").href = $("pdfSave").href = `/api/scores/${scoreId}/score.pdf`;
  $("pdf").target = "_blank";
  const opening = $("workspace").hidden;
  show("workspace");
  draw();
  // The library beside it lists it, and says it's the one open.
  if (opening && libraryShown()) drawHistory();
}

// The names Verovio makes up for what it draws differ every time a page is drawn: the page's own,
// which its style sheet is written for, and those of everything without a name in the score. The
// score's own names, of its notes, measures and marks, don't, so two drawings that differ in nothing
// else are the same page.
const MADE_UP_NAMES = /<style[^>]*>[^<]*<\/style>| id="(?!(?:note|mark|m|p\d+m)\d+")[^"]*"|xlink:href="#[^"]*"|\bid-(?!(?:note|mark|m|p\d+m)\d+\b)[\w-]+|(?<=Milestone)(?:End|Start)? [\w-]+/g;
// What each page on the desk was drawn from, so a page an edit didn't change is left as it is.
let drawnPages = [];

// Only the pages an edit changed are drawn again. Laying out a page of music takes the browser far
// longer than anything else a redraw does, and most edits change one page.
function drawPages(fresh = false) {
  const holder = $("pages");
  const spare = new Map();
  for (const drawn of fresh ? [] : drawnPages) spare.set(drawn.key, [...(spare.get(drawn.key) ?? []), drawn.element]);
  drawnPages = score.pages.map((svg, index) => {
    const key = svg.replace(MADE_UP_NAMES, "");
    // Each page as wide as it prints, which in a continuous view is as wide as its music.
    const width = `calc(${score.pageSizes?.[index]?.[0] ?? 210}mm * var(--zoom))`;
    let element = spare.get(key)?.shift();
    if (!element) {
      element = document.createElement("div");
      element.className = "page";
      element.innerHTML = svg;
    }
    if (element.style.width !== width) element.style.width = width;
    return { key, element };
  });
  // The pages that changed go first, then the new ones go in their places. Moving a page that
  // stays would make the browser lay it out again.
  const kept = new Set(drawnPages.map(drawn => drawn.element));
  for (const child of [...holder.children]) if (!kept.has(child)) child.remove();
  drawnPages.forEach(({ element }, index) => { if (holder.children[index] !== element) holder.insertBefore(element, holder.children[index] ?? null); });
  // A line carried over a page break names its start by an id the drawing made up, which differs
  // from drawing to drawing, so if a kept page and a new one no longer agree, every page is new.
  const orphaned = [...holder.querySelectorAll("g.spanning")].some(piece => {
    const start = [...piece.classList].find(name => name.startsWith("id-"));
    return start && !document.getElementById(start.slice(3));
  });
  if (orphaned && !fresh) { drawPages(true); return; }
  // A page that stayed still shows what was selected or playing when it was drawn.
  holder.querySelectorAll(".selected, .playing, .awaited, .unsure, .active, .early, .late, .onTime, .short").forEach(element => element.classList.remove("selected", "voice2", "voice3", "voice4", "playing", "awaited", "unsure", "active", "early", "late", "onTime", "short"));
}

function draw() {
  drawPages();
  $("viewMode").value = score.view ?? "page";
  document.body.dataset.view = score.view ?? "page";
  if (fittedZoom()) setZoom(zoom);
  $("undo").disabled = !score.canUndo;
  $("concertPitch").setAttribute("aria-pressed", String(Boolean(score.concertPitch)));
  $("redo").disabled = !score.canRedo;
  $("checked").textContent = score.checked ? "Checked ✓" : "Mark as checked";
  $("checked").classList.toggle("done", score.checked);
  $("checked").title = score.checked ? "Checked: its chords are learnt from as right. Press to un-mark it, so they aren't." : "Press this once you've fixed everything on this sheet. Checked sheets become trusted answers for retraining and for your real-world score.";
  const shown = [...document.querySelectorAll("#pages g.harm")];
  score.chords.forEach((chord, index) => {
    const element = shown[index];
    if (!element) return;
    element.dataset.index = index;
    if (chord.confidence !== null && chord.confidence < UNSURE_BELOW) element.classList.add("unsure");
  });
  tagMarks();
  drawParts();
  drawBoxes();
  drawChecks();
  drawBarChecks();
  drawTitle();
  if (selected) {
    const element = document.getElementById(selected);
    if (element) markSelected(element); else selected = null;
  }
  if (selectedMark) {
    const pieces = markElements(selectedMark);
    pieces.forEach(piece => piece.classList.add("selected"));
    if (!pieces.length) selectedMark = null;
  }
  drawRange();
  if (dockOpen("mixer")) drawMixer();
  if (dockOpen("timeline")) drawTimeline();
  if (instrumentShown()) drawInstrument(); // A new tuning or capo changes the neck.
  drawPartSwitch();
  drawPracticeChoices();
  refreshFollower();
  drawPageCount();
  drawCursor();
  drawStickyStart();
  updateTools();
  refreshPosition();
  // The page under the pointer has changed, and so has what a click there would write.
  if (pointer) drawGhost(pointer.x, pointer.y, pointer.shift);
}

function drawOriginals() {
  setSheetZoom(1);
  sheetPlaces = {};
  $("images").replaceChildren(...score.sourcePages.map(number => {
    const frame = document.createElement("div");
    frame.className = "imageBox";
    frame.dataset.page = number;
    const image = new Image();
    image.alt = `Page ${number} of your sheet`;
    image.draggable = false;
    image.addEventListener("load", drawBoxes);
    image.addEventListener("load", () => { if (number === score?.sourcePages?.[0]) layOutOriginal(image); });
    image.src = `/api/scores/${scoreId}/source/${number}.jpg`;
    frame.append(image);
    loadPlaces(scoreId, number);
    return frame;
  }));
}

// Where each bar and beat is on each page of the original, as the reader found them, to add a chord
// it missed where it's clicked. A page read before they were kept has none.
let sheetPlaces = {};

async function loadPlaces(id, number) {
  const found = await request(`/api/scores/${id}/source/${number}/layout`).catch(() => null);
  if (id === scoreId) sheetPlaces[number] = found?.lines ?? [];
}

// The original is zoomed by the wheel with Ctrl, as the pages are, by a pinch, or by a double-click
// on a place and again to fit; dragged to move about; and given more room by dragging its edge.
let sheetZoom = 1;
const SHEET_ZOOMS = [1, 1.5, 2, 3, 4, 6];

function setSheetZoom(value, at = null) {
  const holder = $("images");
  const next = Math.min(SHEET_ZOOMS.at(-1), Math.max(1, value));
  const area = holder.getBoundingClientRect();
  const x = at ? at.x - area.left : holder.clientWidth / 2, y = at ? at.y - area.top : holder.clientHeight / 2;
  // The place under the pointer stays under it.
  const left = holder.scrollLeft + x, top = holder.scrollTop + y, ratio = next / sheetZoom;
  sheetZoom = next;
  holder.style.setProperty("--sheetZoom", next);
  holder.scrollLeft = left * ratio - x;
  holder.scrollTop = top * ratio - y;
  $("originalFit").textContent = next === 1 ? "Fit" : `${Math.round(next * 100)}%`;
  $("originalFit").disabled = next === 1;
  $("originalZoomOut").disabled = next === 1;
  $("originalZoomIn").disabled = next === SHEET_ZOOMS.at(-1);
  $("sheetGhost").hidden = true;
}

function stepSheetZoom(by) {
  const next = by > 0 ? SHEET_ZOOMS.find(level => level > sheetZoom + 0.01) : SHEET_ZOOMS.findLast(level => level < sheetZoom - 0.01);
  if (next) setSheetZoom(next);
}

// A chord's box is brought into sight when the chord is chosen, however far the original is zoomed.
function showBox(box) {
  const holder = $("images"), area = holder.getBoundingClientRect(), place = box.getBoundingClientRect();
  if (place.left < area.left || place.right > area.right) holder.scrollLeft += place.left - area.left - (area.width - place.width) / 2;
  if (place.top < area.top || place.bottom > area.bottom) holder.scrollTop += place.top - area.top - (area.height - place.height) / 2;
}

// A box clicked on the original chooses its chord in the score, and opens it to change, as a step to it does.
function openChordFromSheet(index) {
  const element = document.querySelector(`#pages g.harm[data-index="${index}"]`);
  if (!element || !score?.chords?.[index]) return;
  element.scrollIntoView({ block: "center", inline: "nearest" }); // With room below it for the box to change it.
  select(null);
  chooseChord(index);
  openEditor(element.getBoundingClientRect(), index, score.chords[index].text);
}

// The page under a point of the original, and the point on it in the page's own pixels.
function sheetPointAt(clientX, clientY) {
  const frame = document.elementsFromPoint(clientX, clientY).find(element => element.classList?.contains("imageBox"));
  const image = frame?.querySelector("img");
  if (!image?.naturalWidth) return null;
  const box = image.getBoundingClientRect();
  return { page: Number(frame.dataset.page), image, x: (clientX - box.left) / box.width * image.naturalWidth, y: (clientY - box.top) / box.height * image.naturalHeight };
}

// Where a chord clicked at this point of the page goes, as the reader places one: the line of music
// it's over, the first whose staff ends below it, or the last; the bar between its barlines, the first
// or last bar beyond the line's ends; and the nearest note or rest in that bar.
function missedChordAt(point) {
  const lines = sheetPlaces[point.page];
  if (!lines?.length) return null;
  const line = lines.find(item => point.y <= item.bottom + item.unit * 2) ?? lines.at(-1);
  if (!line.bars.length) return null;
  const bar = line.bars.find(item => point.x < item.right) ?? line.bars.at(-1);
  const beat = bar.beats.reduce((best, item) => Math.abs(item.x - point.x) < Math.abs(best.x - point.x) ? item : best);
  return { line, bar, beat, name: placeName(bar.measure, beat.onset) };
}

// How a player counts a moment in a bar, the bar found by its number as the score was read, wherever
// it is now: "bar 5, beat 3". Null for a bar deleted since.
function placeName(origin, onset) {
  const bar = document.getElementById(`m${origin}`);
  const ids = bar ? [...bar.querySelectorAll("g.note, g.rest, g.mRest")].map(element => ownId(element.id)).filter(id => score?.places?.[id]) : [];
  if (!ids.length) return null;
  const at = ids.find(id => Math.abs(score.places[id][1] - onset) < 0.001);
  return at ? whenOf(document.getElementById(at)) : `bar ${measureFacts(score.places[ids[0]][0]).number}`;
}

function drawSheetGhost(event) {
  const ghost = $("sheetGhost");
  if (editing?.at) return; // It stays where the chord being typed goes.
  const point = !sheetDrag && !event.target.closest?.(".box") ? sheetPointAt(event.clientX, event.clientY) : null;
  const spot = point && missedChordAt(point);
  if (!spot?.name) { ghost.hidden = true; return; }
  const image = point.image.getBoundingClientRect(), frame = $("sheetFrame").getBoundingClientRect();
  const scale = image.width / point.image.naturalWidth;
  const top = spot.line.top - spot.line.unit * 3.5;
  ghost.style.left = `${image.left - frame.left + spot.beat.x * scale}px`;
  ghost.style.top = `${image.top - frame.top + top * scale}px`;
  ghost.style.height = `${(spot.line.bottom - top) * scale}px`;
  ghost.querySelector(".tag").textContent = `Add a chord: ${spot.name}`;
  ghost.hidden = false;
}

// A click where the reader missed a chord asks for it, and writes it at that bar and beat. The place
// clicked is kept as its box, a little to the right of the beat and above the staff, as chords sit.
function askForMissedChord(event) {
  const point = sheetPointAt(event.clientX, event.clientY);
  const spot = point && missedChordAt(point);
  if (!point) return;
  if (!spot) {
    message(sheetPlaces[point.page] ? "This page was read before chords could be added from it. Choose a note in the score, then Add chord." : "");
    return;
  }
  if (!spot.name) { message("That bar has been deleted from the score."); return; }
  const unit = spot.line.unit;
  openEditor({ left: event.clientX, right: event.clientX, top: event.clientY, bottom: event.clientY + 8 }, null, "");
  editing.at = { measure: spot.bar.measure, onset: spot.beat.onset, page: point.page, box: [spot.beat.x - unit * 0.5, point.y - unit * 1.2, spot.beat.x + unit * 3.5, point.y + unit * 0.8] };
  $("sheetGhost").querySelector(".tag").textContent = `Chord at ${spot.name}`;
}

let sheetDrag = null, sheetDragged = false, sheetClick = 0;
const sheetPointers = new Map();

function setUpOriginal() {
  const holder = $("images");
  holder.addEventListener("wheel", event => {
    if (!event.ctrlKey && !event.metaKey) return; // The wheel alone scrolls, as anywhere else.
    event.preventDefault();
    setSheetZoom(sheetZoom * Math.exp(-event.deltaY / 400), { x: event.clientX, y: event.clientY });
  }, { passive: false });
  holder.addEventListener("dblclick", event => {
    if (event.target.closest(".box")) return;
    event.preventDefault();
    clearTimeout(sheetClick);
    closeEditor();
    if (sheetZoom > 1) setSheetZoom(1);
    else setSheetZoom(3, { x: event.clientX, y: event.clientY });
  });
  holder.addEventListener("pointerdown", event => {
    if (event.button !== 0) return;
    sheetPointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (sheetPointers.size === 2) {
      const [a, b] = [...sheetPointers.values()];
      sheetDrag = { pinch: Math.hypot(a.x - b.x, a.y - b.y), zoom: sheetZoom, moved: true };
      return;
    }
    sheetDrag = { x: event.clientX, y: event.clientY, left: holder.scrollLeft, top: holder.scrollTop, moved: false };
  });
  holder.addEventListener("pointermove", event => {
    if (sheetPointers.has(event.pointerId)) sheetPointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (!sheetDrag) { drawSheetGhost(event); return; }
    if (sheetDrag.pinch && sheetPointers.size === 2) {
      const [a, b] = [...sheetPointers.values()];
      setSheetZoom(sheetDrag.zoom * Math.hypot(a.x - b.x, a.y - b.y) / sheetDrag.pinch, { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
      return;
    }
    if (sheetDrag.pinch) return;
    const dx = event.clientX - sheetDrag.x, dy = event.clientY - sheetDrag.y;
    if (!sheetDrag.moved && Math.hypot(dx, dy) < 5) return; // A click wobbles a little.
    if (!sheetDrag.moved) { holder.setPointerCapture(event.pointerId); holder.classList.add("panning"); $("sheetGhost").hidden = true; }
    sheetDrag.moved = true;
    holder.scrollLeft = sheetDrag.left - dx;
    holder.scrollTop = sheetDrag.top - dy;
  });
  const letGo = event => {
    sheetPointers.delete(event.pointerId);
    if (sheetDrag?.moved) sheetDragged = true;
    if (!sheetPointers.size) { sheetDrag = null; holder.classList.remove("panning"); }
  };
  holder.addEventListener("pointerup", letGo);
  holder.addEventListener("pointercancel", letGo);
  holder.addEventListener("pointerleave", () => { if (!editing?.at) $("sheetGhost").hidden = true; });
  holder.addEventListener("scroll", () => { $("sheetGhost").hidden = true; }, { passive: true });
  holder.addEventListener("click", event => {
    if (sheetDragged) { sheetDragged = false; return; } // That was a drag to move about, not a click.
    const box = event.target.closest(".box");
    if (box) { openChordFromSheet(Number(box.dataset.index)); return; }
    // A second click is a double-click, which zooms, so the first waits a moment to see.
    clearTimeout(sheetClick);
    if (event.detail > 1) return;
    const { clientX, clientY } = event;
    sheetClick = setTimeout(() => askForMissedChord({ clientX, clientY }), 260);
  });
  $("originalZoomIn").addEventListener("click", () => stepSheetZoom(1));
  $("originalZoomOut").addEventListener("click", () => stepSheetZoom(-1));
  $("originalFit").addEventListener("click", () => setSheetZoom(1));
  setUpOriginalRoom();
}

// The original's room: its width beside the score, or its height across the top, dragged at its edge
// or given more at once by its button, and kept for next time.
const LARGE_ORIGINAL = "60%";

function originalRoom(size) {
  const across = $("original").classList.contains("across");
  $("scoreArea").style.setProperty(across ? "--originalHeight" : "--originalWidth", size || null);
  $("originalLarger").setAttribute("aria-pressed", String(size === LARGE_ORIGINAL));
  if (fittedZoom()) setZoom(zoom);
  drawCursor();
}

function setUpOriginalRoom() {
  const grip = $("originalGrip");
  const setting = () => $("original").classList.contains("across") ? "originalHeight" : "originalWidth";
  const resize = (clientX, clientY) => {
    const area = $("scoreArea").getBoundingClientRect();
    const across = $("original").classList.contains("across");
    const size = across ? Math.min(Math.max(clientY - area.top, 120), area.height - 160) : Math.min(Math.max(area.right - clientX, 240), area.width - 320);
    originalRoom(`${Math.round(size)}px`);
  };
  let dragging = false;
  grip.addEventListener("pointerdown", event => { dragging = true; grip.setPointerCapture(event.pointerId); grip.classList.add("dragging"); event.preventDefault(); });
  grip.addEventListener("pointermove", event => { if (dragging) resize(event.clientX, event.clientY); });
  grip.addEventListener("pointerup", () => {
    dragging = false;
    grip.classList.remove("dragging");
    keepSetting(setting(), $("scoreArea").style.getPropertyValue(`--${setting()}`));
  });
  grip.addEventListener("keydown", event => {
    const by = { ArrowLeft: 40, ArrowRight: -40, ArrowUp: -40, ArrowDown: 40 }[event.key];
    if (!by) return;
    event.preventDefault();
    const box = $("original").getBoundingClientRect(), across = $("original").classList.contains("across");
    resize(box.left - (across ? 0 : by), box.bottom + (across ? by : 0));
    keepSetting(setting(), $("scoreArea").style.getPropertyValue(`--${setting()}`));
  });
  $("originalLarger").addEventListener("click", () => {
    const large = $("originalLarger").getAttribute("aria-pressed") !== "true";
    originalRoom(large ? LARGE_ORIGINAL : readSetting(setting(), ""));
    if (!large && readSetting(setting(), "") === LARGE_ORIGINAL) originalRoom("");
  });
}

// A sheet wider than it's tall, such as a lead sheet, is shown across the top of the score, where it
// can be read at a size, and the score keeps the window's width. A page taller than it's wide is
// shown beside the score, as it reads best down its height.
const ACROSS_ABOVE = 1.25; // How much wider than tall a sheet is to go across the top.

function layOutOriginal(image) {
  const across = image.naturalWidth / image.naturalHeight > ACROSS_ABOVE;
  if ($("original").classList.contains("across") !== across) $("original").classList.toggle("across", across);
  originalRoom(readSetting(across ? "originalHeight" : "originalWidth", ""));
}

function drawBoxes() {
  document.querySelectorAll(".box").forEach(box => box.remove());
  for (const chord of score.chords) {
    if (!chord.box) continue;
    const frame = document.querySelector(`.imageBox[data-page="${chord.page}"]`);
    const image = frame?.querySelector("img");
    if (!image?.naturalWidth) continue;
    const [left, top, right, bottom] = chord.box;
    const box = document.createElement("div");
    box.className = "box" + (chord.confidence !== null && chord.confidence < UNSURE_BELOW ? " unsure" : "") + (chord.added ? " added" : "");
    box.dataset.index = chord.index;
    box.title = `${chord.text}: click to change it`;
    box.style.left = `${left / image.naturalWidth * 100}%`;
    box.style.top = `${top / image.naturalHeight * 100}%`;
    box.style.width = `${(right - left) / image.naturalWidth * 100}%`;
    box.style.height = `${(bottom - top) / image.naturalHeight * 100}%`;
    frame.append(box);
  }
}

// The chords the reader wasn't sure of, coloured in the score and on the original, are one line to
// step through under it, as the bars that don't add up are (stepLine). The one stepped to is lit in
// both places.
let chordStep = null; // The chord last stepped to, by its number in the score, or null.

function drawChecks() {
  const unsure = score.chords.filter(chord => chord.confidence !== null && chord.confidence < UNSURE_BELOW);
  $("checks").hidden = unsure.length === 0;
  document.querySelectorAll("#pages g.harm.checkHere, .box.checkHere").forEach(element => element.classList.remove("checkHere"));
  if (unsure.some(chord => chord.index === chordStep)) {
    document.querySelectorAll(`#pages g.harm[data-index="${chordStep}"], .box[data-index="${chordStep}"]`).forEach(element => element.classList.add("checkHere"));
  }
  $("checks").querySelector(".steps")?.remove();
  if (!unsure.length) return;
  $("checks").append(stepLine("chordSteps", "Chords to check", unsure, unsure.map(chord => chord.index), chordStep, {
    said: chord => chord.text,
    many: `${unsure.length} chords`,
    why: "The reader wasn't sure of these chords, which are coloured in the score and on the original.",
    before: "The chord before to check",
    next: "The next chord to check",
    go: stepToChordCheck,
  }));
}

// A chord stepped to is chosen and opened to change, as a click on its chip or its box was.
function stepToChordCheck(chord) {
  chordStep = chord.index;
  drawChecks();
  openChordFromSheet(chord.index);
}

// A selected note takes its voice's colour, as it does in MuseScore.
function markSelected(element) {
  element.classList.remove("voice2", "voice3", "voice4");
  const voice = layerNumber(element) + 1;
  element.classList.add("selected", ...(voice > 1 ? [`voice${voice}`] : []));
}

function select(id) {
  document.querySelectorAll("#pages g.selected").forEach(element => element.classList.remove("selected"));
  range = null;
  selectedMark = null;
  selectedChord = null;
  selected = id;
  const element = id && document.getElementById(id);
  if (element) markSelected(element);
  if (id) playFromNote(id);
  drawRangeBoxes();
  updateTools();
}

async function send(path, body) {
  const data = await postJson(`/api/scores/${scoreId}/${path}`, body || undefined);
  const before = editor.cursor;
  receive(data);
  if (editor.on && path !== "undo" && path !== "redo") {
    editor.trail.push({ before, after: editor.cursor });
    editor.future = [];
  }
  return data;
}

async function applyToNote(action, extra = {}) {
  if (range && GROUP_ACTIONS.has(action)) {
    await send("group", { action, notes: range.ids });
    return;
  }
  const id = actionTarget();
  if (id) await send(`notes/${id}`, { action, type: editor.type, dots: editor.dots, ...extra });
}

function noteAction(action, extra = {}) {
  return enqueue(() => applyToNote(action, extra));
}

function openEditor(rect, index, text, noteId = null) {
  editing = { index, noteId };
  document.querySelectorAll(".box.active").forEach(box => box.classList.remove("active"));
  const active = index === null ? null : document.querySelector(`.box[data-index="${index}"]`);
  active?.classList.add("active");
  if (active && !$("original").hidden) showBox(active);
  $("editor").hidden = false;
  $("editorError").hidden = true;
  $("chordDelete").hidden = index === null;
  $("chordInput").hidden = $("chordSave").hidden = false;
  $("chordInput").placeholder = "e.g. Bbmaj7";
  const width = $("editor").offsetWidth;
  const height = $("editor").offsetHeight;
  $("editor").style.left = `${Math.max(8, Math.min(rect.left + scrollX, innerWidth - width - 8))}px`;
  // Below what it's for, unless that would put it off the bottom of the window.
  const below = rect.bottom + 6;
  $("editor").style.top = `${(below + height > innerHeight - 8 ? Math.max(8, rect.top - height - 6) : below) + scrollY}px`;
  $("chordInput").value = text;
  $("chordInput").focus();
  $("chordInput").select();
}

function closeEditor() {
  editing = null;
  $("sheetGhost").hidden = true;
  // A box that's hidden while it has the focus keeps it for a moment, and would swallow the next key.
  $("chordInput").blur();
  $("editor").hidden = true;
  document.querySelectorAll(".box.active").forEach(box => box.classList.remove("active"));
}

async function saveChord(text) {
  if (!editing) return;
  try {
    if (editing.mark) await send("marks", { action: "label", id: editing.mark, text });
    else if (editing.newMark) await send("marks", { ...editing.newMark, text });
    else if (editing.at) await send("chords/at", { ...editing.at, text });
    else if (editing.index === null) await send("chords", { note: editing.noteId, text });
    else await send(`chords/${editing.index}`, { text });
    closeEditor();
  } catch (error) {
    $("editorError").textContent = error.message;
    $("editorError").hidden = false;
  }
}

// Noteheads are tiny and staff lines can sit on top of them, so a near miss still counts.
function nearest(selector, x, y, reach) {
  let best = null, bestDistance = reach;
  for (const element of document.querySelectorAll(selector)) {
    const box = headBox(element);
    const dx = Math.max(box.left - x, 0, x - box.right);
    const dy = Math.max(box.top - y, 0, y - box.bottom);
    const distance = Math.hypot(dx, dy);
    if (distance < bestDistance) { best = element; bestDistance = distance; }
  }
  return best;
}

function clickPages(event) {
  if (reading()) return;
  if (dragged) { dragged = false; return; }
  if (editor.on) {
    closeEditor();
    // A note already written is selected, to fix it, not written over. Empty staff is where notes
    // go, and Shift adds the note to the chord there.
    const head = event.shiftKey ? null : noteheadAt(event.clientX, event.clientY);
    if (head) { pickWhileWriting(head); return; }
    // A click writes the note the ghost shows, and where it shows none, such as far from any
    // staff, nothing. A click that came without a move, as a tap can, draws the ghost there first.
    if (pointer?.x !== event.clientX || pointer?.y !== event.clientY || pointer?.shift !== event.shiftKey) {
      pointer = { x: event.clientX, y: event.clientY, shift: event.shiftKey };
      drawGhost(pointer.x, pointer.y, pointer.shift);
    }
    if (ghostSpot) placeNote(ghostSpot, event.shiftKey);
    return;
  }
  const mark = markAt(event);
  if (mark) { closeEditor(); selectMark(mark); return; }
  const chord = event.target.closest("g.harm") || nearest("#pages g.harm", event.clientX, event.clientY, 6);
  if (chord && chord.dataset.index !== undefined) {
    const index = Number(chord.dataset.index);
    // The chord is selected too, and the inspector shows its diagram to change.
    select(null);
    chooseChord(index);
    openEditor(chord.getBoundingClientRect(), index, score.chords[index].text);
    return;
  }
  const note = noteheadAt(event.clientX, event.clientY) || event.target.closest("g.note, g.rest, g.mRest") || nearest("#pages g.note, #pages g.rest, #pages g.mRest", event.clientX, event.clientY, 14);
  const staff = note ? null : staffUnder(event.clientX, event.clientY);
  const extend = event.shiftKey;
  closeEditor();
  enqueue(async () => {
    if (note && extend && selected) await growRange(note.id);
    else if (note) { select(note.id); hearNote(note); }
    else if (staff) await selectMeasure(staff, extend);
    else select(null);
  });
}

function addChordHere() {
  return enqueue(async () => {
    const id = actionTarget();
    const note = id && document.getElementById(id);
    if (!note) { message("Choose a note first."); return; }
    openEditor(headBox(note), null, "", id);
  });
}

function setUpPage() {
  $("pages").addEventListener("click", clickPages);
  document.querySelectorAll("button[data-action]").forEach(button => {
    button.addEventListener("click", () => noteAction(button.dataset.action));
  });
  $("addChord").addEventListener("click", addChordHere);
  // The text is read now and saved in turn, as a reply that's on its way can redraw the box.
  const saveInTurn = text => enqueue(() => saveChord(text));
  $("chordSave").addEventListener("click", () => saveInTurn($("chordInput").value));
  $("chordDelete").addEventListener("click", () => saveInTurn(""));
  $("chordInput").addEventListener("keydown", event => {
    if (event.key === "Enter") saveInTurn(event.target.value);
    if (event.key === "Escape") closeEditor();
  });
  $("desk").addEventListener("scroll", () => { if (!$("editor").hidden && document.activeElement !== $("chordInput")) closeEditor(); });
  $("undo").addEventListener("click", () => undoEdit());
  $("checked").addEventListener("click", markChecked);
  // Marked as the button is pressed, not once the dialog has closed, which comes a moment later, so an
  // edit made straight after can't overtake it.
  $("checkedAnyway").addEventListener("click", () => setChecked(true));
  // Cleared afterwards, so choosing the same file again still counts as a choice.
  $("file").addEventListener("change", event => { upload(event.target.files); event.target.value = ""; });
  // A file dropped anywhere opens, as Open does.
  const carriesFile = event => [...(event.dataTransfer?.types ?? [])].includes("Files");
  for (const name of ["dragenter", "dragover"]) document.addEventListener(name, event => { if (!carriesFile(event)) return; event.preventDefault(); document.body.classList.add("dropping"); });
  for (const name of ["dragleave", "drop"]) document.addEventListener(name, event => { if (!carriesFile(event)) return; event.preventDefault(); document.body.classList.remove("dropping"); });
  document.addEventListener("drop", event => { if (carriesFile(event)) upload(event.dataTransfer.files); });
  document.querySelectorAll("label.button").forEach(label => {
    label.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); label.click(); } });
  });
  $("homeSearch").addEventListener("input", drawTiles);
  setUpOriginal();
  $("readingCancel").addEventListener("click", () => removeSheet(scoreId));
  $("readingRemove").addEventListener("click", () => removeSheet(scoreId));
  $("readingRetry").addEventListener("click", () => readAgain(scoreId));
  $("readingLibrary").addEventListener("click", goHome);
  $("brand").addEventListener("click", goHome);
  $("toastClose").addEventListener("click", hideToast);
  addEventListener("hashchange", followAddress);
  addEventListener("popstate", followAddress);
  setInterval(tickReading, 1000);
}

// A checked sheet is learnt from as right, so marking one with chords or bars still listed to check
// asks first, saying how many, and either way can be undone at once or un-marked later.
function stillListed() {
  const chords = score.chords.filter(chord => chord.confidence !== null && chord.confidence < UNSURE_BELOW).length;
  const bars = (score.barChecks ?? []).length;
  const counted = [chords && `${chords} ${chords === 1 ? "chord" : "chords"}`, bars && `${bars} ${bars === 1 ? "bar" : "bars"}`].filter(Boolean);
  return counted.length ? `${counted.join(" and ")} ${chords + bars === 1 ? "is" : "are"} still listed to check.` : "";
}

function markChecked() {
  if (!score) return;
  if (score.checked) { setChecked(false); return; }
  const listed = stillListed();
  if (!listed) { setChecked(true); return; }
  $("checkedWhat").textContent = listed;
  $("checkedDialog").showModal();
}

function setChecked(on) {
  return enqueue(async () => {
    await send("checked", { checked: on });
    toast(on ? "Marked as checked: its chords will be learnt from." : "No longer marked as checked, so it won't be learnt from.", { label: "Undo", run: () => setChecked(!on) });
  });
}

// The logo, and Library where a sheet is being read, go back to the library. Whatever was open is
// kept as it is: a score is saved with every edit, and a sheet goes on being read.
function goHome() {
  if (document.body.classList.contains("atHome")) return;
  closeScore();
}

// Each score opened is an entry in the window's history, named in the address. Back and Forward, a
// mouse's back button, or another score's address typed in open what the address names: that score,
// or with none the library. They changed only the address, so it and the score shown drifted apart.
function followAddress() {
  const id = location.hash.slice(1);
  if (id === (scoreId ?? "")) return;
  if (id) openScore(id);
  else closeScore();
}
