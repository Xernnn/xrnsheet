// The workspace: Read, Write and Practice, the instrument switcher, the library beside the score,
// turning pages in Read, and the command palette, which finds every action the app has by name.
const MODES = {
  read: { label: "Read", keys: "Alt+R" },
  write: { label: "Write", keys: "Alt+W" },
  practice: { label: "Practice", keys: "Alt+P" },
};
const TURN_SHARE = 0.9; // How much of the window a turn moves on, when a page is taller than the window.
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
  zoom = readSetting(zoomSetting(), usualZoom());
  setZoom(zoom);
  drawDock();
  if (score) updateTools();
  drawMidiState();
  drawPageCount();
}

// The library sits beside an open score on a wide window, and over it on a narrow one.
function libraryShown() {
  if (document.body.classList.contains("atHome")) return true;
  return innerWidth > 1180 ? !document.body.classList.contains("libraryHidden") : document.body.classList.contains("libraryOpen");
}

function toggleLibrary() {
  if (document.body.classList.contains("atHome")) return;
  const on = !libraryShown();
  if (innerWidth > 1180) {
    document.body.classList.toggle("libraryHidden", !on);
    keepSetting("library", on ? "shown" : "hidden");
  } else {
    document.body.classList.toggle("libraryOpen", on);
  }
  $("libraryToggle").setAttribute("aria-pressed", String(on));
  if (on) drawHistory();
  if (fittedZoom()) setZoom(zoom);
  drawCursor();
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
    ...(score?.parts ?? []).map((_, index) => ({ label: `${partLabel(index)} · ${KINDS[kindOfPart(index)].label.toLowerCase()}`, checked: index === activePart(), run: () => choosePart(index) })),
    null,
    { label: "Add an instrument…", keys: "I", run: openAddPart },
  ];
}

// Pages in Read: the pages a window holds at once, turned together, and a page taller than the
// window read down before it's turned.
function pageElements() {
  return [...document.querySelectorAll("#pages .page")];
}

function pagesInView() {
  const desk = $("desk");
  const pages = pageElements();
  const left = desk.scrollLeft, right = left + desk.clientWidth;
  const shown = pages.map((page, index) => ({ page, index })).filter(({ page }) => page.offsetLeft + page.offsetWidth / 2 > left && page.offsetLeft + page.offsetWidth / 2 < right);
  return { pages, shown: shown.length ? shown.map(item => item.index) : [0] };
}

function turnPage(by) {
  const desk = $("desk");
  const view = score?.view ?? "page";
  if (view === "vertical") { desk.scrollTop += by * desk.clientHeight * TURN_SHARE; drawPageCount(); return; }
  if (view === "horizontal") { desk.scrollLeft += by * desk.clientWidth * TURN_SHARE; drawPageCount(); return; }
  const { pages, shown } = pagesInView();
  if (!pages.length) return;
  const page = pages[shown[0]];
  const bottom = page.offsetTop + page.offsetHeight;
  if (by > 0 && desk.scrollTop + desk.clientHeight < bottom - 4) { desk.scrollTop += desk.clientHeight * TURN_SHARE; drawPageCount(); return; }
  if (by < 0 && desk.scrollTop > 4) { desk.scrollTop -= desk.clientHeight * TURN_SHARE; drawPageCount(); return; }
  const target = Math.min(Math.max(shown[0] + by * shown.length, 0), pages.length - 1);
  desk.scrollLeft = pages[target].offsetLeft - parseFloat(getComputedStyle($("pagesFrame")).paddingLeft);
  desk.scrollTop = 0;
  drawPageCount();
}

function turnToEnd(last) {
  const pages = pageElements();
  if (!pages.length) return;
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
  "finger:type": "Ctrl+F",
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
    ...[["all", "Everything"], ...practiceChoices()].map(([key, label]) => ({ label: `Hear ${label === "Everything" ? "everything" : label}`, where: "Practice", keys: "", checked: practice.hear === key, run: go(() => { practice.hear = key; drawPracticeChoices(); }) })),
    ...practiceChoices().map(([key, label]) => ({ label: `Wait for me: ${label}`, where: "Practice", keys: "", checked: practice.waitFor === key, run: go(() => { practice.waitFor = key; drawPracticeChoices(); setUpMidi(); drawMidiState(); }) })),
    { label: "Stop waiting for me", where: "Practice", keys: "", run: go(() => { practice.waitFor = "off"; drawPracticeChoices(); drawMidiState(); }) },
  ];
}

function allCommands() {
  const modes = Object.entries(MODES).map(([name, mode]) => ({ label: `${mode.label} mode`, where: "Mode", keys: mode.keys, enabled: Boolean(score), checked: currentMode() === name, run: () => setMode(name) }));
  const parts = score ? (score.parts ?? []).map((_, index) => ({ label: `Work with ${partLabel(index)}`, where: "Instrument", keys: "", checked: index === activePart(), run: () => choosePart(index) })) : [];
  const scores = sheets.map(sheet => ({ label: `Open ${sheet.title || "Untitled score"}`, where: "Library", keys: "", run: () => openScore(sheet.id) }));
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
  $("welcomeNew")?.addEventListener("click", openNewScore);
  if (readSetting("library", "shown") === "hidden") document.body.classList.add("libraryHidden");
  $("libraryToggle").setAttribute("aria-pressed", String(libraryShown()));
  for (const id of ["topBar", "dockHead"]) $(id).addEventListener("click", event => event.target.closest("button")?.blur());
  setUpCommands();
  setMode(readSetting("mode", "write"), false);
}
