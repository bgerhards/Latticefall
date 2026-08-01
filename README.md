# Latticefall

An isometric tower defence about **power as the real currency**. You hold transit anchors on a
precursor network humanity did not build and cannot switch off. Emplacements cost money to
build and **draw continuous megawatts to run** — and exceeding your reactor capacity browns out
the whole bus, at a price set by how far over you are. It is never a blocked build; it is
always a decision.

24 anchors, three acts. Each act introduces one antagonist, one biome, one power tier, and one
mechanic that invalidates the previous act's dominant strategy.

Grounded military sci-fi. Professionals doing dangerous technical work. Dry, not heroic.

**Build journal:** <https://bgerhards.github.io/Latticefall/> — written as the game is made,
including the wrong turns.

---

## Running the game

### 1. You need these three things

| | Why |
|---|---|
| **Godot 4.7.1** | The project targets 4.7 with the **GL Compatibility** renderer. Other 4.x versions may open it but are untested. [Download](https://godotengine.org/download) |
| **Git LFS** | **Not optional.** Every sprite, sound and font is stored in LFS — 694 files, ~140 MB. Without it you get 130-byte text pointers instead of images, and the game loads but draws nothing recognisable. |
| ~300 MB disk | ~140 MB of LFS assets, plus the import cache Godot builds on first open. |

### 2. Clone — install Git LFS *first*

```bash
git lfs install                     # once per machine, BEFORE cloning
git clone https://github.com/bgerhards/Latticefall.git
cd Latticefall
```

**If you already cloned without LFS**, you do not need to start over:

```bash
git lfs install
git lfs pull
```

**How to tell it went wrong:** open any file under `assets/renders/`. A real PNG is tens of
kilobytes; an unfetched LFS pointer is ~130 bytes of plain text beginning
`version https://git-lfs.github.com/spec/v1`. If you see that, run `git lfs pull`.

### 3. Open it in the Godot **editor** once before you play

This step is required, and skipping it is the most common way to end up with a
broken-looking game. Open the Godot project manager, **Import** the cloned folder, and let it
finish.

**Why it matters, concretely.** Godot's *game* mode never imports assets — it loads a cache in
`.godot/`, which is not in the repository because it is build output. Until the editor has
built that cache:

- Every texture is missing, so the board renders blank or flat grey.
- Global class names (`class_name`) live in `.godot/global_script_class_cache.cfg` and do not
  exist yet — and **the symptom of a missing global class is a hang or a blank frame, not an
  error message.** A scene simply never finishes loading.

The first import takes a minute or two. It happens once, and again after any `git pull` that
brings new art.

### 4. Play

Press **F5** in the editor, or run the project. It opens on the menu; pick an anchor.

---

## Controls

Keyboard and mouse, or gamepad — both are fully supported.

| Key | |
|---|---|
| `` ` `` | **Game speed** — cycles 1× / 2× / 3×. Shown top-left. |
| `1` `2` `3` | **Abilities** — Threshold Surge, Overcharge, Shutter. Charge state is shown bottom-left. |
| Mouse / `WASD` / arrows | Move the board cursor |
| `E` | Build at the cursor |
| `Q` | Sell the selected emplacement |
| `R` | Upgrade the selected emplacement |
| `F` | Take the selected emplacement **off the bus** without selling it |
| `T` | Cycle targeting mode |
| `C` | Call the next wave early, for a cash bonus |
| `M` | Focus the tactical map |
| `H` | Hide the HUD |
| `=` / `-` | Zoom · `Home` resets the camera |
| `Tab` | Pause |

Interface scale goes to 200% for accessibility; both instrument panels scroll when it does.

---

## Working on the game

Only needed if you intend to change something. **Playing needs none of this.**

### The tooling

Python 3.12, standard library plus `numpy` and `soundfile`. Everything is a script under
`tools/`, and every script is idempotent and safe to re-run.

```bash
python3 -m venv .venv
.venv/bin/pip install numpy soundfile
```

### The gate

One command, one exit code. **If it fails, do not commit.**

```bash
.venv/bin/python tools/check.py --tier 1     # ~6 s, pre-commit
.venv/bin/python tools/check.py --tier 2     # ~28 s, pre-push
.venv/bin/python tools/check.py --tier 3     # what CI runs on every pull request
.venv/bin/python tools/check.py              # tier 4 — everything, including full parity
.venv/bin/python tools/check.py --list       # every check and its tier
```

A tier is a **minimum**: a tier-1 check also runs at 2, 3 and 4. A check the tier excludes
reports `skip` and **is not a pass** — the summary line names every check that did not run.

### The thing that makes a balance claim falsifiable

**The rules of the game exist twice.** `scripts/anchor_sim.gd` is what the engine runs;
`sim/engine.py` is a headless reference implementation in pure Python. `tools/test_parity.py`
diffs them across **1,440 runs** — 24 anchors × 20 grading policies × 3 difficulties — and they
must agree exactly. CI runs that on every pull request touching a rules file, on **both Linux
and Windows**, because floating-point library differences between platforms are real and
measured.

That is why balance work here reports distributions and refusals rather than adjectives.

```bash
.venv/bin/python -m sim.run --jobs 8         # grade all 24 anchors headlessly
.venv/bin/python -m sim.coverage --jobs 8    # per-emplacement uptime and siting
.venv/bin/python tools/test_parity.py        # ~10 minutes
```

### Content is data

A level, wave table, tower or dialog line is a JSON file under `data/`, validated against a
schema in `data/schema/`. You can retune the whole game without opening the engine.

### Layout

```
data/        game content — the source of truth for everything tunable
sim/         headless combat simulator — pure Python, no Godot
tools/       deterministic scripts: the gate, balance, audio, Blender render pipeline
scenes/      Godot scenes
scripts/     GDScript
assets/      committed build output — renders and audio
docs/        STATE, BACKLOG, DECISIONS, the PRD, and the build journal
```

`docs/STATE.md` says where the project is right now. `docs/DECISIONS.md` is append-only and
records why things are the way they are, including the rejected alternatives. `CLAUDE.md` is
the working agreement, and carries the traps that have actually cost this project time.

---

## Status

In development, and playable end to end. **Not packaged for distribution yet** — there is no
installer and no store build, so you run it from the editor. Packaging and a storefront
release are tracked in the backlog.

## Licence

Not yet determined. Music is generated under the author's own subscription; sound effects are
synthesised in-repo from scripts. See `docs/DECISIONS.md` entry 038 for the asset-licensing
position.
