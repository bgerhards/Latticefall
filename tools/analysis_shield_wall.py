"""LF-032 take two: wall in a mid-priority slot so the guns keep their positions,
and sweep the slow strength as well as the draw. Baseline to beat is 7 guns +
relay with no wall: 10/9/6 lives at anchor-04."""
import json, sys, pathlib
sys.path.insert(0, "/Users/briangerhards/dev/defend-claude")
TOWERS = pathlib.Path("/Users/briangerhards/dev/defend-claude/data/towers.json")
orig = TOWERS.read_text()
P, R, W = "pulse-turret", "scan-relay", "shield-wall"
BASE = [10, 9, 6]

def score(draw, rng, slow):
    doc = json.loads(orig)
    for t in doc["towers"]:
        if t["id"] == W:
            t["draw_mw"] = draw; t["range"] = rng
            t["effect"] = {"type": "slow", "value": slow}
    TOWERS.write_text(json.dumps(doc, indent=2) + "\n")
    for m in [m for m in list(sys.modules) if m.startswith("sim.")]: del sys.modules[m]
    from sim.content import load_anchor, load_enemies, load_towers
    from sim.engine import DIFFICULTIES, Placed, Policy, Sim
    class Fixed(Sim):
        def pin(self, spec):
            self.placed = []; slots = self._slot_priority()
            for i, tid in enumerate(spec):
                self.placed.append(Placed(tower=self.towers[tid], slot=slots[i]))
                self.free_slots.remove(slots[i]); self.funds -= self.towers[tid].cost
                self.spend += self.towers[tid].cost
        def _try_build(self): return
        def _shed_load(self): return
    # wall third: guns keep the two best slots, wall still sits on the path
    spec = [P, P, W] + [P]*5 + [R]
    lives = []
    for diff in DIFFICULTIES:
        s = Fixed(load_anchor("anchor-04"), load_towers(), load_enemies(),
                  Policy("x", [], allow_overdraw=True), diff)
        s.pin(spec); o = s.run()
        lives.append(o.lives_left if o.won else -1)
    return lives

print(f"baseline, no wall: {BASE}   (wall now in the 3rd-best slot)\n")
print(f"{'draw':>5}{'range':>7}{'slow':>6} | {'7g+wall+relay':>16}  verdict")
hits = []
for draw in (26, 30, 34):
    for rng in (3.0, 3.6):
        for slow in (0.35, 0.28):
            v = score(draw, rng, slow)
            ok = all(x >= 0 for x in v) and sum(v) > sum(BASE)
            print(f"{draw:>5}{rng:>7}{slow:>6} | {str(v):>16}  {'<-- BEATS BASELINE' if ok else ''}")
            if ok: hits.append((draw, rng, slow, v))
TOWERS.write_text(orig)
print("\nrestored towers.json\nbeats baseline:", hits)
