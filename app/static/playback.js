// Playback: rewind, play, loop, the metronome, the position as a measure and beat and as a time, and
// the speed, in the top bar. Audio export is made by the same code, and Practice adds to it.
const AUDIO_RATE = 44100;
const AUDIO_TAIL_SECONDS = 0.5;
const START_DELAY = 0.1; // Seconds between pressing Play and the first note, so nothing is cut short.
// General MIDI groups its sounds in eights, so the group says what kind of instrument this is.
// They're rough stand-ins to tell parts apart, and the levels even out how loud each wave sounds.
const FAMILY_SOUNDS = {
  5: { wave: "sawtooth", level: 0.7 }, // Strings.
  6: { wave: "sawtooth", level: 0.7 }, // Ensembles and voices.
  7: { wave: "sawtooth", level: 0.7 }, // Brass.
  8: { wave: "square", level: 0.5 }, // Reeds.
  9: { wave: "sine", level: 1.2 }, // Flutes.
};
const USUAL_SOUND = { wave: "triangle", level: 1 };
// Instruments recorded from the real thing: a note every few semitones, each played a little faster
// or slower for the notes between, as a sampler does. The level makes a note's first fifth of a
// second as loud as the stand-in it replaces, or a bowed note as loud while the bow is drawn, so
// the mix and the metronome keep their balance. Another instrument is one more set, and one more
// family or sound below, with a folder of its own notes. A set that isn't a note every so many
// semitones lists its notes: each one's pitch, how many cents sharp it was recorded, and how many
// decibels it's turned up or down to match the rest, as a sampler's mapping evens out a recording
// session. A bowed set's `sustain` is where in its recordings the bow is drawn steadily, after the
// note's start, which a held note goes round (see shapeRecording).
const SAMPLE_SETS = {
  piano: { name: "piano", folder: "sounds/piano", lowest: 21, highest: 108, every: 3, level: 4.7 }, // The Salamander Grand Piano, A0 to C8 (see its README).
  // FreePats' classical guitar, E2 to C6, and Karoryfer's Growlybass, E1 to D♯4 (see their READMEs).
  guitar: { name: "guitar", folder: "sounds/guitar", type: "flac", level: 1.8, notes: [[40, 0, -1], [43, 11], [47, 8, 1], [50, 5], [53, 5, -1], [56, 8, -4], [59, 15, -2], [62, 8], [65], [69, 0, -4], [72, 11, 4], [75, 0, 3], [78, 0, 2], [81, 9, -1], [84, 7, 5]] },
  bassGuitar: { name: "bass guitar", folder: "sounds/bass-guitar", type: "flac", level: 4.4, notes: [[28, 28], [30, 6, 4], [33, 0, 2], [36, -6, 2], [39, 0, -1], [42, 0, 3], [45, 0, -1], [51, 6], [57, 0, -3], [63, 8, -6]] },
  // VSCO 2's solo violin, G3 to A6, and viola section, C3 to B5, Karoryfer's solo cello, C2 to C6,
  // and VSCO 2's solo double bass, F♯1 to B3 (see their READMEs).
  violin: { name: "violin", folder: "sounds/violin", type: "flac", level: 7.2, sustain: 0.5, notes: [[55], [60, -9, -6], [64, 0, -2], [67, 0, 1], [72, 0, -4], [76, 17], [79, 6, -1], [84, 0, -5], [88, 15, 2], [93, 11, 1]] },
  viola: { name: "viola", folder: "sounds/viola", type: "flac", level: 4.2, sustain: 0.5, notes: [[48, 0, -1], [52, 0, 1], [55], [59, 0, -2], [65], [69, 0, -5], [76], [83, 0, 2]] },
  cello: { name: "cello", folder: "sounds/cello", type: "flac", level: 4.6, sustain: 0.5, notes: [[36, 7, -1], [39, 0, -3], [42, -6, -1], [45, 0, -2], [48, 0, -3], [51, 0, -3], [54, 0, 1], [60, 0, 1], [66, 0, 5], [72, 0, 1], [78, 0, 5], [84, 7, 3]] },
  doubleBass: { name: "double bass", folder: "sounds/double-bass", type: "flac", level: 9.6, sustain: 0.5, notes: [[30, 0, 2], [34, 15, -4], [38], [42, 0, 1], [45, 0, -9], [49, 0, 3], [52, 0, 2], [56, 0, -3], [59, 0, -8]] },
  // The same instruments plucked, for pizz. until arco: VSCO 2's violin and violas, Karoryfer's
  // cello, and its Meatbass for the double bass (see their READMEs).
  violinPizzicato: { name: "violin", folder: "sounds/violin/pizzicato", type: "flac", level: 6.4, notes: [[55, -10, 12], [60, -20, -5], [64, -15, 7], [67, -12, 1], [72, 0, -6], [76, 6, -1], [81, 0, -5], [93, 0, 6]] },
  violaPizzicato: { name: "viola", folder: "sounds/viola/pizzicato", type: "flac", level: 4.9, notes: [[48, -7, 1], [52, 0, 2], [55], [62, 0, -4], [69], [79, -5, -6]] },
  celloPizzicato: { name: "cello", folder: "sounds/cello/pizzicato", type: "flac", level: 4.2, notes: [[36], [42, -10, -1], [48, -11], [54, 11], [60, 0, 3], [66, 0, -5], [72, 0, -2]] },
  doubleBassPizzicato: { name: "double bass", folder: "sounds/double-bass/pizzicato", type: "flac", level: 1.8, notes: [[27, -17, -2], [30, 0, 2], [33, 0, -1], [39, 8, -1], [45, -6], [54, 0, 3], [60, -11, 2]] },
};
for (const name of ["violin", "viola", "cello", "doubleBass"]) SAMPLE_SETS[name].pizzicato = SAMPLE_SETS[`${name}Pizzicato`];
// General MIDI's families by their first sound: the pianos (1 to 8), the guitars (25 to 32) and the
// basses (33 to 40).
const SAMPLED_FAMILIES = { 0: SAMPLE_SETS.piano, 3: SAMPLE_SETS.guitar, 4: SAMPLE_SETS.bassGuitar };
// And its bowed strings, sound by sound: a violin, a viola, a cello and a double bass (41 to 44)
// each their own, and the tremolo strings (45), the pizzicato strings (46) and the two string
// ensembles (49 and 50) each note on the instrument whose range it's in, as a section shares its
// music out. A harp (47), the timpani (48) and the synthesizers' strings keep their stand-ins.
const BOWED_SOUNDS = { 41: SAMPLE_SETS.violin, 42: SAMPLE_SETS.viola, 43: SAMPLE_SETS.cello, 44: SAMPLE_SETS.doubleBass };
const SECTION_SOUNDS = new Set([45, 46, 49, 50]);
// A bowed part's note marked pizz., until arco, comes as General MIDI's pizzicato strings.
const PIZZICATO_SOUND = 46;
// A bowed note going round its recording again crosses from its end back to its steady part this long.
const BOW_CROSSFADE_SECONDS = 0.2;
// Chord symbols are played softly under the music on the piano, the usual accompaniment, or on the
// sine wave they had if its recordings can't be had.
const CHORD_SOUND = { wave: "sine", level: 1, samples: SAMPLE_SETS.piano };
const CHORD_VOLUME = 0.045;
const FILE_NAMES = ["C", "Cs", "D", "Ds", "E", "F", "Fs", "G", "Gs", "A", "As", "B"]; // A recording's name, as in "Fs3.mp3".
// A key let go: the damper comes down on the string and quiets it over this long, rather than cutting it
// off. A bow lifted from the string leaves it as quickly.
const DAMPER_SECONDS = 0.3;
const SLOW_LOADING_MS = 1000; // Getting the recordings ready is mentioned only if it takes longer than this.
// Each recording is fetched and decoded once, when something first needs it, and kept for every
// player after: Play, Practice, the keys below and exported audio alike.
const recordings = { buffers: new Map(), loading: new Map(), decoder: null, warned: false };
const TICK = { strong: 1760, weak: 1320, seconds: 0.04, level: 0.35 }; // The metronome: higher on the first beat of a bar.
const SLOWEST_SPEED = 10, FASTEST_SPEED = 300; // Percent of the written tempo, as MuseScore allows.
const EARLY = 1e-9; // Places are sums of fractions, so a beat may land a hair before where it's due.
// A pan in the middle gives each speaker this much less of a sound, so it's given back, and a score
// nobody has mixed sounds just as it did before there was a mixer.
const PAN_LAW = Math.SQRT2;
// Where playback starts and what it does. The place is in quarter notes along the music as it's
// played, with its repeats, so it means the same here as the times the server sends.
const playState = {
  place: 0,
  looping: false,
  loop: null, // The selection the loop goes round: { first, last } note ids, or null ends for the whole score.
  metronome: readSetting("metronome", "off") === "on",
  chords: readSetting("chordSymbols", "on") === "on",
  speed: 1,
  data: null, // The latest playback, for the position and tempo shown while nothing plays.
  wanted: 0,
  chosen: null, // A note or rest selected since, which playback starts from instead, as in MuseScore.
  starting: null, // Playback on its way, while its notes are fetched: Stop before then stops it starting.
};
let player = null;

function tone(audio, midi, at, length, volume, wave, destination = audio.destination) {
  const oscillator = audio.createOscillator();
  const gain = audio.createGain();
  const end = at + Math.max(length, 0.05);
  oscillator.type = wave;
  oscillator.frequency.value = 440 * 2 ** ((midi - 69) / 12);
  gain.gain.setValueAtTime(0, at);
  gain.gain.linearRampToValueAtTime(volume, at + 0.012);
  gain.gain.linearRampToValueAtTime(volume * 0.7, Math.max(at + 0.02, end - 0.04));
  gain.gain.linearRampToValueAtTime(0, end);
  oscillator.connect(gain).connect(destination);
  oscillator.start(at);
  oscillator.stop(end + 0.02);
}

// The recording nearest a pitch: the note it was recorded at, where its file is, how much faster or
// slower it's played to sound the pitch, its tuning allowed for, and how much louder or softer.
function recordingOf(set, midi) {
  const count = Math.floor((set.highest - set.lowest) / set.every);
  const [recorded, cents = 0, decibels = 0] = set.notes
    ? set.notes.reduce((nearest, note) => (Math.abs(note[0] - midi) < Math.abs(nearest[0] - midi) ? note : nearest))
    : [set.lowest + Math.min(count, Math.max(0, Math.round((midi - set.lowest) / set.every))) * set.every];
  return {
    midi: recorded,
    url: `${set.folder}/${FILE_NAMES[recorded % 12]}${Math.floor(recorded / 12) - 1}.${set.type ?? "mp3"}`,
    rate: 2 ** ((midi - recorded - cents / 100) / 12),
    gain: 10 ** (decibels / 20),
  };
}

// A recording is made ready to play as it's decoded. A bowed one goes round its steady part for as
// long as the bow is drawn, from its set's `sustain` to its end and back, so the stretch before its
// end is faded into what comes just before that point (with equal power, as the two are unlike
// moments of a bow stroke) and going back makes no click. Any other is damped over its last
// moments, so one cut short of its natural end, as the bass's are, dies away instead of stopping
// with a click. One that ends in silence, as the piano's do, is the same with it.
function shapeRecording(buffer, set) {
  const length = buffer.length;
  const from = Math.round((set?.sustain ?? 0) * buffer.sampleRate);
  const fade = set?.sustain
    ? Math.min(Math.round(BOW_CROSSFADE_SECONDS * buffer.sampleRate), from, length - from)
    : Math.min(length, Math.round(DAMPER_SECONDS * buffer.sampleRate));
  for (let channel = 0; channel < buffer.numberOfChannels; channel++) {
    const samples = buffer.getChannelData(channel);
    for (let index = 0; index < fade; index++) {
      const at = length - fade + index;
      if (!set?.sustain) { samples[at] *= 1000 ** (-index / fade); continue; }
      const share = (index + 1) / fade * Math.PI / 2;
      samples[at] = samples[at] * Math.cos(share) + samples[from - fade + index] * Math.sin(share);
    }
  }
}

// A bowed note is held for as long as it's played: its recording goes round its steady part.
function bowed(source, set) {
  if (!set.sustain) return;
  source.loop = true;
  source.loopStart = set.sustain;
  source.loopEnd = source.buffer.duration;
}

// Fetched and decoded the first time it's asked for, as its set's recordings are. One that can't be
// had is null, and the instrument plays its stand-in instead, which is said once.
function loadRecording(url, set) {
  if (!recordings.loading.has(url)) {
    recordings.loading.set(url, (async () => {
      const response = await fetch(url);
      if (!response.ok) throw new Error(`${url} answered ${response.status}`);
      recordings.decoder ??= new OfflineAudioContext(2, 1, AUDIO_RATE);
      const buffer = await recordings.decoder.decodeAudioData(await response.arrayBuffer());
      shapeRecording(buffer, set);
      recordings.buffers.set(url, buffer);
      return buffer;
    })().catch(error => {
      if (!recordings.warned) console.warn("A recorded instrument couldn't be loaded, so it plays its stand-in sound.", error);
      recordings.warned = true;
      return null;
    }));
  }
  return recordings.loading.get(url);
}

// How loud a recording is played. A stereo recording goes through the mixer's pan whole, so it
// isn't given back what a pan in the middle takes from a sound in one channel.
function levelOf(set, recording, buffer, volume) {
  return Math.max(volume * set.level * recording.gain / (buffer.numberOfChannels > 1 ? PAN_LAW : 1), 1e-6);
}

// A recorded note: the string is struck or plucked and rings for as long as the key is held, or is
// bowed for as long as the note lasts, and is damped when it's let go. Between two recordings it's
// the nearer one played faster or slower.
function sampled(audio, set, midi, at, length, volume, destination) {
  const recording = recordingOf(set, midi);
  const buffer = recordings.buffers.get(recording.url);
  if (!buffer) return false;
  const source = audio.createBufferSource();
  const gain = audio.createGain();
  const end = at + Math.max(length, 0.05);
  const level = levelOf(set, recording, buffer, volume);
  source.buffer = buffer;
  source.playbackRate.value = recording.rate;
  bowed(source, set);
  gain.gain.setValueAtTime(level, at);
  gain.gain.setValueAtTime(level, end);
  gain.gain.exponentialRampToValueAtTime(level / 1000, end + DAMPER_SECONDS);
  source.connect(gain).connect(destination);
  source.start(at);
  source.stop(end + DAMPER_SECONDS);
  return true;
}

// A key held down, whose end isn't known yet: it sounds from now, as play() would, until the function
// returned lets it go, and then the damper quiets it, as a piano's does when its key comes up. A
// struck or plucked recording dies away by itself as its string does; a wave holds, and so does a
// bowed recording, so one whose let-go never comes (a MIDI keyboard's note-off lost) is let go
// after LONGEST_HOLD_SECONDS rather than left droning.
const LONGEST_HOLD_SECONDS = 20;

function heldVoice(audio, sound, midi, volume, destination = audio.destination) {
  const at = audio.currentTime + 0.005;
  const recording = sound.samples ? recordingOf(sound.samples, midi) : null;
  const buffer = recording ? recordings.buffers.get(recording.url) : null;
  const gain = audio.createGain();
  let voice;
  if (buffer) {
    voice = audio.createBufferSource();
    voice.buffer = buffer;
    voice.playbackRate.value = recording.rate;
    bowed(voice, sound.samples);
    gain.gain.setValueAtTime(levelOf(sound.samples, recording, buffer, volume), at);
  } else {
    voice = audio.createOscillator();
    voice.type = sound.wave;
    voice.frequency.value = 440 * 2 ** ((midi - 69) / 12);
    const level = Math.max(volume * sound.level, 1e-6);
    gain.gain.setValueAtTime(0, at);
    gain.gain.linearRampToValueAtTime(level, at + 0.012);
    gain.gain.linearRampToValueAtTime(level * 0.7, at + 0.3);
  }
  voice.connect(gain).connect(destination);
  voice.start(at);
  let gone = false;
  const letGo = () => {
    if (gone) return;
    gone = true;
    clearTimeout(longest);
    const now = Math.max(audio.currentTime, at);
    const level = Math.max(gain.gain.value, 1e-6);
    gain.gain.cancelScheduledValues(now);
    gain.gain.setValueAtTime(level, now);
    gain.gain.exponentialRampToValueAtTime(level / 1000, now + DAMPER_SECONDS);
    voice.stop(now + DAMPER_SECONDS);
  };
  const longest = setTimeout(letGo, LONGEST_HOLD_SECONDS * 1000);
  return letGo;
}

// A note in a part's sound: its recording, where it has one that's ready, or else its wave.
function play(audio, sound, midi, at, length, volume, destination = audio.destination) {
  if (sound.samples && sampled(audio, sound.samples, midi, at, length, volume, destination)) return;
  tone(audio, midi, at, length, volume * sound.level, sound.wave, destination);
}

// What a playback will play from recordings is fetched before it starts, so its first notes aren't
// missing. They come from this server, so it's short, and only a wait longer than that is mentioned.
async function readySamples(data) {
  if (!data) return data;
  const wanted = new Map(), names = new Set();
  const want = (set, midi) => { if (set) { wanted.set(recordingOf(set, midi).url, set); names.add(set.name); } };
  for (const note of data.notes) want(soundOf(data, note).samples, note.midi);
  if (playState.chords) for (const chord of data.chords) chord.midi.forEach(midi => want(CHORD_SOUND.samples, midi));
  if (!wanted.size) return data;
  const words = `Getting the ${[...names].join(" and ")} ready…`;
  const slow = setTimeout(() => message(words), SLOW_LOADING_MS);
  await Promise.all([...wanted].map(([url, set]) => loadRecording(url, set)));
  clearTimeout(slow);
  if ($("message").textContent === words) message("");
  return data;
}

// A metronome's tick: a short, bright click that dies away at once.
function tick(audio, at, strong, destination = audio.destination) {
  const oscillator = audio.createOscillator();
  const gain = audio.createGain();
  oscillator.type = "sine";
  oscillator.frequency.value = strong ? TICK.strong : TICK.weak;
  gain.gain.setValueAtTime(0, at);
  gain.gain.linearRampToValueAtTime(TICK.level, at + 0.002);
  gain.gain.exponentialRampToValueAtTime(0.001, at + TICK.seconds);
  oscillator.connect(gain).connect(destination);
  oscillator.start(at);
  oscillator.stop(at + TICK.seconds + 0.01);
}

// Turns places in quarter notes into seconds, which takes adding up every tempo before them, and
// back again with `place`.
function clockFor(data, speed) {
  const changes = data.tempos?.length ? data.tempos : [{ start: 0, bpm: data.tempo }];
  let seconds = 0;
  const marks = changes.map((change, index) => {
    if (index) seconds += (change.start - changes[index - 1].start) * 60 / (changes[index - 1].bpm * speed);
    return { ...change, seconds };
  });
  const clock = place => {
    const mark = marks.findLast(item => item.start <= place) ?? marks[0];
    return mark.seconds + (place - mark.start) * 60 / (mark.bpm * speed);
  };
  clock.place = time => {
    const mark = marks.findLast(item => item.seconds <= time) ?? marks[0];
    return mark.start + (time - mark.seconds) * mark.bpm * speed / 60;
  };
  clock.bpm = place => (marks.findLast(item => item.start <= place) ?? marks[0]).bpm;
  return clock;
}

// A note's sound. One plucked, pizz. until arco, plays its bowed instrument's pizzicato recordings,
// or its stand-in's short note without them.
function soundOf(data, note) {
  const program = data.programs?.[note.part] ?? 1;
  const standIn = FAMILY_SOUNDS[Math.floor((program - 1) / 8)] ?? USUAL_SOUND;
  const part = samplesOf(data, note.part ?? 0, note.midi);
  const samples = note.program === PIZZICATO_SOUND ? part?.pizzicato ?? part : part;
  return samples ? { ...standIn, samples } : standIn; // The stand-in is what it plays if its recordings can't be had.
}

// The recordings a part's note is played with, if its sound has them. A part that names no sound is
// given program 1, the grand piano, so that one is a piano only when the part is a keyboard by its
// name or its staves, as the instrument below decides. Otherwise it goes by its name, as a string
// quartet's parts often come with none: a violin or a cello named so plays its own recordings, as
// does a guitar or a bass guitar, and a melody on a plain staff keeps its triangle. A part named for
// one bowed instrument but given a string section's sound, as files often give a quartet, is that
// instrument too, so a viola's A4 isn't a violin's.
function samplesOf(data, part, midi) {
  const program = data.programs?.[part] ?? 1;
  if (program === 1) return namedSamples(part);
  if (SECTION_SOUNDS.has(program)) {
    const set = bowedByName(part) ?? sectionAt(midi);
    return program === PIZZICATO_SOUND ? set.pizzicato : set;
  }
  return BOWED_SOUNDS[program] ?? SAMPLED_FAMILIES[Math.floor((program - 1) / 8)] ?? null;
}

function namedSamples(part) {
  const kind = kindOfPart(part), name = (score?.parts?.[part] ?? "").toLowerCase();
  if (kind === "piano") return SAMPLE_SETS.piano;
  if (/bass guitar|electric bass/.test(name) || (kind === "guitar" && /bass/.test(name))) return SAMPLE_SETS.bassGuitar;
  return kind === "guitar" ? SAMPLE_SETS.guitar : bowedByName(part);
}

// The bowed instrument a part is named for, if any: "Violoncello" is a cello, "Fiddle" a violin.
function bowedByName(part) {
  const name = (score?.parts?.[part] ?? "").toLowerCase();
  if (/double bass|contrabass/.test(name)) return SAMPLE_SETS.doubleBass;
  if (/cello/.test(name)) return SAMPLE_SETS.cello;
  if (/viola/.test(name)) return SAMPLE_SETS.viola;
  return /violin|fiddle/.test(name) ? SAMPLE_SETS.violin : null;
}

// A string section's note is played by the highest of its instruments that reaches down to it.
function sectionAt(midi) {
  if (midi >= 55) return SAMPLE_SETS.violin;
  if (midi >= 48) return SAMPLE_SETS.viola;
  return midi >= 36 ? SAMPLE_SETS.cello : SAMPLE_SETS.doubleBass;
}

// The beat a bar is counted in: its time signature's lower number, or a dotted beat in 6/8, 9/8 and 12/8.
function beatOf(bar) {
  const compound = bar.beatType === 8 && bar.beats % 3 === 0 && bar.beats > 3;
  return (compound ? 3 : 1) * 4 / bar.beatType;
}

// A pickup's beats are the last ones of its bar, so they're counted from where the bar would begin.
function barBegins(bar) {
  return bar.index === 0 && bar.length < bar.full ? bar.start - (bar.full - bar.length) : bar.start;
}

// The metronome's beats from one place to another, each saying whether it starts a bar.
function beatsBetween(data, from, until) {
  const found = [];
  for (const bar of data.bars ?? []) {
    const unit = beatOf(bar), begins = barBegins(bar);
    for (let count = Math.ceil((bar.start - begins) / unit - EARLY); begins + count * unit < bar.start + bar.length - EARLY; count++) {
      const place = begins + count * unit;
      if (place >= from - EARLY && place < until - EARLY) found.push({ place, strong: count === 0 });
    }
  }
  return found;
}

// A channel's level as a gain. Muted, or left out while others are soloed, it's silent.
function gainOf(channel, soloing, soloable = true) {
  if (!channel || channel.mute || (soloing && soloable && !channel.solo) || channel.volume <= QUIETEST_DB) return channel ? 0 : 1;
  return 10 ** (channel.volume / 20);
}

// The mixer as playback reads it: each part's channel by its place in the score, not its id.
function mixerForPlayback(settings = mixerOf(score)) {
  return { parts: (score?.partIds ?? []).map(id => settings.parts[id]), chords: settings.chords, metronome: settings.metronome, master: settings.master };
}

// Each part, the chord symbols and the metronome go through a level and a pan of their own into the
// master channel, as in MuseScore's mixer. Their values can change while the music plays.
function mixerNodes(audio, mixer = {}) {
  const master = audio.createGain();
  master.connect(audio.destination);
  const strip = () => {
    const gain = audio.createGain(), panner = audio.createStereoPanner();
    gain.connect(panner).connect(master);
    return { gain, panner };
  };
  const nodes = { master, parts: [], chords: strip(), metronome: strip(), part: index => (nodes.parts[index] ??= strip()) };
  (mixer.parts ?? []).forEach((_, index) => nodes.part(index));
  setMixer(nodes, mixer);
  return nodes;
}

function setMixer(nodes, mixer, when = null) {
  const soloing = (mixer.parts ?? []).some(channel => channel?.solo) || Boolean(mixer.chords?.solo);
  const set = (parameter, value) => (when === null ? (parameter.value = value) : parameter.setTargetAtTime(value, when, 0.02));
  set(nodes.master.gain, gainOf(mixer.master, false));
  const level = (strip, channel, soloable) => {
    set(strip.gain.gain, gainOf(channel, soloing, soloable) * PAN_LAW);
    set(strip.panner.pan, (channel?.pan ?? 0) / 100);
  };
  nodes.parts.forEach((strip, index) => level(strip, mixer.parts?.[index], true));
  level(nodes.chords, mixer.chords, true);
  level(nodes.metronome, mixer.metronome, false);
}

// The mixer moved: what's playing follows it at once.
function applyMixer(settings) {
  if (player) setMixer(player.nodes, mixerForPlayback(settings), player.audio.currentTime);
}

// Works with speakers or with an offline context, which is how the tests check what would be heard.
// Nothing is played from `until` on, which is where a loop goes back to its start. A recorded
// instrument plays the recordings readySamples has fetched, and its stand-in for any it hasn't.
function schedule(audio, data, from, zero, speed = 1, until = Infinity, options = {}) {
  const clock = clockFor(data, speed);
  const at = place => zero + clock(place) - clock(from);
  const nodes = options.nodes ?? mixerNodes(audio, options.mixer);
  const notes = data.notes.filter(note => note.start >= from && note.start < until && (!options.only || options.only(note)));
  for (const note of notes) {
    const ends = Math.min(note.start + note.length, until);
    play(audio, soundOf(data, note), note.midi, at(note.start), at(ends) - at(note.start), 0.4 * note.volume ** 2, nodes.part(note.part ?? 0).gain);
  }
  const chords = options.chords === false ? [] : data.chords.filter(chord => chord.start + chord.length > from && chord.start < until);
  for (const chord of chords) {
    const begins = Math.max(chord.start, from);
    const ends = Math.min(chord.start + chord.length, until);
    for (const midi of chord.midi) play(audio, CHORD_SOUND, midi, at(begins), at(ends) - at(begins), CHORD_VOLUME, nodes.chords.gain);
  }
  const beats = options.metronome ? beatsBetween(data, from, Math.min(until, data.total)) : [];
  for (const beat of beats) tick(audio, at(beat.place), beat.strong, nodes.metronome.gain);
  return { notes, chords, beats, at, nodes };
}

// A plain 16-bit WAV, which any program can open: one channel, or the left and right of a stereo one.
function wavFile(samples, rate) {
  const channels = ArrayBuffer.isView(samples) ? [samples] : samples;
  const frames = channels[0].length, size = frames * channels.length * 2;
  const peak = Math.max(1, ...channels.map(channel => channel.reduce((top, sample) => Math.max(top, Math.abs(sample)), 0)));
  const file = new DataView(new ArrayBuffer(44 + size));
  const write = (at, word) => [...word].forEach((letter, index) => file.setUint8(at + index, letter.charCodeAt(0)));
  write(0, "RIFF");
  file.setUint32(4, 36 + size, true);
  write(8, "WAVEfmt ");
  file.setUint32(16, 16, true);
  file.setUint16(20, 1, true); // Plain samples, not compressed.
  file.setUint16(22, channels.length, true);
  file.setUint32(24, rate, true);
  file.setUint32(28, rate * 2 * channels.length, true);
  file.setUint16(32, 2 * channels.length, true);
  file.setUint16(34, 16, true);
  write(36, "data");
  file.setUint32(40, size, true);
  for (let frame = 0; frame < frames; frame++) {
    channels.forEach((channel, number) => file.setInt16(44 + (frame * channels.length + number) * 2, channel[frame] / peak * 0x7fff, true));
  }
  return new Blob([file], { type: "audio/wav" });
}

async function fetchPlayback() {
  const response = await fetch(`/api/scores/${scoreId}/playback`);
  if (!response.ok) return null;
  playState.data = await response.json();
  return playState.data;
}

// Made by the same code as Play, so the file sounds just as the score does, only faster than real
// time. As in MuseScore, it's mixed as the mixer is set, in stereo, and the metronome is in it when
// it's switched on.
function saveAudio() {
  return enqueue(async () => {
    const data = await readySamples(await fetchPlayback());
    const seconds = clockFor(data, 1)(data.total) + AUDIO_TAIL_SECONDS;
    const audio = new OfflineAudioContext(2, Math.ceil(seconds * AUDIO_RATE), AUDIO_RATE);
    schedule(audio, data, 0, 0, 1, Infinity, { metronome: playState.metronome, chords: playState.chords, mixer: mixerForPlayback() });
    const rendered = await audio.startRendering();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(wavFile([rendered.getChannelData(0), rendered.getChannelData(1)], AUDIO_RATE));
    link.download = savedName(".wav");
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  });
}

// Where a note or rest is first played, in quarter notes, and how long it lasts. After a given
// place, it's the first time it's played from there.
function placeOf(data, id, after = -Infinity) {
  const found = data.events?.[id];
  if (!found) return null;
  const [index, onset, length] = found;
  const bar = data.bars.find(item => item.index === index && item.start + onset >= after - EARLY) ?? data.bars.find(item => item.index === index);
  return bar ? { start: bar.start + onset, end: bar.start + onset + length } : null;
}

// The stretch a loop goes round: the selection it was set on, or the whole score.
function loopWindow(data) {
  const whole = { start: 0, end: data.total };
  const { first, last } = playState.loop ?? {};
  if (!first || !last) return whole;
  const opening = placeOf(data, first), closing = placeOf(data, last);
  if (!opening || !closing) return whole;
  const start = Math.min(opening.start, closing.start);
  const end = Math.max(placeOf(data, first, start).end, placeOf(data, last, start).end);
  return end > start ? { start, end } : whole;
}

function clearPlaying() {
  document.querySelectorAll("#pages .playing").forEach(element => element.classList.remove("playing"));
}

function showPlaying(on) {
  const button = $("play");
  button.setAttribute("aria-label", on ? "Stop" : "Play");
  button.setAttribute("aria-pressed", String(on));
  button.innerHTML = icon(on ? "stop" : "play");
}

// Where playback has got to, as a place in the music.
function playingPlace() {
  const now = player.audio.currentTime;
  const cycle = player.cycles.findLast(item => item.zero <= now) ?? player.cycles[0];
  const seconds = cycle.clock(cycle.from) + Math.max(0, now - cycle.zero);
  return Math.min(cycle.clock.place(seconds), cycle.until);
}

// One time through: to the end of the score, or round the loop, which then goes round again.
// A note starts: it's shown on the page, which follows it, and on the instrument if it's that part's.
function showSounding(data, note, at, until) {
  clearPlaying();
  const element = document.getElementById(note.id);
  element?.classList.add("playing");
  // Lit for as long as it sounds: through a rest after it, only the playhead moves on.
  const ends = (at(Math.min(note.start + note.length, until)) - at(note.start)) * 1000;
  const timers = player?.timers ?? practice.follower?.timers;
  timers?.push(setTimeout(() => element?.classList.remove("playing"), Math.max(ends, 50)));
  followNote(element);
  if (timers && instrumentShown() && (note.part ?? 0) === activePart()) timers.push(soundKey(note.midi, at(Math.min(note.start + note.length, until)) - at(note.start), note.id));
}

// Each time round a loop can go a little faster (see nextSpeed), shown as that time round begins.
function playCycle(from, until, zero) {
  const { audio, data } = player;
  const speed = player.cycles.length ? nextSpeed(player.speed) : player.speed;
  player.speed = speed;
  const { notes, chords, at } = schedule(audio, data, from, zero, speed, until, { metronome: playState.metronome, chords: playState.chords, nodes: player.nodes, only: hearing() });
  player.cycles.push({ from, until, zero, clock: clockFor(data, speed) });
  if (player.cycles.length > 1) player.timers.push(setTimeout(() => showSpeed(speed), Math.max(0, (zero - audio.currentTime) * 1000)));
  for (const note of notes) {
    player.timers.push(setTimeout(() => showSounding(data, note, at, until), (at(note.start) - audio.currentTime) * 1000));
  }
  if (player.cycles.length === 1) {
    $("play").dataset.notes = notes.length;
    $("play").dataset.chords = chords.length;
  }
  const ends = at(until);
  if (player.loop) {
    const window = player.loop;
    player.timers.push(setTimeout(() => { if (player) playCycle(window.start, window.end, ends); }, Math.max(0, (zero - audio.currentTime) * 1000)));
  } else {
    player.timers.push(setTimeout(() => stopPlaying(true), (ends - audio.currentTime + 0.3) * 1000));
  }
}

function followPlaying() {
  if (!player) return;
  const place = playingPlace();
  drawPosition(place);
  drawPlayhead(place);
  player.frame = requestAnimationFrame(followPlaying);
}

// The playhead: a line across the system that moves through each bar as it plays, through rests
// and held notes too, and the page follows it. Between the notes of a bar it moves at an even pace
// from one to the next, as the beats pass.
const playhead = { bar: null, anchors: [], box: null };

function barAnchors(data, bar) {
  const measure = document.querySelectorAll("#pages g.measure")[bar.index];
  if (!measure) return null;
  const staves = [...measure.children].filter(child => child.classList.contains("staff") && child.querySelector(":scope > path"));
  if (!staves.length) return null;
  const top = staffLines(staves[0]), bottom = staffLines(staves.at(-1));
  // Where the bar's music begins, after a clef, key or time signature that starts it.
  const signs = [...measure.querySelectorAll(":scope > g.staff > g.clef, :scope > g.staff > g.keySig, :scope > g.staff > g.meterSig")];
  const begins = Math.max(top.left, ...signs.map(sign => sign.getBoundingClientRect().right)) + top.step * 2;
  const onsets = new Map();
  // A whole bar's rest is drawn in the middle of its bar, so it says nothing of where its beats are.
  for (const element of measure.querySelectorAll("g.note, g.rest")) {
    const found = data.events?.[ownId(element.id)];
    if (!found || found[0] !== bar.index) continue;
    onsets.set(found[1], Math.min(onsets.get(found[1]) ?? Infinity, headBox(element).left));
  }
  const anchors = [...onsets].sort((first, second) => first[0] - second[0]).map(([onset, x]) => ({ onset, x }));
  if (!anchors.length || anchors[0].onset > EARLY) anchors.unshift({ onset: 0, x: Math.min(begins, anchors[0]?.x ?? begins) });
  anchors.push({ onset: bar.length, x: top.right - top.step });
  return { anchors, top: top.top - top.step * 3, bottom: bottom.bottom + bottom.step * 3, measure };
}

function drawPlayhead(place) {
  const line = $("playhead");
  const data = player?.data ?? practice.follower?.data;
  const bar = data?.bars?.findLast(item => item.start <= place + EARLY);
  if (!bar) { line.hidden = true; return; }
  if (playhead.bar !== bar) {
    playhead.bar = bar;
    playhead.box = barAnchors(data, bar);
    if (playhead.box) followNote(playhead.box.measure);
  }
  const box = playhead.box;
  if (!box) { line.hidden = true; return; }
  const onset = place - bar.start;
  const after = box.anchors.findIndex(anchor => anchor.onset > onset + EARLY);
  const from = box.anchors[Math.max(0, (after < 0 ? box.anchors.length : after) - 1)], to = box.anchors[after < 0 ? box.anchors.length - 1 : after];
  const share = to.onset > from.onset ? Math.min(1, Math.max(0, (onset - from.onset) / (to.onset - from.onset))) : 0;
  const frame = $("pagesFrame").getBoundingClientRect();
  line.hidden = false;
  line.style.left = `${from.x + (to.x - from.x) * share - frame.left}px`;
  line.style.top = `${box.top - frame.top}px`;
  line.style.height = `${box.bottom - box.top}px`;
}

function hidePlayhead() {
  $("playhead").hidden = true;
  playhead.bar = null;
  playhead.box = null;
}

// The playback position follows whatever was done last: selecting a note or rest puts it there, and
// so does typing in a counter, rewinding or stopping. It's where Play starts, as in MuseScore.
function playFromNote(id) {
  playState.chosen = id;
  const found = id && playState.data && placeOf(playState.data, id);
  if (found && !player) drawPosition(found.start);
}

// Play from a place given, or from the playback position. Started again where it was playing, as a
// new speed or a part to hear does, it's not counted in again.
async function startPlaying(place = null, counted = true) {
  const starting = playState.starting = {};
  const data = await readySamples(await fetchPlayback());
  if (playState.starting !== starting) return; // Stopped, or started again, while its notes came.
  playState.starting = null;
  if (!data) { message("This sheet couldn't be played."); return; }
  // While entering notes it's where the next note goes, as it always was.
  const fromNote = editor.on ? plainId(editor.cursor) : playState.chosen;
  const chosen = place === null && fromNote ? placeOf(data, fromNote) : null;
  if (chosen) playState.place = chosen.start;
  playState.chosen = null;
  let from = place ?? (playState.place < data.total - EARLY ? playState.place : 0);
  const loop = playState.looping ? loopWindow(data) : null;
  if (loop && (from < loop.start - EARLY || from >= loop.end - EARLY)) from = loop.start;
  const audio = new (window.AudioContext || window.webkitAudioContext)();
  const nodes = mixerNodes(audio, mixerForPlayback());
  player = { audio, data, nodes, speed: playState.speed, clock: clockFor(data, playState.speed), timers: [], cycles: [], loop };
  // In Practice a bar or two can be counted in first.
  const lead = counted ? countIn(audio, data, from, audio.currentTime + START_DELAY, nodes) : 0;
  playCycle(from, loop ? loop.end : data.total, audio.currentTime + START_DELAY + lead);
  showPlaying(true);
  followPlaying();
}

// Stopping keeps the place, so Play carries on from there. Coming to the end goes back to the start.
function stopPlaying(finished = false) {
  if (practice.follower) { stopFollowing(finished); return; }
  if (!player) return;
  playState.place = finished ? 0 : playingPlace();
  player.timers.forEach(clearTimeout);
  cancelAnimationFrame(player.frame);
  player.audio.close();
  player = null;
  clearPlaying();
  hidePlayhead();
  quietKeys();
  showPlaying(false);
  showWait("");
  speedBack();
  drawPosition(playState.place);
}

// In Practice, with someone's notes to wait for, playback waits for you to play them.
function togglePlaying() {
  if (player || practice.follower) return stopPlaying();
  if (playState.starting) { playState.starting = null; return; }
  return practising() && practice.waitFor !== "off" ? startFollowing() : startPlaying();
}

// Go to a place: at once if playing, or ready for the next Play.
function playFrom(place) {
  playState.chosen = null;
  if (practice.follower) {
    stopPlaying();
    startFollowing(place, false);
    return;
  }
  if (!player) {
    playState.place = place;
    drawPosition(place);
    return;
  }
  stopPlaying();
  startPlaying(place, false);
}

function rewind() {
  const data = playState.data;
  const looping = Boolean(playState.looping && data);
  playFrom(looping ? loopWindow(data).start : 0);
  // Stopped, the page goes to where Play will start, as it follows the music while it plays: only
  // the counters changed before, so Rewind seemed to do nothing with the end of the score in sight.
  if (player || practice.follower) return;
  const first = looping && playState.loop?.first && document.getElementById(plainId(playState.loop.first));
  if (first) followNote(first.closest("g.measure") ?? first); // Its whole bar, as playback follows one.
  else turnToEnd(false);
}

function toggleLoop() {
  playState.looping = !playState.looping;
  if (playState.looping) {
    const first = range?.first ?? actionTarget(), last = range?.last ?? first;
    if (first || !playState.loop) playState.loop = { first: first ?? null, last: last ?? null };
  }
  drawPracticeState();
  drawLoopMarkers();
  if (player) playFrom(playingPlace());
}

// The settings popup can set either end of the loop at the selected note, as MuseScore's can.
function setLoopEnd(side) {
  const id = range ? (side === "first" ? range.first : range.last) : actionTarget();
  if (!id) { message("Select a note or rest for the loop to start or end at."); return; }
  playState.loop = { first: playState.loop?.first ?? id, last: playState.loop?.last ?? id, [side]: id };
  playState.looping = true;
  drawPracticeState();
  drawLoopMarkers();
}

function toggleMetronome() {
  playState.metronome = !playState.metronome;
  keepSetting("metronome", playState.metronome ? "on" : "off");
  drawPracticeState();
  if (player) playFrom(playingPlace());
}

function setSpeed(percent) {
  const value = Math.min(FASTEST_SPEED, Math.max(SLOWEST_SPEED, Math.round(Number(percent) || 100)));
  practice.startSpeed = null; // A speed chosen is where a stepped-up loop starts from now.
  playState.speed = value / 100;
  $("speed").value = value;
  $("speedValue").value = value;
  drawPracticeState();
  drawPosition(player ? playingPlace() : playState.place);
  if (player) playFrom(playingPlace());
}

function twoDigits(number) {
  return String(number).padStart(2, "0");
}

// The time as MuseScore shows it, hours, minutes, seconds and tenths, and back.
function timeText(seconds) {
  const tenths = Math.floor(seconds * 10 + EARLY);
  return `${twoDigits(Math.floor(tenths / 36000))}:${twoDigits(Math.floor(tenths / 600) % 60)}:${twoDigits(Math.floor(tenths / 10) % 60)}.${tenths % 10}`;
}

function secondsIn(text) {
  const parts = text.trim().split(":").map(Number);
  if (!parts.length || parts.some(part => !Number.isFinite(part) || part < 0)) return null;
  return parts.reduce((total, part) => total * 60 + part, 0);
}

// The measure and beat of a place, the measure numbered as the page and the inspector number it: a
// pickup is measure 0 there, so counting from 1 put the counter a measure ahead of the page.
function beatAt(data, place) {
  const bar = data.bars.findLast(item => item.start <= place + EARLY) ?? data.bars[0];
  if (!bar) return { measure: 1, beat: 1 };
  return { measure: measureFacts(bar.index).number, beat: Math.floor((place - barBegins(bar)) / beatOf(bar) + EARLY) + 1 };
}

// A measure typed in is the one the page numbers so, or failing that, the one counted so from 1.
function placeAtBeat(data, measure, beat) {
  const numbered = (score?.timeline?.measures ?? []).findIndex(item => item.number === String(measure));
  const index = numbered >= 0 ? numbered : measure - 1;
  const bar = data.bars.find(item => item.index === index);
  if (!bar) return null;
  return Math.min(Math.max(barBegins(bar) + (beat - 1) * beatOf(bar), bar.start), bar.start + bar.length - EARLY);
}

function drawPosition(place) {
  const data = playState.data;
  if (!data) return;
  const clock = clockFor(data, playState.speed);
  if (document.activeElement !== $("timeCounter")) $("timeCounter").value = timeText(clock(place));
  const { measure, beat } = beatAt(data, place);
  if (document.activeElement !== $("beatCounter")) $("beatCounter").value = `${measure}.${beat}`;
  $("tempoValue").textContent = String(Math.round(clock.bpm(place) * playState.speed));
}

// The playback position and tempo are kept up to date as the score is edited, but not on every keystroke.
function refreshPosition() {
  if (!scoreId) return;
  clearTimeout(playState.wanted);
  playState.wanted = setTimeout(async () => {
    if (!scoreId || player || practice.follower) return;
    const data = await fetchPlayback();
    if (data && !player && !practice.follower) {
      playState.place = Math.min(playState.place, data.total);
      // A note selected since the last play is where the position is, as it's where Play starts.
      const chosen = playState.chosen && placeOf(data, playState.chosen);
      drawPosition(chosen ? chosen.start : playState.place);
      refreshKinds();
      lightKeys();
      // A transposing instrument's sound is only known now, and the inspector's strings follow it.
      if (actionTarget()) drawInspector();
    }
  }, 400);
}

// Flags where the loop starts and ends, at the notes it was set on, as MuseScore draws them.
function drawLoopMarkers() {
  document.querySelectorAll(".loopMarker").forEach(marker => marker.remove());
  if (!playState.looping || !playState.loop?.first) return;
  const frame = $("pagesFrame").getBoundingClientRect();
  for (const [side, id] of [["in", playState.loop.first], ["out", playState.loop.last]]) {
    const element = document.getElementById(id);
    const staff = element?.closest("g.system")?.querySelector("g.staff");
    if (!element || !staff) continue;
    const box = headBox(element), lines = staffLines(element.closest("g.staff") ?? staff);
    const marker = document.createElement("div");
    marker.className = `loopMarker ${side}`;
    marker.style.left = `${(side === "in" ? box.left - 4 : box.right + 4) - frame.left}px`;
    marker.style.top = `${lines.top - frame.top - lines.step * 6}px`;
    marker.style.height = `${lines.bottom - lines.top + lines.step * 8}px`;
    $("pagesFrame").append(marker);
  }
}

// A popup that opens below the button it belongs to, and closes when anything else is pressed.
function popupFor(buttonId, popupId) {
  $(buttonId).addEventListener("click", event => {
    const popup = $(popupId);
    popup.hidden = !popup.hidden;
    const at = event.currentTarget.getBoundingClientRect();
    popup.style.top = `${at.bottom + 4}px`;
    popup.style.left = `${Math.max(4, Math.min(at.left, innerWidth - popup.offsetWidth - 4))}px`;
  });
  document.addEventListener("pointerdown", event => {
    if (!$(popupId).hidden && !event.target.closest(`#${popupId}, #${buttonId}`)) $(popupId).hidden = true;
  });
}

// Another score starts from its beginning, with no loop.
function forgetPlayback() {
  stopPlaying();
  Object.assign(playState, { place: 0, looping: false, loop: null, data: null, chosen: null });
  drawPracticeState();
  $("timeCounter").value = timeText(0);
  $("beatCounter").value = "1.1";
}

function setUpPlayback() {
  $("tempoButton").querySelector(".tempoNote").outerHTML = glyphIcon("ECA5", 3.6);
  $("play").addEventListener("click", togglePlaying);
  $("rewind").addEventListener("click", rewind);
  $("loop").addEventListener("click", toggleLoop);
  $("metronome").addEventListener("click", toggleMetronome);
  $("audio").addEventListener("click", saveAudio);
  popupFor("playSettings", "playPopup");
  popupFor("tempoButton", "speedPopup");
  $("playChords").checked = playState.chords;
  $("playChords").addEventListener("change", event => {
    playState.chords = event.target.checked;
    keepSetting("chordSymbols", playState.chords ? "on" : "off");
    if (player) playFrom(playingPlace());
  });
  $("loopLeft").addEventListener("click", () => { $("playPopup").hidden = true; setLoopEnd("first"); });
  $("loopRight").addEventListener("click", () => { $("playPopup").hidden = true; setLoopEnd("last"); });
  $("speed").addEventListener("input", event => setSpeed(event.target.value));
  $("speedValue").addEventListener("change", event => setSpeed(event.target.value));
  $("speedReset").addEventListener("click", () => setSpeed(100));
  // A counter takes a new place when it's typed in, then Enter or a click elsewhere.
  const typed = (id, read) => {
    $(id).addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === "Escape") { event.preventDefault(); event.target.blur(); }
    });
    $(id).addEventListener("change", event => {
      const data = playState.data;
      const place = data ? read(data, event.target.value) : null;
      if (place !== null && Number.isFinite(place)) playFrom(Math.min(Math.max(place, 0), data.total));
      else drawPosition(player ? playingPlace() : playState.place);
    });
  };
  typed("timeCounter", (data, text) => {
    const seconds = secondsIn(text);
    return seconds === null ? null : clockFor(data, playState.speed).place(seconds);
  });
  typed("beatCounter", (data, text) => {
    const [measure, beat = 1] = text.trim().split(/[.:,\s]+/).map(Number);
    return Number.isInteger(measure) && Number.isInteger(beat) && beat >= 1 ? placeAtBeat(data, measure, beat) : null;
  });
}
