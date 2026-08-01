"""LF-014, corrected: the wall must occupy a slot the path actually passes.

The first run put the wall in the last-priority slot — the one furthest from the
path — so nothing ever entered its radius, it never came online, and the result was
identical to having no wall at all. Here the wall takes the slot nearest the path.
"""
import math, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sim.content import load_anchor, load_enemies, load_towers
from sim.engine import DIFFICULTIES, Placed, Policy, Sim

P, W, R = "pulse-turret", "shield-wall", "scan-relay"


class Fixed(Sim):
    def pin(self, spec):
        self.placed = []
        slots = self._slot_priority()
        for i, tid in enumerate(spec):
            # PLC-01: Placed carries x/y, not a slot tuple; there is no free_slots list
            # to keep in sync any more (Sim._slot_priority() already computes availability
            # fresh from self.placed each call, and this file bypasses it here anyway by
            # overriding _try_build() to a no-op below).
            self.placed.append(Placed(tower=self.towers[tid], x=slots[i][0], y=slots[i][1]))
            self.funds -= self.towers[tid].cost
            self.spend += self.towers[tid].cost
    def _try_build(self): return
    def _shed_load(self): return


class Toggled(Fixed):
    wall = 0
    raised_ticks = 0
    total_ticks = 0
    def _tick_once(self):
        w = self.placed[self.wall]
        near = any(
            math.hypot(w.slot[0] - self.a.point_at(u.lane, u.dist)[0],
                       w.slot[1] - self.a.point_at(u.lane, u.dist)[1]) <= w.tower.range
            for u in self.units if u.alive)
        w.online = near
        self.total_ticks += 1
        self.raised_ticks += 1 if near else 0
        super()._tick_once()


def run(cls, spec, diff, **kw):
    s = cls(load_anchor("anchor-04"), load_towers(), load_enemies(),
            Policy("x", [], allow_overdraw=True), diff)
    for k, v in kw.items(): setattr(s, k, v)
    s.pin(spec)
    return s, s.run()


def show(o):
    return f"{'WON' if o.won else 'lost w%d' % (o.died_on_wave or 0)} lv{o.lives_left}"


towers = load_towers()
cap = load_anchor("anchor-04").capacity_mw
print(f"anchor-04 · cap {cap:.0f} MW · wall {towers[W].draw_mw:.0f} MW · wall on the "
      f"slot NEAREST the path\n")
print(f"{'difficulty':<11}{'no wall (7g+relay)':>21}{'wall always on':>18}"
      f"{'wall toggled':>15}{'raised %':>10}")
for diff in DIFFICULTIES:
    _, base = run(Fixed, [P]*7 + [R], diff)
    _, always = run(Fixed, [W] + [P]*7 + [R], diff)
    st, tog = run(Toggled, [W] + [P]*7 + [R], diff, wall=0)
    pct = 100.0 * st.raised_ticks / max(st.total_ticks, 1)
    print(f"{diff:<11}{show(base):>21}{show(always):>18}{show(tog):>15}{pct:>9.1f}%")
