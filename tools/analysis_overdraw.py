"""Does a marginal overdraw ever pay under decision 022?

The flat-cliff rule made "never exceed capacity" unconditionally correct. This asks
whether the proportional rule creates a real decision: boards that stop at capacity
versus boards that take one or two emplacements past it.
"""
import sys
sys.path.insert(0, "/Users/briangerhards/dev/defend-claude")
from sim.content import load_anchor, load_enemies, load_towers
from sim.engine import DIFFICULTIES, Placed, Policy, Sim, brownout_penalty

P, R = "pulse-turret", "scan-relay"


class Fixed(Sim):
    def pin(self, spec):
        self.placed = []
        slots = self._slot_priority()
        for i, tid in enumerate(spec):
            self.placed.append(Placed(tower=self.towers[tid], slot=slots[i]))
            self.free_slots.remove(slots[i])
            self.funds -= self.towers[tid].cost
            self.spend += self.towers[tid].cost
    def _try_build(self): return
    def _shed_load(self): return


def play(aid, spec, diff):
    s = Fixed(load_anchor(aid), load_towers(), load_enemies(),
              Policy("x", [], allow_overdraw=True), diff)
    s.pin(spec)
    return s.run()


towers = load_towers()
for aid in ("anchor-04",):
    a = load_anchor(aid)
    cap = a.capacity_mw
    boards = {
        "6 guns + relay  (under)": [P]*6 + [R],
        "7 guns + relay  (under)": [P]*7 + [R],
        "8 guns          (at cap)": [P]*8,
        "8 guns + relay  (OVER)":  [P]*8 + [R],
        "9 guns + relay  (OVER)":  [P]*9 + [R],
    }
    print(f"{aid} · capacity {cap:.0f} MW\n")
    print(f"{'board':<26}{'draw':>6}{'penalty':>9} | " +
          " | ".join(f"{d:>12}" for d in DIFFICULTIES))
    for name, spec in boards.items():
        draw = sum(towers[t].draw_mw for t in spec)
        pen = brownout_penalty(draw, cap)
        cells = []
        for diff in DIFFICULTIES:
            o = play(aid, spec, diff)
            cells.append(f"{'WON' if o.won else 'lost w%d' % (o.died_on_wave or 0):>7} lv{o.lives_left}")
        print(f"{name:<26}{draw:>5.0f}W{pen*100:>8.1f}% | " + " | ".join(f"{c:>12}" for c in cells))
