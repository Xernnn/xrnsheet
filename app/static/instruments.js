// The instrument along the bottom: a piano's keyboard, a guitar's fretboard or a violin's
// fingerboard, for whichever part is chosen. It lights the selected notes and the notes sounding,
// and while entering notes a key, a fret or a place on a string writes that pitch, and Shift adds it
// to the chord. In Practice it's also what you play into when playback waits for you.
const KINDS = {
  piano: { label: "Keyboard", player: "pianist", icon: "piano", panel: "pianoPanel" },
  guitar: { label: "Fretboard", player: "guitarist", icon: "guitar", panel: "fretboardPanel" },
  violin: { label: "Fingerboard", player: "violinist", icon: "violin", panel: "fingerboardPanel" },
  other: { label: "Keyboard", player: "player", icon: "music", panel: "pianoPanel" },
};
const LOWEST_KEY = 21, HIGHEST_KEY = 108; // A piano's 88 keys, A0 to C8.
const BLACK_KEYS = new Set([1, 3, 6, 8, 10]);
const NARROWEST_KEY = 10, WIDEST_KEY = 44; // A white key's width in pixels, which Ctrl and the wheel change.
const PREVIEW_SECONDS = 0.4; // A note selected is heard this long, as MuseScore plays one.
// Keys held: a key below, a fret or a MIDI keyboard's key sounds from when it's pressed until it's let
// go, as an instrument's does, not for a set time. A MIDI keyboard's sustain pedal (controller 64)
// keeps the keys let go sounding until it comes up; "all notes off" (123) lets everything go.
const POINTER_KEY = "pointer"; // The mouse or a finger holds one key at a time.
const HELD_VOLUME = 0.3; // A key below, as loud as a note selected; a MIDI key by how hard it's struck.
const SUSTAIN_PEDAL = 64, ALL_NOTES_OFF = 123;
const held = { voices: new Map(), pedal: false, pedalled: new Set() };
// Strings from the highest, as tablature draws them: a guitar in standard tuning, a violin in fifths.
const GUITAR = { strings: [64, 59, 55, 50, 45, 40], names: ["E", "B", "G", "D", "A", "E"], stops: 19, inlays: [3, 5, 7, 9, 15, 17, 19], double: [12] };
// A bowed instrument's hand: the stop its first finger is at in each position, from half position to
// XII, how far the hand reaches above it, the finger for each stop above it, and where first
// position's finger tapes go. A violinist's hand spans a fourth, a cellist's a minor third, and a
// double bassist's a whole tone, fingered 1, 2 and 4, as Simandl teaches it.
const HANDS = {
  violin: { first: [1, 2, 4, 5, 7, 9, 10, 12, 14, 16, 17, 19, 21], reach: 5, fingers: [[0, "1"], [2, "2"], [4, "3"], [6, "4"]], tapes: [2, 4, 5, 7] },
  cello: { first: [1, 2, 3, 5, 7, 8, 10, 12, 14, 15, 17, 19, 20], reach: 3, fingers: [[0, "1"], [1, "2"], [2, "3"], [4, "4"]], tapes: [2, 3, 4, 5] },
  bass: { first: [1, 2, 3, 5, 7, 8, 10, 12, 14, 15, 17, 19, 20], reach: 2, fingers: [[0, "1"], [1, "2"], [3, "4"]], tapes: [2, 3, 4] },
};
const ORDINALS = ["", "1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th", "10th", "11th", "12th"];
// A fingerboard: its strings from the highest, to the fourth finger in twelfth position, each position
// named where its first finger goes.
function bowedNeck(hand, strings, names) {
  return { strings, names, hand, stops: hand.first.at(-1) + hand.reach, positions: Object.fromEntries(hand.first.slice(1).map((stop, number) => [stop, ORDINALS[number + 1]])), tapes: hand.tapes };
}
const VIOLIN = bowedNeck(HANDS.violin, [76, 69, 62, 55], ["E", "A", "D", "G"]);
const SHARP_NAMES = ["C", "C♯", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B"];
// A key's width is whatever fills the panel, until Ctrl and the wheel choose one.
// The notes sounding and awaited are kept by pitch for the keys, and with their ids for a neck.
const keyboard = { width: Number(readSetting("keyWidth", "0")) || 0, sounding: new Map(), soundingNotes: new Map(), expected: new Set(), expectedNotes: [] };
let keyPreview = null;
const fingerings = new Map(); // Where a set of pitches is played on the strings, worked out once.

function noteName(midi) {
  return `${SHARP_NAMES[midi % 12]}${Math.floor(midi / 12) - 1}`;
}

// What kind of instrument a part is: a guitar, bass or anything else with frets has a fretboard, and
// otherwise it goes by its name and, once playback has been fetched, its sound.
function kindOfPart(index) {
  const name = (score?.parts?.[index] ?? "").toLowerCase();
  if (score?.fretted?.[index]) return "guitar";
  if (/violin|viola|fiddle|cello|double bass|contrabass/.test(name)) return "violin";
  if (/bass/.test(name)) return /piano|keyboard/.test(name) ? "piano" : "other";
  if (/guitar/.test(name)) return "guitar";
  if (/piano|keyboard|harpsichord|organ|celesta|clavi|accordion|marimba/.test(name)) return "piano";
  const program = playState.data?.programs?.[index];
  if (program >= 25 && program <= 32) return "guitar";
  if (program >= 41 && program <= 44) return "violin";
  if ((program >= 1 && program <= 8 && program !== 1) || (score?.partStaves?.[index] ?? 1) > 1) return "piano";
  return "other";
}

// Who plays a part, as the inspector names them: a bass's bassist, a guitar's guitarist.
function playerOf(index) {
  const name = (score?.parts?.[index] ?? "").toLowerCase();
  if (/bass/.test(name) && kindOfPart(index) === "guitar") return "bassist";
  if (/double bass|contrabass/.test(name)) return "double bassist";
  if (/cello/.test(name)) return "cellist";
  if (/viola/.test(name)) return "violist";
  if (/ukulele|banjo|mandolin/.test(name)) return `${name.match(/ukulele|banjo|mandolin/)[0]} player`;
  return KINDS[kindOfPart(index)].player;
}

function partLabel(index) {
  return score?.parts?.[index] || `Instrument ${index + 1}`;
}

function activePart() {
  const count = score?.parts?.length ?? 0;
  return chosenPart !== null && chosenPart < count ? chosenPart : 0;
}

function activeKind() {
  return kindOfPart(activePart());
}

function instrumentLabel() {
  return KINDS[score ? activeKind() : "piano"].label;
}

// Choose the instrument to work with: the panel below shows it, and practice follows it.
function choosePart(index, redraw = true) {
  chosenPart = index;
  drawParts();
  drawPartSwitch();
  drawPracticeChoices();
  if (dockOpen("instrument")) drawDock();
  if (redraw) lightKeys();
}

// An instrument chosen by name, in the switcher, the palette or the score's list, is the one worked
// with from then on: a note selected in another one is let go, as it would choose its own part
// back, and while writing the cursor goes to that instrument, in the same measure.
function switchPart(index) {
  choosePart(index);
  return enqueue(async () => {
    if (editor.on) {
      const here = document.getElementById(plainId(editor.cursor) ?? "");
      const measure = here?.closest("g.measure");
      if (measure && partOfStaff(staffNumber(here)) !== index) {
        const staff = [...measure.children].filter(child => child.classList.contains("staff"))[firstStaffOf(index)];
        const first = staff && [...staff.querySelectorAll(EVENTS)].find(element => stepOf(element) === element);
        if (first) { editor.last = null; moveCursor(first.id); }
      }
    } else {
      const chosen = document.getElementById(actionTarget() ?? "");
      if (chosen && partOfStaff(staffNumber(chosen)) !== index) select(null);
    }
    choosePart(index);
  });
}

// The pitch a staff shows for one that sounds, and back: they differ for a transposing instrument
// unless the score is shown at concert pitch. The score says how far each part transposes, so a
// note played on a bass's fretboard lands right before playback has been fetched.
function partShift(part) {
  return playState.data?.transpositions?.[part] ?? score?.transpositions?.[part] ?? 0;
}

function shownPitch(sounding, part) {
  return sounding - (score?.concertPitch ? 0 : partShift(part));
}

function soundingPitch(shown, part) {
  return shown + (score?.concertPitch ? 0 : partShift(part));
}

// The key a written note is shown on: as written, or where it sounds at concert pitch.
function shownKey(data, written, part) {
  return written + (score?.concertPitch ? data.transpositions?.[part] ?? 0 : 0);
}

// And the key a sounding note is shown on, for playback.
function keyOfSound(data, midi, part) {
  return midi - (score?.concertPitch ? 0 : data.transpositions?.[part] ?? 0);
}

function instrumentShown() {
  return dockOpen("instrument") && !$("dock").hidden;
}

// A part's neck: a fretted part's strings as the score tunes them, with its frets and capo.
// Which hand plays a bowed part: a cello's, a double bass's, or a violin's and a viola's.
function handOf(part) {
  const name = (score?.parts?.[part] ?? "").toLowerCase();
  const program = playState.data?.programs?.[part];
  if (/double bass|contrabass/.test(name) || program === 44) return HANDS.bass;
  if (/cello/.test(name) || program === 43) return HANDS.cello;
  return HANDS.violin;
}

function neckOf(part, kind) {
  if (kind !== "guitar") {
    const strings = bowedStrings(part);
    const hand = handOf(part);
    if ((!strings || strings.join() === "G3,D4,A4,E5") && hand === HANDS.violin) return VIOLIN;
    const highFirst = [...(strings ?? ["G3", "D4", "A4", "E5"])].reverse();
    return bowedNeck(hand, highFirst.map(midiOfName), highFirst.map(letterOf));
  }
  const board = score?.fretted?.[part];
  if (!board) return GUITAR;
  const highFirst = [...board.strings].reverse();
  return {
    strings: highFirst.map(midiOfName), names: highFirst.map(letterOf), stops: board.frets, capo: board.capo ?? 0,
    inlays: [3, 5, 7, 9, 15, 17, 19, 21].filter(stop => stop <= board.frets), double: [12, 24].filter(stop => stop <= board.frets),
  };
}

// The panel for the chosen part's kind, made again only when its strings, frets or capo change.
function drawInstrument() {
  const kind = activeKind();
  for (const name of ["pianoPanel", "fretboardPanel", "fingerboardPanel"]) $(name).hidden = name !== KINDS[kind].panel;
  if (kind === "guitar") drawNeck($("fretboard"), neckOf(activePart(), kind), "guitar");
  else if (kind === "violin") drawNeck($("fingerboard"), neckOf(activePart(), kind), "violin");
  else drawKeyboard();
  lightKeys();
}

function keyWidth() {
  if (keyboard.width) return keyboard.width;
  const room = $("dock").clientWidth - 24;
  return Math.max(NARROWEST_KEY + 6, Math.min(WIDEST_KEY, Math.floor(room / 52)));
}

function drawKeyboard() {
  const keys = $("pianoKeys");
  keys.style.setProperty("--key", `${keyWidth()}px`);
  if (!keys.childElementCount) {
    let whites = 0;
    for (let midi = LOWEST_KEY; midi <= HIGHEST_KEY; midi++) {
      const key = document.createElement("button");
      const black = BLACK_KEYS.has(midi % 12);
      key.className = `key ${black ? "black" : "white"}`;
      key.dataset.midi = midi;
      key.tabIndex = -1;
      key.setAttribute("aria-label", noteName(midi));
      key.title = noteName(midi);
      if (black) key.style.setProperty("--at", String(whites));
      else key.style.setProperty("--at", String(whites++));
      if (midi % 12 === 0) key.innerHTML = `<span>C${midi / 12 - 1}</span>`;
      key.addEventListener("pointerdown", event => { event.preventDefault(); pressKey(midi, event.shiftKey); });
      key.addEventListener("pointerup", () => { letGo(POINTER_KEY); practiseReleased(midi); });
      for (const away of ["pointercancel", "pointerleave"]) key.addEventListener(away, () => letGo(POINTER_KEY));
      keys.append(key);
    }
    keys.style.setProperty("--whites", String(whites));
    requestAnimationFrame(() => {
      const middle = keys.querySelector('[data-midi="60"]');
      $("pianoPanel").scrollLeft = middle.offsetLeft - $("pianoPanel").clientWidth / 2;
    });
  }
}

// A neck: each string a row, each stop on it a button, from the open string at the nut. A capo
// covers the frets behind it, and the open strings sound at its fret.
function drawNeck(holder, neck, kind) {
  const key = `${kind}:${neck.strings.join(",")}:${neck.stops}:${neck.capo ?? 0}`;
  if (holder.childElementCount && holder.dataset.neck === key) return;
  holder.dataset.neck = key;
  const capo = neck.capo ?? 0;
  holder.className = `neck ${kind}`;
  holder.style.setProperty("--strings", String(neck.strings.length));
  holder.style.setProperty("--stops", String(neck.stops));
  // A guitar's frets close up towards the body, each about 5.6% narrower than the one before.
  const shares = Array.from({ length: neck.stops }, (_, index) => (kind === "guitar" ? 0.944 ** index : 1));
  holder.style.setProperty("--columns", shares.map(share => `minmax(${Math.round(30 * share)}px, ${share.toFixed(3)}fr)`).join(" "));
  const pieces = [];
  const place = (element, column, row, span = 1) => {
    element.style.gridColumn = `${column} / span ${span}`;
    if (row) element.style.gridRow = String(row);
    pieces.push(element);
    return element;
  };
  const board = document.createElement("div");
  board.className = "board";
  pieces.push(board);
  for (let stop = 1; stop <= neck.stops; stop++) {
    if (kind === "guitar") place(Object.assign(document.createElement("i"), { className: "wire" }), stop + 2);
    if (neck.inlays?.includes(stop)) place(Object.assign(document.createElement("i"), { className: "inlay" }), stop + 2);
    if (neck.double?.includes(stop)) place(Object.assign(document.createElement("i"), { className: "inlay double" }), stop + 2);
    if (neck.tapes?.includes(stop)) place(Object.assign(document.createElement("i"), { className: "tape" }), stop + 2);
  }
  if (capo) {
    const bar = place(Object.assign(document.createElement("i"), { className: "capo", title: `Capo on fret ${capo}` }), capo + 2);
    bar.style.gridRow = `1 / span ${neck.strings.length}`;
  }
  neck.strings.forEach((open, index) => {
    const row = index + 1;
    const name = place(document.createElement("span"), 1, row);
    name.className = "stringName";
    name.textContent = neck.names[index];
    const line = place(document.createElement("i"), 2, row, neck.stops + 1);
    line.className = "stringLine";
    line.style.setProperty("--gauge", `${1 + index * (kind === "guitar" ? 0.45 : 0.35)}px`);
    for (let stop = 0; stop <= neck.stops; stop++) {
      const spot = place(document.createElement("button"), stop + 2, row);
      const midi = open + stop;
      spot.className = `spot${stop === 0 ? " open" : ""}${stop < capo ? " blocked" : ""}`;
      spot.tabIndex = -1;
      Object.assign(spot.dataset, { string: String(index + 1), stop: String(stop), midi: String(midi) });
      const fromCapo = capo && stop >= capo ? ` (${stop === capo ? "open with the capo" : `${stop - capo} from the capo`})` : "";
      const where = kind === "guitar" ? (stop ? `fret ${stop}${fromCapo}` : "open") : (stop ? `${stop} semitones up` : "open");
      spot.title = stop < capo ? `${neck.names[index]} string, fret ${stop}: behind the capo` : `${neck.names[index]} string, ${where}: ${noteName(midi)}`;
      spot.setAttribute("aria-label", spot.title);
      if (stop < capo) spot.disabled = true;
      spot.addEventListener("pointerdown", event => { event.preventDefault(); pressKey(shownPitch(midi, activePart()), event.shiftKey, { string: index + 1, stop }); });
      spot.addEventListener("pointerup", () => { letGo(POINTER_KEY); practiseReleased(shownPitch(midi, activePart())); });
      for (const away of ["pointercancel", "pointerleave"]) spot.addEventListener(away, () => letGo(POINTER_KEY));
    }
  });
  for (let stop = 0; stop <= neck.stops; stop++) {
    const label = kind === "guitar" ? ([0, ...neck.inlays, ...neck.double].includes(stop) ? String(stop) : "") : neck.positions[stop] ?? "";
    if (!label) continue;
    const number = place(document.createElement("span"), stop + 2, neck.strings.length + 1);
    number.className = "number";
    number.textContent = label;
  }
  holder.replaceChildren(...pieces);
  holder.closest(".dockPanel").scrollLeft = 0;
}

// Where pitches are played on the strings: each on a string of its own, as close together and as
// near the nut as they'll go, which is how a chord is fingered in the lowest position it fits.
function fingering(neck, pitches) {
  const wanted = [...new Set(pitches)].sort((a, b) => b - a);
  const key = `${neck.strings.join(",")}/${neck.stops}:${wanted.join(",")}`;
  if (fingerings.has(key)) return fingerings.get(key);
  let best = null;
  const choose = (index, used, chosen) => {
    if (index === wanted.length) {
      const fretted = chosen.filter(spot => spot.stop > 0).map(spot => spot.stop);
      const span = fretted.length ? Math.max(...fretted) - Math.min(...fretted) : 0;
      const cost = span * 10 + chosen.reduce((total, spot) => total + spot.stop, 0);
      if (!best || cost < best.cost) best = { cost, spots: [...chosen] };
      return;
    }
    let placed = false;
    neck.strings.forEach((open, string) => {
      const stop = wanted[index] - open;
      if (used.has(string) || stop < 0 || stop > neck.stops) return;
      placed = true;
      used.add(string);
      chosen.push({ string: string + 1, stop, midi: wanted[index] });
      choose(index + 1, used, chosen);
      chosen.pop();
      used.delete(string);
    });
    if (!placed) choose(index + 1, used, chosen); // Out of reach, or more notes than strings: left out.
  };
  choose(0, new Set(), []);
  const spots = best?.spots ?? [];
  fingerings.set(key, spots);
  return spots;
}

// The finger a violinist uses in first position, from the open string up: 0 is the open string.
function violinFinger(stop) {
  return stop === 0 ? "0" : stop <= 2 ? "1" : stop <= 4 ? "2" : stop <= 5 ? "3" : stop <= 7 ? "4" : "";
}

// The selected notes of the chosen part, each with its id and its pitch as it sounds. They're read
// from the score as the page has it, so a note just written or changed is lit at once.
function chosenNotes() {
  const found = [];
  for (const id of range ? range.ids : [actionTarget()].filter(Boolean)) {
    const element = document.getElementById(id);
    const notes = element?.matches("g.note") ? [element] : [];
    for (const note of notes) {
      const [step, alter, octave] = score?.notes?.[ownId(note.id)] ?? [];
      const part = partOfStaff(staffNumber(note));
      if (step && part === activePart()) found.push({ id: ownId(note.id), midi: soundingPitch(12 * (octave + 1) + STEP_SEMITONES[step] + alter, part) });
    }
  }
  return [...new Map(found.map(note => [note.id, note])).values()];
}

function chosenPitches() {
  return chosenNotes().map(note => note.midi);
}

// Where the score says a note is played, as a string and a stop from the nut: on a guitar from its
// tablature or its string number, and on a violin from its string number, the "sul G" over it or the
// position it's in. Without a word from the score it's left for fingering() to place.
function storedSpot(neck, kind, note) {
  const played = playedAs(note.id);
  if (kind === "guitar" && played.string) return { string: played.string, stop: played.fret + (neck.capo ?? 0), midi: note.midi };
  const named = kind === "violin" && played.sul ? neck.names.indexOf(letterOf(played.sul)) + 1 : 0;
  const string = played.mark || named || null;
  if (string) {
    const stop = note.midi - neck.strings[string - 1];
    return stop >= 0 && stop <= neck.stops ? { string, stop, midi: note.midi } : null;
  }
  if (kind === "violin" && played.position !== undefined) return inPosition(neck, note.midi, played.position);
  return null;
}

// Where a note falls under the hand in a position: on the string where it's between a low first
// finger and a stretched fourth, nearest the middle of the hand, or on an open string.
function inPosition(neck, midi, position) {
  const hand = neck.hand ?? HANDS.violin;
  const first = hand.first[position] ?? hand.first.at(-1);
  let best = null;
  neck.strings.forEach((open, index) => {
    const stop = midi - open;
    if (stop < first - 1 || stop > first + hand.reach + 1 || stop > neck.stops) return;
    const away = Math.abs(stop - first - hand.reach / 2);
    if (!best || away < best.away) best = { string: index + 1, stop, midi, away };
  });
  if (best) return best;
  const open = neck.strings.indexOf(midi);
  return open >= 0 ? { string: open + 1, stop: 0, midi } : null;
}

// The finger a string player uses at a stop in a position: 1 from a half step below where the first
// finger goes, up to 4 at the top of the hand, a fourth above it on a violin.
function fingerInPosition(position, stop, hand = HANDS.violin) {
  if (stop === 0) return "0";
  const above = stop - (hand.first[position] ?? hand.first[1]);
  return hand.fingers.find(([most]) => above <= most)?.[1] ?? "";
}

// Place notes on a neck: those the score places where it says, the rest as fingering() finds best,
// from the capo up.
function placeOnNeck(neck, kind, notes) {
  const spots = [];
  const left = [];
  for (const note of notes) {
    const stored = note.id ? storedSpot(neck, kind, note) : null;
    if (stored) spots.push({ ...stored, id: note.id }); else left.push(note);
  }
  const capo = neck.capo ?? 0;
  const fromCapo = capo ? { ...neck, strings: neck.strings.map(open => open + capo), stops: neck.stops - capo } : neck;
  const free = { ...fromCapo, strings: fromCapo.strings.map((open, index) => (spots.some(spot => spot.string === index + 1) ? -100 : open)) };
  for (const spot of fingering(free, left.map(note => note.midi))) spots.push({ ...spot, stop: spot.stop + capo, id: left.find(note => note.midi === spot.midi)?.id });
  return spots;
}

// What a lit place on a neck shows: a violinist's finger, or the note's name.
function spotLabel(kind, spot, element) {
  const name = noteName(Number(element.dataset.midi)).replace(/\d+$/, "");
  if (kind !== "violin") return name;
  const played = spot.id ? playedAs(spot.id) : {};
  const stop = Number(element.dataset.stop);
  const hand = handOf(activePart());
  // Unmarked, a note is in first position, or half position below it.
  const finger = played.position !== undefined ? fingerInPosition(played.position, stop, hand) : hand === HANDS.violin ? violinFinger(stop) : fingerInPosition(stop < hand.first[1] ? 0 : 1, stop, hand);
  return played.finger || finger || name;
}

// Light the selected notes, the notes sounding and the notes playback is waiting for.
function lightKeys() {
  if (!instrumentShown()) return;
  const kind = activeKind();
  const part = activePart();
  const chosen = chosenPitches();
  const sounding = [...keyboard.sounding].filter(([, count]) => count > 0).map(([midi]) => midi);
  if (kind === "guitar" || kind === "violin") {
    const neck = neckOf(part, kind);
    const holder = kind === "guitar" ? $("fretboard") : $("fingerboard");
    const soundingNotes = [...keyboard.soundingNotes.values()].filter(note => note.count > 0);
    const marks = new Map();
    const mark = (spots, name) => spots.forEach(spot => marks.set(`${spot.string}/${spot.stop}`, { name, spot }));
    mark(placeOnNeck(neck, kind, chosenNotes()), "chosen");
    mark(placeOnNeck(neck, kind, [...keyboard.expectedNotes]), "expected");
    mark(placeOnNeck(neck, kind, soundingNotes), "sounding");
    holder.querySelectorAll(".spot").forEach(element => {
      const lit = marks.get(`${element.dataset.string}/${element.dataset.stop}`);
      for (const name of ["chosen", "expected", "sounding"]) element.classList.toggle(name, lit?.name === name);
      element.dataset.show = lit ? spotLabel(kind, lit.spot, element) : "";
    });
    showChosenSpot(holder);
    return;
  }
  // A keyboard key is the pitch the staff shows, so at concert pitch it's the one that sounds.
  const shown = new Set(chosen.map(pitch => shownPitch(pitch, part)));
  const lit = new Set(sounding.map(pitch => shownPitch(pitch, part)));
  const expected = new Set([...keyboard.expected].map(pitch => shownPitch(pitch, part)));
  document.querySelectorAll("#pianoKeys .key").forEach(key => {
    const midi = Number(key.dataset.midi);
    key.classList.toggle("chosen", shown.has(midi));
    key.classList.toggle("expected", expected.has(midi));
    key.classList.toggle("sounding", lit.has(midi));
  });
}

// A selected note high on the neck is brought into view, as a phone shows only the first frets.
function showChosenSpot(holder) {
  const panel = holder.closest(".dockPanel");
  const spot = holder.querySelector(".spot.chosen");
  if (!panel || !spot) return;
  const box = spot.getBoundingClientRect(), view = panel.getBoundingClientRect();
  if (box.left < view.left || box.right > view.right) panel.scrollLeft += box.left + box.width / 2 - (view.left + view.width / 2);
}

// Playback lights a note for as long as it sounds, if it's the chosen part's: a key by its pitch,
// and a place on a neck where the score says the note is played.
function soundKey(midi, seconds, id = null) {
  keyboard.sounding.set(midi, (keyboard.sounding.get(midi) ?? 0) + 1);
  const key = `${id}:${midi}`;
  const note = keyboard.soundingNotes.get(key) ?? { id, midi, count: 0 };
  note.count += 1;
  keyboard.soundingNotes.set(key, note);
  lightKeys();
  return setTimeout(() => {
    keyboard.sounding.set(midi, Math.max(0, (keyboard.sounding.get(midi) ?? 1) - 1));
    note.count = Math.max(0, note.count - 1);
    if (!note.count) keyboard.soundingNotes.delete(key);
    lightKeys();
  }, Math.max(seconds, 0.05) * 1000);
}

function quietKeys() {
  keyboard.sounding.clear();
  keyboard.soundingNotes.clear();
  lightKeys();
}

// A key, a fret or a place on a string pressed, given as the pitch the staff shows: in Practice it's
// what you played, while entering notes it writes its note, or adds it to the chord with Shift, as a
// MIDI keyboard does. In Write with one note or rest selected, it becomes that note, or with Shift
// is added to its chord, so a misread note is put right by playing the right one. Otherwise it's
// only heard. A note written from a neck keeps the string it was played on, where the score can
// say so.
function pressKey(midi, addToChord, where = null) {
  holdKey(POINTER_KEY, soundingPitch(midi, activePart())); // Let go by the key's pointerup.
  if (practiseHeard(midi)) return where;
  const string = stringToKeep(activePart(), soundingPitch(midi, activePart()), where);
  if (!editor.on) {
    const target = writing() && !range ? document.getElementById(selected ?? "") : null;
    if (!target?.matches("g.note, g.rest, g.mRest") || partOfStaff(staffNumber(target)) !== activePart()) return where;
    const id = ownId(target.id);
    enqueue(async () => {
      if (addToChord && target.matches("g.note")) await send(`notes/${id}`, { action: "stack", midi, ...string });
      else await send(`notes/${id}`, { action: "pitch", midi, ...string });
    });
    return where;
  }
  enqueue(async () => {
    if (addToChord && editor.last) await send(`notes/${editor.last}`, { action: "stack", midi, ...string });
    else if (editor.cursor) await send(`notes/${editor.cursor}`, { action: "write", type: editor.type, dots: editor.dots, midi, voice: editor.voice, ...string });
  });
  return where;
}

// A guitar keeps whichever string was played, on its tablature or, without one, on the note. A
// violin's note gets the string played as its number, but only where that isn't the string it would
// be shown on anyway, as a violinist marks only the strings that aren't the obvious ones.
function stringToKeep(part, sounding, where) {
  if (!where?.string) return {};
  const kind = kindOfPart(part);
  if (kind === "guitar") return score?.fretted?.[part] ? { string: where.string } : {};
  if (kind === "violin") return fingering(neckOf(part, kind), [sounding])[0]?.string === where.string ? {} : { string: where.string };
  return {};
}

// The sound a part's keys make: a piano's recordings, or for any other part the plain triangle every
// key had before.
function keySound(part) {
  const samples = samplesOf(playState.data ?? {}, part);
  return samples ? { ...USUAL_SOUND, samples } : USUAL_SOUND;
}

// A key pressed, by [key] ("pointer", or "midi:60" for a MIDI keyboard's): it sounds until letGo. A
// key pressed again is damped first, as a piano's hammer stops the string it strikes again. A piano's
// recording is fetched the first time a key needs it; one let go before it came is heard briefly.
function holdKey(key, midi, part = activePart(), volume = HELD_VOLUME) {
  letGo(key, true);
  const voice = { letGo: null, released: false };
  held.voices.set(key, voice);
  try {
    keyPreview ??= new (window.AudioContext || window.webkitAudioContext)();
    const sound = keySound(part);
    const begin = () => {
      if (held.voices.get(key) !== voice && !voice.released) return; // Pressed again meanwhile.
      voice.letGo = heldVoice(keyPreview, sound, midi, volume);
      if (voice.released) voice.letGo();
    };
    if (sound.samples) loadRecording(recordingOf(sound.samples, midi).url).then(begin);
    else begin();
  } catch {
    // Without sound the key still writes its note.
  }
}

// A key let go is damped, unless the sustain pedal is down, which keeps it sounding until it comes up.
function letGo(key, now = false) {
  const voice = held.voices.get(key);
  if (!voice) return;
  if (held.pedal && !now) { held.pedalled.add(key); return; }
  held.voices.delete(key);
  held.pedalled.delete(key);
  voice.released = true;
  voice.letGo?.();
}

function setPedal(down) {
  held.pedal = down;
  if (!down) for (const key of [...held.pedalled]) letGo(key, true);
}

function letAllGo() {
  held.pedal = false;
  for (const key of [...held.voices.keys()]) letGo(key, true);
}

// How loud a MIDI key is, by how hard it was struck: a velocity of 100, a firm touch, is as loud as a
// key below, and the scale is steeper than straight, as the ear hears loudness.
function midiVolume(velocity) {
  return HELD_VOLUME * Math.max(0.05, (velocity / 100) ** 1.6);
}

// A MIDI keyboard's message: a key down or up, or a pedal.
function midiMessage(data) {
  const kind = data[0] & 0xf0;
  if (kind === 0x90 && data[2] > 0) midiNoteOn(data[1], data[2]);
  else if (kind === 0x80 || kind === 0x90) midiNoteOff(data[1]);
  else if (kind === 0xb0 && data[1] === SUSTAIN_PEDAL) setPedal(data[2] >= 64);
  else if (kind === 0xb0 && data[1] === ALL_NOTES_OFF) letAllGo();
}

// A note selected is heard as its part plays, briefly, as MuseScore plays one.
function hearKey(midi, part = activePart()) {
  const sounded = sound => {
    try {
      play(keyPreview, sound, midi, keyPreview.currentTime + 0.01, PREVIEW_SECONDS, 0.3);
    } catch {
      // Without sound the key still writes its note.
    }
  };
  try {
    keyPreview ??= new (window.AudioContext || window.webkitAudioContext)();
    const sound = keySound(part);
    if (sound.samples) loadRecording(recordingOf(sound.samples, midi).url).then(() => sounded(sound));
    else sounded(sound);
  } catch {
    // Without sound the key still writes its note.
  }
}

function setUpInstruments() {
  window.addEventListener("resize", () => { if (!keyboard.width && !$("pianoPanel").hidden) drawKeyboard(); });
  $("pianoPanel").addEventListener("wheel", event => {
    if (!event.ctrlKey) return;
    event.preventDefault();
    keyboard.width = Math.min(WIDEST_KEY, Math.max(NARROWEST_KEY, keyWidth() + (event.deltaY < 0 ? 2 : -2)));
    keepSetting("keyWidth", String(keyboard.width));
    drawKeyboard();
  }, { passive: false });
}
