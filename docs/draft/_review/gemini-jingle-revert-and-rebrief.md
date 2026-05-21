# Gemini Jingle: Revert Status, Diagnosis, and Rebrief

_Agent: verify-gemini-revert-rebrief-jin-dd51ed — 2026-05-21_

---

## Revert Status

**Verdict: Revert was incomplete. Two files remained. Both are now deleted.**

### What Gemini left behind

| Location | File | Size | Status |
|---|---|---|---|
| `frontend/public/sounds/` (main repo) | `needle-close-victory.mid` | 247 bytes | **Deleted** |
| `frontend/public/sounds/` (main repo) | `needle-close-victory.m4a` | 32 KB | **Deleted** |

The `sounds/` directory and both files were untracked (never committed). The `frontend/public/index.html` in the same directory is a pre-existing tracked file (committed in `9d6f869 Add recent specs widget...`) — left untouched.

### What was already clean

- `frontend/public/sounds/` no longer exists. `git status` is clean on that path.
- No `.mid` files in the last 2 hours anywhere in the repo.
- No `.sf2` soundfont files in the repo tree.
- No `.wav` files matching the jingle. (The rendered audio was m4a, not wav — Gemini likely used ffmpeg or afconvert after fluidsynth.)
- Worktree `frontend/` dir: only contains `frontend/public/index.html` (tracked, pre-existing). No sounds there.

### Brew

`fluid-synth 2.4.4` is installed at `/opt/homebrew/Cellar/fluid-synth/2.5.4/`. Left as-is per instructions — it's useful and harmless.

---

## Why Gemini Struggled

### 1. The fcp:midi plugin has no render verb

The full plugin reference (from `midi_help`) contains these verbs:
`note`, `chord`, `track`, `cc`, `bend`, `tracker`, `mute`, `solo`, `program`, `tempo`, `time-sig`, `key-sig`, `marker`, `title`, `remove`, `move`, `copy`, `transpose`, `velocity`, `quantize`, `modify`, `repeat`, `crescendo`, `decrescendo`.

**There is no `render`, `export`, `play`, or audio-conversion verb.** The plugin only saves `.mid` files via `midi_session save as:<path>`. Gemini exhausted the plugin's capabilities, confirmed the save, then went outside it to get audible output — reaching for `fluidsynth` via bash because that was the only path it knew.

### 2. The brief implied "hear it" without saying so

Asking Gemini to "compose the jingle live" during a demo-prep session carries an implicit expectation: Tori wants to *hear* the result. The brief didn't say "stop at .mid" so Gemini reasonably tried to make the sound playable. That's the gap. A good brief makes the stopping point explicit.

### 3. Wrong save path (`frontend/` vs `app/`)

CLAUDE.md says: *"Servers: `scripts/dev-backend.sh` and `scripts/dev-frontend.sh`."* It does NOT say "the React app lives in `app/`". Gemini had no signal that `frontend/` was wrong. It guessed a conventional React layout (`frontend/public/sounds/`) and was incorrect. The right path is `app/public/` or a dedicated assets dir.

### 4. midi_query 3x = uncertainty about persistence

Gemini queried state three times after saving. The plugin's `midi_query` inspects in-memory state, not what's on disk. Gemini couldn't tell whether `midi_session save` had actually written the file, so it kept checking. This is a plugin feedback gap — save doesn't return a confirmation line that names the output path.

### 5. brew install mid-session

Gemini had to discover that `fluidsynth` wasn't installed (`which fluidsynth timidity` first), then install it live. This is a sign that the render step was unplanned and exploratory. A brief that says "stop at .mid" eliminates this entirely.

---

## Gemini Brief for Next Attempt

```
Task: compose the needle-close victory jingle and save it as a .mid file.

Rules:
- Use fcp:midi only. No bash audio commands. No brew installs. No fluidsynth. No rendering.
- Save target: app/assets/victory-fanfare/needle-close-victory.mid
  (create the directory first if it doesn't exist:
   mcp__ostk__fs_ops op="mkdir" path="app/assets/victory-fanfare")
- After save, STOP. Do not verify by playing. Do not render to wav/mp3/m4a.
  The wav step happens later via a JS MIDI player (Tone.js) — .mid is the only deliverable.

Composition spec:
- 4-8 notes, ~2 seconds total
- C major key, fast tempo (140+ BPM)
- Triumphant-but-goofy: think Mario power-up or Zelda item-get
- Single melodic track, instrument: program:9 (glockenspiel) or vibraphone

Steps:
1. midi_session new "needle-close-victory"
2. midi: set tempo, add track, add 4-8 notes
3. midi_session save as:app/assets/victory-fanfare/needle-close-victory.mid
4. Done. Report the save path and file size only.
```
