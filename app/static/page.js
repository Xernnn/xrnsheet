// Opening, drawing and saving a score. The other scripts load after this one, and editor.js starts the page.
const $ = id => document.getElementById(id);
const UNSURE_BELOW = 0.8;
let scoreId = null, score = null, selected = null, editing = null;
let publishing = false; // The Publish tab shows the same pages with other tools above them.
let sheets = []; // What the Home tab lists.
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

function show(view) {
  const open = Boolean(score && scoreId);
  $("start").hidden = view !== "start";
  $("busy").hidden = view !== "busy";
  $("workspace").hidden = view !== "workspace";
  document.body.classList.toggle("atHome", view !== "workspace");
  document.body.classList.toggle("hasScore", open);
  document.body.classList.toggle("publishing", view === "workspace" && publishing);
  const tab = view !== "workspace" ? "homeTab" : publishing ? "publishTab" : "scoreTab";
  for (const id of ["homeTab", "scoreTab", "publishTab"]) {
    $(id).setAttribute("aria-selected", String(id === tab));
    if (id !== "homeTab") $(id).disabled = !open;
  }
  if (view === "workspace") {
    $("original").hidden = score.blank || originalHidden();
    // A zoom that fits the page depends on how much room the pages have, which is only known now.
    if (zoom === "width" || zoom === "page") setZoom(zoom);
    drawCursor();
  } else {
    stopPlaying();
    setInput(false);
  }
  if (view === "start") drawHistory();
}

function tileFor(sheet) {
  const link = document.createElement("a");
  link.className = "tile";
  link.href = `#${sheet.id}`;
  link.addEventListener("click", event => { event.preventDefault(); openScore(sheet.id); });
  const picture = document.createElement("span");
  picture.className = "sheet";
  const image = new Image();
  image.alt = "";
  image.loading = "lazy";
  image.src = `/api/scores/${sheet.id}/thumbnail.svg?${sheet.saved}`;
  picture.append(image);
  const when = new Date(sheet.saved * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  const name = document.createElement("span");
  name.className = "name";
  name.textContent = sheet.title || "Untitled score";
  const state = document.createElement("span");
  state.className = "state";
  const made = sheet.blank ? "written from scratch" : `${sheet.pages} ${sheet.pages === 1 ? "page" : "pages"}, ${sheet.chords} ${sheet.chords === 1 ? "chord" : "chords"}, ${sheet.checked ? "checked" : "not checked"}`;
  state.textContent = `${when}, ${made}`;
  link.append(picture, name, state);
  return link;
}

function drawTiles() {
  const wanted = $("homeSearch").value.trim().toLowerCase();
  const create = document.createElement("button");
  create.className = "tile";
  create.id = "newTile";
  create.innerHTML = `<span class="sheet">${icon("plus")}</span><span class="name">New score</span>`;
  create.addEventListener("click", openNewScore);
  const shown = sheets.filter(sheet => !wanted || (sheet.title || "Untitled score").toLowerCase().includes(wanted));
  $("historyList").replaceChildren(...(wanted ? [] : [create]), ...shown.map(tileFor));
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
  scoreId = id;
  score = null;
  selected = null;
  publishing = false;
  location.hash = id;
  return poll();
}

// The score stays where it is, as it's saved with every edit, and the Home tab lists it.
function closeScore() {
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
  show("workspace");
  draw();
}

function draw() {
  $("pages").innerHTML = score.pages.map(page => `<div class="page">${page}</div>`).join("");
  $("undo").disabled = !score.canUndo;
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
  drawCursor();
  updateTools();
}

function drawOriginals() {
  $("images").replaceChildren(...score.sourcePages.map(number => {
    const frame = document.createElement("div");
    frame.className = "imageBox";
    frame.dataset.page = number;
    const image = new Image();
    image.alt = `Page ${number} of your sheet`;
    image.addEventListener("load", drawBoxes);
    image.src = `/api/scores/${scoreId}/source/${number}.jpg`;
    frame.append(image);
    return frame;
  }));
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
  selected = id;
  const element = id && document.getElementById(id);
  if (element) markSelected(element);
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
  if (publishing) return;
  if (dragged) { dragged = false; return; }
  if (editor.on) {
    const spot = placeUnder(event.clientX, event.clientY);
    closeEditor();
    if (spot) placeNote(spot, event.shiftKey);
    return;
  }
  const mark = markAt(event);
  if (mark) { closeEditor(); selectMark(mark); return; }
  const chord = event.target.closest("g.harm") || nearest("#pages g.harm", event.clientX, event.clientY, 6);
  if (chord && chord.dataset.index !== undefined) {
    const index = Number(chord.dataset.index);
    openEditor(chord.getBoundingClientRect(), index, score.chords[index].text);
    return;
  }
  const note = event.target.closest("g.note, g.rest, g.mRest") || nearest("#pages g.note, #pages g.rest, #pages g.mRest", event.clientX, event.clientY, 14);
  const staff = note ? null : staffUnder(event.clientX, event.clientY);
  const extend = event.shiftKey;
  closeEditor();
  enqueue(async () => {
    if (note && extend && selected) await growRange(note.id);
    else if (note) select(note.id);
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
  const drop = $("start");
  for (const name of ["dragenter", "dragover"]) drop.addEventListener(name, event => { event.preventDefault(); drop.classList.add("over"); });
  for (const name of ["dragleave", "drop"]) drop.addEventListener(name, event => { event.preventDefault(); drop.classList.remove("over"); });
  drop.addEventListener("drop", event => upload(event.dataTransfer.files[0]));
  document.querySelectorAll("label.button").forEach(label => {
    label.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); label.click(); } });
  });
  $("homeSearch").addEventListener("input", drawTiles);
}
