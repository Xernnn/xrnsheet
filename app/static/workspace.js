// The workspace: Read, Write and Practice, the instrument switcher, the library beside the score,
// turning pages in Read, and the command palette, which finds every action the app has by name.
const MODES = {
  read: { label: "Read", keys: "Alt+R" },
  write: { label: "Write", keys: "Alt+W" },
  practice: { label: "Practice", keys: "Alt+P" },
};
const TURN_SHARE = 0.9; // How much of the window a turn moves on, when a system is taller than the window.
const SYSTEM_MARGIN_PX = 12; // Left above a system brought up to the top of the window.
const MOST_COMMANDS = 80;
const commands = { list: [], shown: [], chosen: 0 };
let pageCountFrame = 0;

function currentMode() {
  return document.body.dataset.mode || "write";
}

function reading() {
  return currentMode() === "read";
}

function writing() {
  return currentMode() === "write";
}

// Read shows the score alone, as large as it goes; Write brings the note input, the inspector and the
// instrument; Practice the loop, speed, count-in, hands and waiting. A mode is kept for next time.
function setMode(mode, remember = true) {
  if (!MODES[mode]) return;
  const was = currentMode();
  const kept = musicAtTop(); // Before the mode's own layout moves the pages about.
  if (was !== mode && (practice.follower || (player && (was === "practice" || mode === "practice")))) stopPlaying();
  if (mode !== "write") {
    setInput(false);
    closeEditor();
    closeLyric();
    closeMenus();
  }
  if (mode === "read") select(null);
  document.body.dataset.mode = mode;
  if (remember) keepSetting("mode", mode);
  for (const button of document.querySelectorAll("#modeSwitch button")) button.setAttribute("aria-checked", String(button.dataset.mode === mode));
  restoreZoom(kept);
  drawDock();
  if (score) updateTools();
  drawMidiState();
  drawPageCount();
  drawStart(); // An A–B loop is played from A in Practice only.
  $("libraryToggle").setAttribute("aria-pressed", String(libraryShown()));
  $("inspectorToggle").setAttribute("aria-pressed", String(inspectorShown()));
}

// The library sits beside an open score on a wide window, and over it on a narrow one. Read shows
// the score alone, so there it's away.
function libraryShown() {
  if (document.body.classList.contains("atHome")) return true;
  if (reading() && score) return false;
  return innerWidth > 1180 ? !document.body.classList.contains("libraryHidden") : document.body.classList.contains("libraryOpen");
}

function toggleLibrary() {
  if (document.body.classList.contains("atHome")) return;
  const on = !libraryShown();
  if (on && reading() && score) setMode("write"); // The library is shown beside the score, as in Write.
  if (innerWidth > 1180) document.body.classList.toggle("libraryHidden", !on);
  else document.body.classList.toggle("libraryOpen", on);
  $("libraryToggle").setAttribute("aria-pressed", String(on));
  if (on) drawHistory();
  if (fittedZoom()) setZoom(zoom);
  drawCursor();
}

// A score that opens has the window to itself: the library folds away, and its button or F6 brings
// it back. With no score open the library is the window.
function foldLibrary() {
  document.body.classList.add("libraryHidden");
  document.body.classList.remove("libraryOpen");
  $("libraryToggle").setAttribute("aria-pressed", String(libraryShown()));
}

// A drawer over the score closes once it has done its job.
function closeDrawers() {
  if (innerWidth > 1180) return;
  document.body.classList.remove("libraryOpen", "inspectorOpen");
  $("libraryToggle").setAttribute("aria-pressed", String(libraryShown()));
}

// The instrument switcher shows the chosen part, by its kind, and lists the others.
function drawPartSwitch() {
  if (!score) return;
  const part = activePart();
  const kind = kindOfPart(part);
  $("partKind").innerHTML = icon(KINDS[kind].icon);
  $("partName").textContent = partLabel(part);
  $("partSwitch").dataset.kind = kind;
  $("partSwitch").setAttribute("aria-label", `Instrument: ${partLabel(part)}`);
  $("partSwitch").title = `${partLabel(part)} · ${KINDS[kind].label.toLowerCase()}`;
  fitTopBar();
}

// Short of room, as in a window tiled to half a laptop's screen, the switcher shows only its
// instrument's icon, the part named in its tooltip, before the title is cut short: a name cut to
// its first letter says nothing. The title then ends in an ellipsis, and its tooltip has it whole.
// The title's own limit on a wide window isn't for the switcher to make room for.
function fitTopBar() {
  const holder = $("partSwitch"), title = $("scoreName");
  holder.classList.remove("iconOnly");
  const cut = element => element.scrollWidth > element.clientWidth + 1;
  const squeezed = cut(title) && title.offsetWidth < parseFloat(getComputedStyle(title).maxWidth) - 1;
  holder.classList.toggle("iconOnly", cut($("partName")) || squeezed);
}

let shownKinds = "";

// A part's kind can become known once playback says what sound it has, which may change the panel.
function refreshKinds() {
  const kinds = (score?.parts ?? []).map((_, index) => kindOfPart(index)).join(",");
  if (kinds === shownKinds) return;
  shownKinds = kinds;
  drawPartSwitch();
  drawParts();
  drawPracticeChoices();
  if (dockOpen("instrument")) drawDock();
}

function partItems() {
  return [
    ...(score?.parts ?? []).map((_, index) => ({ label: `${partLabel(index)} · ${KINDS[kindOfPart(index)].label.toLowerCase()}`, checked: index === activePart(), run: () => switchPart(index) })),
    null,
    { label: "Add an instrument…", keys: "I", run: openAddPart },
  ];
}

// Pages in Read: the pages a window holds at once, turned together, and a page taller than the
// window read down before it's turned.
function pageElements() {
  return [...document.querySelectorAll("#pages .page")];
}

// The pages in sight: those whose middle is in the window, across the desk or, with the pages one
// below the other, down it.
function pagesInView() {
  const desk = $("desk");
  const pages = pageElements();
  const down = fittedToWidth() || wholePages();
  const from = down ? desk.scrollTop : desk.scrollLeft, to = from + (down ? desk.clientHeight : desk.clientWidth);
  const middle = page => (down ? page.offsetTop + page.offsetHeight / 2 : page.offsetLeft + page.offsetWidth / 2);
  let shown = pages.map((page, index) => ({ page, index })).filter(({ page }) => middle(page) > from && middle(page) < to);
  // A page taller than the window is in sight while any of it is.
  if (!shown.length && down) shown = pages.map((page, index) => ({ page, index })).filter(({ page }) => page.offsetTop < to && page.offsetTop + page.offsetHeight > from).slice(0, 1);
  return { pages, shown: shown.length ? shown.map(item => item.index) : [0] };
}

// Pages one below the other are read down a system at a time: the first system not wholly in sight
// comes up to the top of the window, with the notes and marks drawn above its staff, so none is cut
// under the top bar. Back, the systems before come down, as many as fit whole, and from the top of
// their page if that fits too. Only a system taller than the window is read a window at a time.
function readDown(by) {
  const desk = $("desk"), room = desk.getBoundingClientRect();
  const systems = [...document.querySelectorAll("#pages g.system")].map(system => ({ system, box: system.getBoundingClientRect() }));
  const fits = room.height - 2 * SYSTEM_MARGIN_PX;
  if (by > 0) {
    const next = systems.find(({ box }) => box.bottom > room.bottom + 1);
    if (!next) return; // The end of the score is in sight.
    const step = next.box.top - room.top - SYSTEM_MARGIN_PX;
    desk.scrollTop += step > 1 ? step : desk.clientHeight * TURN_SHARE;
    return;
  }
  const before = systems.filter(({ box }) => box.top < room.top + SYSTEM_MARGIN_PX - 1);
  const last = before.at(-1)?.box;
  if (!last) { desk.scrollTop = 0; return; }
  if (last.bottom - last.top > fits) { desk.scrollTop -= desk.clientHeight * TURN_SHARE; return; }
  let first = before.length - 1;
  while (first > 0 && last.bottom - before[first - 1].box.top <= fits) first -= 1;
  const { system, box } = before[first];
  const page = system.closest(".page");
  const pageTop = page.getBoundingClientRect().top - parseFloat(getComputedStyle($("pagesFrame")).paddingTop);
  const top = page.querySelector("g.system") === system && last.bottom - pageTop <= fits ? pageTop : box.top - SYSTEM_MARGIN_PX;
  desk.scrollTop += top - room.top;
}

// Pages turned are where the reader now is, even when the turn lands where an earlier zoom left the
// desk, as Home does after a spread on: the music kept from that zoom, on the page turned away from,
// would otherwise be what the next zoom or mode brought back.
function turnPage(by) {
  keptMusic = null;
  const desk = $("desk");
  const view = score?.view ?? "page";
  if (view === "vertical") { readDown(by); drawPageCount(); return; }
  if (view === "horizontal") { desk.scrollLeft += by * desk.clientWidth * TURN_SHARE; drawPageCount(); return; }
  const { pages, shown } = pagesInView();
  if (!pages.length) return;
  const padding = parseFloat(getComputedStyle($("pagesFrame")).paddingTop);
  if (wholePages()) {
    // A row of whole pages, one or two side by side, is turned at once, to the next row below.
    const tops = [...new Set(pages.map(page => page.offsetTop))];
    const row = tops.indexOf(pages[shown[0]].offsetTop);
    const target = Math.min(Math.max(row + by, 0), tops.length - 1);
    if (target === row) return;
    desk.scrollTop = tops[target] - padding;
    drawPageCount();
    return;
  }
  if (fittedToWidth()) {
    // One below the other, the pages are read down as one long page.
    readDown(by);
    drawPageCount();
    return;
  }
  const page = pages[shown[0]];
  const bottom = page.offsetTop + page.offsetHeight;
  if (by > 0 && desk.scrollTop + desk.clientHeight < bottom - 4) { desk.scrollTop += desk.clientHeight * TURN_SHARE; drawPageCount(); return; }
  if (by < 0 && desk.scrollTop > 4) { desk.scrollTop -= desk.clientHeight * TURN_SHARE; drawPageCount(); return; }
  const target = Math.min(Math.max(shown[0] + by * shown.length, 0), pages.length - 1);
  if (target === shown[0]) return; // The end of the score, or its start: nowhere further to turn.
  desk.scrollLeft = pages[target].offsetLeft - parseFloat(getComputedStyle($("pagesFrame")).paddingLeft);
  desk.scrollTop = 0;
  drawPageCount();
}

function turnToEnd(last) {
  keptMusic = null;
  const pages = pageElements();
  if (!pages.length) return;
  if (fittedToWidth() || wholePages()) {
    $("desk").scrollTop = last ? pages.at(-1).offsetTop - parseFloat(getComputedStyle($("pagesFrame")).paddingTop) : 0;
    drawPageCount();
    return;
  }
  $("desk").scrollLeft = last ? pages.at(-1).offsetLeft : 0;
  $("desk").scrollTop = last && (score?.view ?? "page") !== "page" ? $("desk").scrollHeight : 0;
  drawPageCount();
}

function drawPageCount() {
  cancelAnimationFrame(pageCountFrame);
  pageCountFrame = requestAnimationFrame(() => {
    const pages = pageElements();
    if (!score || (score.view ?? "page") !== "page" || pages.length < 2) { $("pageCount").textContent = ""; return; }
    const { shown } = pagesInView();
    const first = shown[0] + 1, last = shown.at(-1) + 1;
    $("pageCount").textContent = first === last ? `Page ${first} of ${pages.length}` : `Pages ${first}–${last} of ${pages.length}`;
  });
}

// Playback follows the music. In Read, a note that's gone past the window turns the page to it.
function followNote(element) {
  if (!element) return;
  if (!reading() || (score?.view ?? "page") !== "page") { element.scrollIntoView({ block: "nearest", inline: "nearest" }); return; }
  const desk = $("desk"), room = desk.getBoundingClientRect(), box = element.getBoundingClientRect();
  if (box.left >= room.left && box.right <= room.right && box.top >= room.top && box.bottom <= room.bottom) return;
  const page = element.closest(".page");
  if (wholePages()) {
    // Whole pages turn to the row the note is on.
    desk.scrollTop = page.offsetTop - parseFloat(getComputedStyle($("pagesFrame")).paddingTop);
    drawPageCount();
    return;
  }
  if (fittedToWidth()) {
    // One below the other, the window moves down to the line being played, near its top.
    desk.scrollTop += box.top - room.top - room.height * 0.15;
    drawPageCount();
    return;
  }
  if (box.left < room.left || box.right > room.right) desk.scrollLeft = page.offsetLeft - parseFloat(getComputedStyle($("pagesFrame")).paddingLeft);
  const again = element.getBoundingClientRect();
  if (again.top < room.top || again.bottom > room.bottom) desk.scrollTop += again.top - room.top - room.height * 0.15;
  drawPageCount();
}

// A click on the score in Read turns the page: forward on its right half, back on its left.
function clickToTurn(event) {
  if (!reading() || event.button !== 0) return;
  // A drag that moved the pages about isn't a click.
  if (dragged) { dragged = false; return; }
  const room = $("desk").getBoundingClientRect();
  turnPage(event.clientX > room.left + room.width / 2 ? 1 : -1);
}

// Keys in Read: the page-turn keys, which a page-turning pedal also sends.
function readingKey(event) {
  const back = ["PageUp", "ArrowLeft", "ArrowUp", "Backspace"].includes(event.key) || (event.key === " " && event.shiftKey);
  const forward = ["PageDown", "ArrowRight", "ArrowDown", "Enter"].includes(event.key) || (event.key === " " && !event.shiftKey);
  if (back || forward) { event.preventDefault(); turnPage(forward ? 1 : -1); return true; }
  if (event.key === "Home" || event.key === "End") { event.preventDefault(); turnToEnd(event.key === "End"); return true; }
  return false;
}

// The command palette. Every action is listed once, with where it lives and its key.
function flatten(items, path, into) {
  for (const item of items ?? []) {
    if (!item) continue;
    if (item.items) { flatten(item.items, [...path, item.label], into); continue; }
    if (!item.run) continue;
    into.push({ label: item.label, where: path.join(" ▸ "), keys: item.keys ?? "", enabled: item.enabled !== false, run: item.run, checked: item.checked });
  }
  return into;
}

function noteCommands() {
  const target = Boolean(score && actionTarget());
  const note = (label, keys, run, needs = true) => ({ label, where: "Note", keys, enabled: Boolean(score) && (!needs || target || editor.on), run });
  return [
    note("Note input", "N", toggleInput, false),
    ...LENGTHS.map(({ key, type, label }) => note(label, key, () => chooseLength(type), false)),
    note("Dotted", ".", chooseDot, false),
    note("Rest", "0", () => (editor.on ? writeAt(null) : noteAction("delete"))),
    ...ACCIDENTAL_ACTIONS.map(([action, label]) => note(label.replace(/ \(.*\)$/, ""), (label.match(/\((.*)\)/) ?? [])[1] ?? "", () => noteAction(action))),
    note("Tie", "T", () => noteAction("tie")),
    note("Pitch up a semitone", "↑", () => noteAction("semitoneUp")),
    note("Pitch down a semitone", "↓", () => noteAction("semitoneDown")),
    note("Up an octave", "Ctrl+↑", () => noteAction("octaveUp")),
    note("Down an octave", "Ctrl+↓", () => noteAction("octaveDown")),
    note("Flip the stem", "X", () => noteAction("flip")),
    ...[1, 2, 3, 4].map(voice => note(`Voice ${voice}`, `Ctrl+Alt+${voice}`, () => chooseVoice(voice), false)),
    note("Lyrics", "Ctrl+L", startLyrics),
    note("Chord symbol", "Ctrl+K", addChordHere),
    note("Insert a measure at the end", "Ctrl+B", appendBar, false),
    note("New line at this measure", "Enter", () => barAction("break")),
  ];
}

// The cells that have a key of their own, so the palette can say so.
const CELL_KEYS = {
  "mark:staccato": "Shift+S", "mark:accent": "Shift+V", "mark:tenuto": "Shift+N", "mark:marcato": "Shift+O", slur: "S", "line:slur": "S",
  "hairpin:crescendo": "<", "hairpin:diminuendo": ">", "line:crescendo": "<", "line:diminuendo": ">", grace: "/",
  "text:staff": "Ctrl+T", "text:expression": "Ctrl+E", "text:rehearsal": "Ctrl+M", "layout:break": "Enter",
  "accidental:sharp": "+", "accidental:flat": "-", "accidental:natural": "=",
  "finger:type": "Ctrl+F", "mark:downbow": "[", "mark:upbow": "]",
  "cross:up": "Ctrl+Shift+↑", "cross:down": "Ctrl+Shift+↓",
};

function cellCommands() {
  const target = Boolean(score && actionTarget());
  const seen = new Set();
  const found = [];
  for (const palette of catalogue()) {
    for (const cell of palette.cells) {
      const label = cell.label.replace(/ \((dynamic)\)$/, "");
      if (seen.has(`${palette.name}/${label}`)) continue;
      seen.add(`${palette.name}/${label}`);
      found.push({ label, where: palette.name, keys: CELL_KEYS[cell.key] ?? "", enabled: Boolean(score) && (!cell.needs || target), run: cell.run, cell: cell.key });
    }
  }
  return found;
}

function practiceCommands() {
  if (!score) return [];
  const go = run => () => { if (!practising()) setMode("practice"); run(); };
  return [
    { label: "Loop on or off", where: "Practice", keys: "", run: go(toggleLoop) },
    { label: "Start the loop here (A)", where: "Practice", keys: "A", enabled: Boolean(actionTarget()), run: go(() => setLoopEnd("first")) },
    { label: "End the loop here (B)", where: "Practice", keys: "B", enabled: Boolean(actionTarget()), run: go(() => setLoopEnd("last")) },
    ...[0, 1, 2].map(bars => ({ label: bars ? `Count in ${bars} ${bars === 1 ? "bar" : "bars"}` : "No count-in", where: "Practice", keys: "", checked: practice.countIn === bars, run: go(() => setCountIn(bars)) })),
    ...[50, 75, 100].map(percent => ({ label: `Speed ${percent}%`, where: "Practice", keys: "", run: go(() => setSpeed(percent)) })),
    ...[["all", "Everything"], ...practiceChoices()].map(([key, label]) => ({ label: `Hear ${label === "Everything" ? "everything" : label}`, where: "Practice", keys: "", checked: practice.hear === key, run: go(() => setHear(key)) })),
    ...practiceChoices().map(([key, label]) => ({ label: `Wait for me: ${label}`, where: "Practice", keys: "", checked: practice.waitFor === key, run: go(() => setWaitFor(key)) })),
    { label: "Stop waiting for me", where: "Practice", keys: "", run: go(() => setWaitFor("off")) },
  ].map(item => ({ enabled: true, ...item }));
}

function allCommands() {
  const modes = Object.entries(MODES).map(([name, mode]) => ({ label: `${mode.label} mode`, where: "Mode", keys: mode.keys, enabled: Boolean(score), checked: currentMode() === name, run: () => setMode(name) }));
  const parts = score ? (score.parts ?? []).map((_, index) => ({ label: `Work with ${partLabel(index)}`, where: "Instrument", keys: "", enabled: true, checked: index === activePart(), run: () => switchPart(index) })) : [];
  const scores = sheets.map(sheet => ({ label: `Open ${sheet.status && sheet.status !== "ready" ? sheetName(sheet) : sheet.title || "Untitled score"}`, where: "Library", keys: "", enabled: true, run: () => openScore(sheet.id) }));
  const inspectorItems = [
    { label: "Inspector on the score", where: "View", keys: "F7", enabled: Boolean(score), run: () => showInspector(true, "score") },
    { label: "Play or stop", where: "Playback", keys: "Space", enabled: Boolean(score), run: togglePlaying },
    { label: "Rewind", where: "Playback", keys: "", enabled: Boolean(score), run: rewind },
    { label: "Metronome", where: "Playback", keys: "", enabled: Boolean(score), checked: playState.metronome, run: toggleMetronome },
  ];
  return [...modes, ...flatten(menuList(), [], []), ...inspectorItems, ...parts, ...noteCommands(), ...cellCommands(), ...techniqueCommands(), ...practiceCommands(), ...scores];
}

// Every word typed must start a word of the command, or be inside it; a command whose name starts
// with what's typed comes first.
function commandScore(command, words, typed) {
  const label = command.label.toLowerCase();
  const text = `${label} ${command.where.toLowerCase()} ${command.keys.toLowerCase()}`;
  let total = 0;
  for (const word of words) {
    const at = text.indexOf(word);
    if (at < 0) return -1;
    total += at === 0 ? 30 : /[\s:(▸+-]/.test(text[at - 1]) ? 20 : 5;
    if (label.includes(word)) total += 10;
  }
  if (label.startsWith(typed)) total += 40;
  if (!command.enabled) total -= 25;
  return total - label.length / 40;
}

function drawCommands() {
  const typed = $("commandInput").value.trim().toLowerCase();
  const words = typed.split(/\s+/).filter(Boolean);
  const scored = commands.list.map(command => ({ command, rank: words.length ? commandScore(command, words, typed) : command.enabled ? 1 : 0 })).filter(item => item.rank >= 0);
  if (words.length) scored.sort((a, b) => b.rank - a.rank);
  commands.shown = scored.slice(0, MOST_COMMANDS).map(item => item.command);
  commands.chosen = Math.min(commands.chosen, Math.max(0, commands.shown.length - 1));
  const rows = commands.shown.map((command, index) => {
    const row = document.createElement("li");
    row.setAttribute("role", "option");
    row.setAttribute("aria-selected", String(index === commands.chosen));
    row.setAttribute("aria-disabled", String(!command.enabled));
    if (command.cell) row.dataset.cell = command.cell;
    row.innerHTML = `<span class="what"></span><span class="where"></span>${command.keys ? keysHtml(command.keys) : ""}`;
    row.querySelector(".what").textContent = `${command.checked ? "✓ " : ""}${command.label}`;
    row.querySelector(".where").textContent = command.where;
    row.addEventListener("mousemove", () => { if (commands.chosen !== index) { commands.chosen = index; markChosenCommand(); } });
    row.addEventListener("click", () => runCommand(index));
    return row;
  });
  if (!rows.length) {
    const none = document.createElement("li");
    none.className = "none";
    none.textContent = "Nothing goes by that name.";
    rows.push(none);
  }
  $("commandList").replaceChildren(...rows);
}

function markChosenCommand() {
  [...$("commandList").children].forEach((row, index) => row.setAttribute("aria-selected", String(index === commands.chosen)));
  $("commandList").children[commands.chosen]?.scrollIntoView({ block: "nearest" });
}

function openCommands(typed = "") {
  if (document.querySelector("dialog[open]")) return;
  closeMenus();
  closeEditor();
  commands.list = allCommands();
  commands.chosen = 0;
  $("commandInput").value = typed;
  $("commandDialog").showModal();
  drawCommands();
  $("commandInput").focus();
}

function runCommand(index) {
  const command = commands.shown[index];
  if (!command || !command.enabled) return;
  $("commandDialog").close();
  command.run();
}

function setUpCommands() {
  $("commandOpen").addEventListener("click", () => openCommands());
  $("commandInput").addEventListener("input", () => { commands.chosen = 0; drawCommands(); });
  $("commandInput").addEventListener("keydown", event => {
    // The palette's keys are its own, and mustn't go on to the score once it has closed.
    if (["ArrowDown", "ArrowUp", "Enter"].includes(event.key)) event.stopPropagation();
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const count = commands.shown.length;
      if (count) commands.chosen = (commands.chosen + (event.key === "ArrowDown" ? 1 : -1) + count) % count;
      markChosenCommand();
    } else if (event.key === "Enter") {
      event.preventDefault();
      runCommand(commands.chosen);
    }
  });
  $("commandDialog").addEventListener("click", event => { if (event.target === $("commandDialog")) $("commandDialog").close(); });
}

function setUpWorkspace() {
  for (const button of document.querySelectorAll("#modeSwitch button")) button.addEventListener("click", () => { button.blur(); setMode(button.dataset.mode); });
  $("libraryToggle").addEventListener("click", toggleLibrary);
  $("libraryClose").addEventListener("click", toggleLibrary);
  $("partSwitch").addEventListener("click", event => {
    const owner = event.currentTarget;
    if (owner.getAttribute("aria-expanded") === "true") closeMenus(); else openMenu(owner, partItems());
  });
  $("desk").addEventListener("click", clickToTurn);
  $("desk").addEventListener("scroll", drawPageCount, { passive: true });
  $("desk").addEventListener("scroll", () => { if ((score?.view ?? "page") === "horizontal") drawStickyStart(); }, { passive: true });
  window.addEventListener("resize", drawStickyStart);
  window.addEventListener("resize", fitTopBar);
  $("welcomeNew")?.addEventListener("click", openNewScore);
  $("libraryToggle").setAttribute("aria-pressed", String(libraryShown()));
  for (const id of ["topBar", "dockHead"]) $(id).addEventListener("click", event => event.target.closest("button")?.blur());
  setUpCommands();
  setMode(readSetting("mode", "write"), false);
}

// The one-line view keeps the instruments' names, brackets, clefs and key signatures in sight as it
// scrolls, as MuseScore's continuous view does: a copy of the start of the line, drawn over the
// music at the left of the window, with the clef and key in force where the window begins.
const STICKY_GAP = 0.6; // Staff spaces after the key signature before the copy ends.

function stickyHost() {
  let host = $("stickyStart");
  if (!host) {
    host = document.createElement("div");
    host.id = "stickyStart";
    host.setAttribute("aria-hidden", "true");
    $("pagesFrame").append(host);
  }
  return host;
}

// The first measure's staves, each with its lines, clef and key signature.
function openingStaves(system) {
  const first = system.querySelector("g.measure");
  return first ? [...first.children].filter(child => child.classList.contains("staff")) : [];
}

function drawStickyStart() {
  const host = stickyHost();
  const page = (score?.view ?? "page") === "horizontal" ? document.querySelector("#pages .page") : null;
  const system = page?.querySelector("g.system");
  if (!system) { host.hidden = true; host.replaceChildren(); host.dataset.drawn = ""; return; }
  const outer = page.querySelector("svg");
  const staves = openingStaves(system);
  // Made again only when the page is: a scroll only moves it and changes its clefs and keys.
  const drawn = `${score.pages[0].length}:${outer.getBoundingClientRect().width.toFixed(1)}`;
  if (host.dataset.drawn !== drawn) {
    host.dataset.drawn = drawn;
    const copy = outer.cloneNode(false);
    let into = copy;
    // The page's own wrappers, with their scale and margins, down to the system.
    for (const wrapper of [...ancestorsOf(system, outer)].reverse()) {
      const shell = wrapper.cloneNode(false);
      shell.removeAttribute("id");
      into.append(shell);
      into = shell;
    }
    const start = document.createElementNS("http://www.w3.org/2000/svg", "g");
    for (const child of system.children) {
      if (child.tagName === "path" || child.matches("g.grpSym, g.label, g.labelAbbr, g.staffDef")) start.append(child.cloneNode(true));
    }
    staves.forEach((staff, number) => {
      const shell = staff.cloneNode(false);
      shell.removeAttribute("id");
      shell.dataset.staff = number;
      for (const child of staff.children) {
        if (child.tagName === "path" || child.matches("g.clef, g.keySig")) shell.append(child.cloneNode(true));
      }
      start.append(shell);
    });
    into.append(start);
    copy.removeAttribute("width");
    copy.removeAttribute("height");
    copy.removeAttribute("id"); // Its style sheet is the page's, found by the class each copy keeps.
    copy.classList.add("stickyCopy");
    copy.style.width = `${outer.getBoundingClientRect().width}px`;
    copy.style.height = `${outer.getBoundingClientRect().height}px`;
    host.replaceChildren(copy);
    // As wide as the names, brackets, clefs and keys, and a little more.
    const pageLeft = outer.getBoundingClientRect().left;
    const ends = staves.flatMap(staff => [...staff.querySelectorAll(":scope > g.clef, :scope > g.keySig")].map(element => element.getBoundingClientRect().right));
    const lines = staves[0] ? staffLines(staves[0]) : null;
    host.style.width = `${Math.max(...ends, pageLeft) - pageLeft + (lines ? lines.step * 2 * STICKY_GAP : 8)}px`;
  }
  placeStickyStart(host, page);
}

function* ancestorsOf(element, top) {
  for (let found = element.parentElement; found && found !== top; found = found.parentElement) yield found;
}

// Shown once the start of the line has gone off the left of the window, at the window's left edge,
// with each staff's clef and key signature as they are where the window now begins.
function placeStickyStart(host, page) {
  const desk = $("desk");
  const frame = $("pagesFrame").getBoundingClientRect();
  const pageBox = page.getBoundingClientRect();
  const left = desk.getBoundingClientRect().left;
  host.hidden = pageBox.left >= left - 1;
  if (host.hidden) return;
  host.style.left = `${left - frame.left}px`;
  host.style.top = `${pageBox.top - frame.top}px`;
  host.style.height = `${pageBox.height}px`;
  host.dataset.width ??= host.style.width;
  const edge = left + parseFloat(host.dataset.width);
  const system = page.querySelector("g.system");
  const measures = [...system.querySelectorAll(":scope > g.measure")];
  openingStaves(system).forEach((staff, number) => {
    const shell = host.querySelector(`[data-staff="${number}"]`);
    if (!shell) return;
    for (const kind of ["clef", "keySig"]) {
      const first = staff.querySelector(`:scope > g.${kind}`);
      let latest = first;
      for (const measure of measures) {
        if (measure.getBoundingClientRect().left > edge) break;
        const there = [...measure.children].filter(child => child.classList.contains("staff"))[number];
        // A clef change is drawn at the end of the measure before, in its voice.
        for (const element of there?.querySelectorAll(`:scope > g.${kind}, :scope > g.layer > g.${kind}`) ?? []) {
          if (element.getBoundingClientRect().left < edge) latest = element;
        }
      }
      const shown = shell.querySelector(`:scope > g.${kind}`);
      const wanted = latest && latest !== first ? latest.id : "first";
      if ((shown?.dataset.from ?? "first") === wanted && (shown || !first)) continue;
      shown?.remove();
      const made = wanted === "first" ? first?.cloneNode(true) : movedTo(latest, first, kind, staff);
      if (!made) continue;
      made.dataset.from = wanted;
      shell.append(made);
    }
  });
  // A key signature with more sharps or flats than the first needs more room.
  const right = Math.max(...[...host.querySelectorAll("g.clef, g.keySig")].map(element => element.getBoundingClientRect().right));
  const space = staffLines(openingStaves(system)[0]).step * 2;
  host.style.width = `${Math.max(parseFloat(host.dataset.width), right - left + space * STICKY_GAP)}px`;
}

// The full clef a smaller one drawn at a clef change stands for, as SMuFL numbers them.
const FULL_CLEFS = { E07A: "E050", E07B: "E05C", E07C: "E062" };

// A clef or key signature from later in the line, drawn where the line's first one is: a clef at the
// size and place of the first, a key signature where the first begins, or just after the clef.
function movedTo(later, first, kind, staff) {
  const made = later.cloneNode(true);
  if (kind === "clef") {
    const use = made.querySelector("use"), at = first?.querySelector("use");
    const [, x, , scale] = /translate\(([-\d.]+),\s*([-\d.]+)\)\s*scale\(([-\d.]+)/.exec(at?.getAttribute("transform") ?? "") ?? [];
    const y = /translate\([-\d.]+,\s*([-\d.]+)\)/.exec(use?.getAttribute("transform") ?? "")?.[1];
    if (use && x !== undefined && y !== undefined) use.setAttribute("transform", `translate(${x}, ${y}) scale(${scale}, ${scale})`);
    const [whole, code, rest] = /^#(E[0-9A-F]{3})(-.*)$/.exec(use?.getAttribute("xlink:href") ?? "") ?? [];
    if (whole && FULL_CLEFS[code] && document.getElementById(`${FULL_CLEFS[code]}${rest}`)) use.setAttribute("xlink:href", `#${FULL_CLEFS[code]}${rest}`);
    return made;
  }
  // Placed by the symbols' own positions, as a group's box can't be trusted with <use> inside it.
  const xs = element => [...(element?.querySelectorAll("use") ?? [])].map(use => Number(/translate\(([-\d.]+)/.exec(use.getAttribute("transform") ?? "")?.[1])).filter(Number.isFinite);
  const lines = [...staff.children].filter(child => child.tagName === "path").map(line => Number(/M[-\d.]+ ([-\d.]+)/.exec(line.getAttribute("d") ?? "")?.[1]));
  const space = lines.length > 1 ? lines[1] - lines[0] : 0;
  const clef = xs(staff.querySelector(":scope > g.clef"));
  const from = xs(first).length ? Math.min(...xs(first)) : clef.length ? Math.max(...clef) + CLEF_SPACES * space : Math.min(...xs(later));
  made.setAttribute("transform", `translate(${from - Math.min(...xs(later))} 0)`);
  return made;
}

const CLEF_SPACES = 3.2; // A clef's width and the gap after it, in staff spaces, where a key signature begins.
