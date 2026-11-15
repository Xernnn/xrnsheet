// Opening, drawing and saving a score. The other scripts load after this one, and editor.js starts the page.
const $ = id => document.getElementById(id);
const UNSURE_BELOW = 0.8;
let scoreId = null, score = null, selected = null, editing = null;
let sheets = []; // What the library lists.
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

// The window is the library while no score is open, the score once one is, and says it's busy while
// a sheet is being read.
function show(view) {
  const open = Boolean(score && scoreId);
  $("busy").hidden = view !== "busy";
  $("workspace").hidden = view !== "workspace";
  document.body.classList.toggle("atHome", view !== "workspace");
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

function tileFor(sheet) {
  const link = document.createElement("a");
  link.className = "tile";
  link.href = `#${sheet.id}`;
  link.dataset.score = sheet.id;
  link.setAttribute("role", "listitem");
  if (sheet.id === scoreId) link.setAttribute("aria-current", "true");
  link.addEventListener("click", event => { event.preventDefault(); if (sheet.id !== scoreId) openScore(sheet.id); closeDrawers(); });
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
  const made = sheet.blank ? "written here" : `read from ${sheet.pages} ${sheet.pages === 1 ? "page" : "pages"}, ${sheet.checked ? "checked" : "not checked"}`;
  state.textContent = `${when} · ${made}`;
  link.title = `${name.textContent}, ${state.textContent}`;
  link.append(picture, name, state);
  return link;
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
    if (sheet) { sheet.saved = now; sheet.title = score?.title ?? sheet.title; }
    const state = tile.querySelector(".state");
    if (sheet && state) state.textContent = tileFor(sheet).querySelector(".state").textContent;
  }, TILE_DELAY_MS);
}

function drawTiles() {
  const wanted = $("homeSearch").value.trim().toLowerCase();
  const create = document.createElement("button");
  create.className = "tile";
  create.id = "newTile";
  create.innerHTML = `<span class="sheet">${icon("plus")}</span><span class="name">New score</span><span class="state">Piano, guitar, violin or anything</span>`;
  create.addEventListener("click", openNewScore);
  const shown = sheets.filter(sheet => !wanted || (sheet.title || "Untitled score").toLowerCase().includes(wanted));
  $("historyList").replaceChildren(...(wanted ? [] : [create]), ...shown.map(tileFor));
  $("libraryEmpty").hidden = sheets.length > 0;
}

async function drawHistory() {
  const response = await fetch("/api/scores");
  if (!response.ok) return;
  sheets = await response.json();
  drawTiles();
}

function message(text) {
  $("message").textContent = text || "";
  $("message").hidden = !text;
}

function openScore(id) {
  forgetPlayback();
  foldLibrary();
  scoreId = id;
  score = null;
  selected = null;
  chosenPart = null;
  location.hash = id;
  document.querySelectorAll("#historyList .tile").forEach(tile => tile.toggleAttribute("aria-current", tile.dataset.score === id));
  return poll();
}

// The score stays where it is, as it's saved with every edit, and the library lists it.
function closeScore() {
  forgetPlayback();
  history.replaceState(null, "", " ");
  scoreId = null;
  score = null;
  selected = null;
  message("");
  show("start");
}

async function upload(file) {
  if (!file) return;
  message("");
  show("busy");
  const body = new FormData();
  body.append("file", file);
  const response = await fetch("/api/scores", { method: "POST", body });
  const data = await response.json();
  if (!response.ok) { show("start"); message(data.detail); return; }
  openScore(data.id);
}

async function poll() {
  const response = await fetch(`/api/scores/${scoreId}`);
  if (!response.ok) { closeScore(); return; }
  const data = await response.json();
  if (data.status === "processing") {
    $("busyText").textContent = data.message || "Reading your sheet. This usually takes under a minute.";
    show("busy");
    setTimeout(poll, 1500);
    return;
  }
  if (data.status === "failed") { closeScore(); message(data.message); return; }
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
  $("images").replaceChildren(...score.sourcePages.map(number => {
    const frame = document.createElement("div");
    frame.className = "imageBox";
    frame.dataset.page = number;
    const image = new Image();
    image.alt = `Page ${number} of your sheet`;
    image.addEventListener("load", drawBoxes);
    image.addEventListener("load", () => { if (number === score?.sourcePages?.[0]) layOutOriginal(image); });
    image.src = `/api/scores/${scoreId}/source/${number}.jpg`;
    frame.append(image);
    return frame;
  }));
}

// A sheet wider than it's tall, such as a lead sheet, is shown across the top of the score, where it
// can be read at a size, and the score keeps the window's width. A page taller than it's wide is
// shown beside the score, as it reads best down its height.
const ACROSS_ABOVE = 1.25; // How much wider than tall a sheet is to go across the top.

function layOutOriginal(image) {
  const across = image.naturalWidth / image.naturalHeight > ACROSS_ABOVE;
  if ($("original").classList.contains("across") === across) return;
  $("original").classList.toggle("across", across);
  if (fittedZoom()) setZoom(zoom);
  drawCursor();
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
    box.className = "box" + (chord.confidence !== null && chord.confidence < UNSURE_BELOW ? " unsure" : "");
    box.dataset.index = chord.index;
    box.style.left = `${left / image.naturalWidth * 100}%`;
    box.style.top = `${top / image.naturalHeight * 100}%`;
    box.style.width = `${(right - left) / image.naturalWidth * 100}%`;
    box.style.height = `${(bottom - top) / image.naturalHeight * 100}%`;
    frame.append(box);
  }
}

function drawChecks() {
  document.querySelectorAll("#checks .chip").forEach(chip => chip.remove());
  const unsure = score.chords.filter(chord => chord.confidence !== null && chord.confidence < UNSURE_BELOW);
  $("checks").hidden = unsure.length === 0;
  for (const chord of unsure) {
    const chip = document.createElement("button");
    chip.className = "chip";
    chip.textContent = chord.text;
    chip.addEventListener("click", () => {
      const element = document.querySelector(`#pages g.harm[data-index="${chord.index}"]`);
      if (!element) return;
      element.scrollIntoView({ block: "nearest", inline: "nearest" });
      openEditor(element.getBoundingClientRect(), chord.index, chord.text);
    });
    $("checks").append(chip);
  }
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
  const response = await fetch(`/api/scores/${scoreId}/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "That edit didn't work.");
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
  const active = document.querySelector(`.box[data-index="${index}"]`);
  active?.classList.add("active");
  active?.scrollIntoView({ block: "nearest" });
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
    const spot = placeUnder(event.clientX, event.clientY);
    if (spot) placeNote(spot, event.shiftKey);
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
  $("checked").addEventListener("click", () => enqueue(() => send("checked", { checked: !score.checked })));
  for (const id of ["file", "camera"]) {
    // Cleared afterwards, so choosing the same file again still counts as a choice.
    $(id).addEventListener("change", event => { upload(event.target.files[0]); event.target.value = ""; });
  }
  // A file dropped anywhere opens, as Open does.
  const carriesFile = event => [...(event.dataTransfer?.types ?? [])].includes("Files");
  for (const name of ["dragenter", "dragover"]) document.addEventListener(name, event => { if (!carriesFile(event)) return; event.preventDefault(); document.body.classList.add("dropping"); });
  for (const name of ["dragleave", "drop"]) document.addEventListener(name, event => { if (!carriesFile(event)) return; event.preventDefault(); document.body.classList.remove("dropping"); });
  document.addEventListener("drop", event => { if (carriesFile(event)) upload(event.dataTransfer.files[0]); });
  document.querySelectorAll("label.button").forEach(label => {
    label.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); label.click(); } });
  });
  $("homeSearch").addEventListener("input", drawTiles);
}
