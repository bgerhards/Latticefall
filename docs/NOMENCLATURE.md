# Nomenclature bible

Latticefall is *influenced by* a well-known television franchise about a transit network.
It must never borrow that franchise's vocabulary. This file is the authority. Check it
before naming anything — a rename after forty assets and three hundred dialog lines is the
expensive kind of mistake, and this is the file that prevents it.

**Rule: if a term is not in the canonical list below, it is not a term yet. Add it here
first, then use it.**

---

## Banned — never appears in code, data, dialog, filenames, or comments

These are franchise-specific coinages or franchise-defining usages. Some are ordinary
English words; what is banned is using them *as the game's terminology*.

| Banned | Use instead |
|---|---|
| Stargate, star gate | Lattice, anchor |
| Chevron | Ward (the six locking blocks on an anchor ring) |
| Dial, dialling, DHD, dial-home device | Bind, binding, **bindstone** |
| Iris | Shutter |
| Event horizon, puddle, kawoosh | Threshold, the surge |
| Wormhole | Transit, the run |
| Naquadah, trinium | Ordinal alloy, **korrite** |
| ZPM, zero point module | Core, **reactor core** |
| Goa'uld, Ori, Wraith, Replicator, Asgard, Ancients, Furlings, Nox | Ordinal (builders), the Hollow (antagonist) |
| System Lord, Jaffa, Tok'ra, symbiote | Warden, Sable Reach operator |
| Sarcophagus, ribbon device, zat, staff weapon | Restorer, arc node, pulse turret |
| SGC, Stargate Command, SG-1, Cheyenne Mountain | Meridian Actual, Task Force Meridian |
| Gate address, point of origin, glyph | Bind key, home ward, **sigil** |
| Ha'tak, al'kesh, death glider | (no ships in scope) |
| Prometheus, Daedalus, Atlantis, Destiny | (no ships in scope) |
| Ascension, ascended being | (not used) |
| Supergate, subspace | Deep lattice |

Also avoid: ring-shaped gate imagery captioned with numbered "chevron lock" beats, the
seven-symbol address motif, and any nine-glyph or eight-glyph address mechanic.

---

## Canonical terms

### The network

| Term | Meaning |
|---|---|
| **the Lattice** | The precursor transit network. Always capitalized, always "the". |
| **anchor** | One node of the Lattice. Also one level. `anchor-07`. |
| **ring** | The physical torus at an anchor through which transit happens. |
| **ward** | One of six locking blocks around a ring. Lit = engaged. |
| **bindstone** | The pedestal that binds one anchor to another. |
| **bind key** | The identifier of a destination anchor. |
| **sigil** | A single Ordinal character. Sixty-one of them exist. |
| **threshold** | The active transit surface inside an engaged ring. |
| **the surge** | The discharge when a threshold forms. Kills anything in the ring. |
| **shutter** | The armoured plate Meridian bolts over a ring to deny transit. |
| **deep lattice** | Anchors with no known bind key. Act III territory. |

### Power

| Term | Meaning |
|---|---|
| **the bus** | An anchor's power distribution. "Your bus is at ninety-six." |
| **reactor core** | The power source. Capacity measured in MW. |
| **draw** | Continuous MW an emplacement consumes while online. |
| **capacity** | MW the core supplies. Fixed per anchor, raised only by story beats. |
| **brownout** | Draw exceeds capacity. All systems −40% fire rate. |
| **breaker** | Manual cutoff for one emplacement. |
| **korrite** | Ordinal alloy. The reason anyone is out here. |

### Factions

| Term | Meaning |
|---|---|
| **the Ordinal** | Builders of the Lattice. Extinct, or wanted to appear so. Named for their obsession with ordering and numbering. |
| **warden** | Automated Ordinal construct still executing a containment order. Act I. |
| **Sable Reach** | Private recovery contractor stripping anchors under a deniable contract. Act II. |
| **the Hollow** | What the containment order was written about. Act III. Never described directly in dialog — only its effects. |
| **Task Force Meridian** | The player's unit. Off-books, multinational, sixty-one people. |
| **Meridian Actual** | Command, back through the anchor. Voice on the radio. |

### Emplacements

`pulse turret` · `arc node` · `ion lance` · `mortar emplacement` · `flak array` ·
`shield wall` · `scan relay` · `anchor damper` · `restorer`

### Enemy units

One row per unit in `data/enemies.json`. Ordinal constructs are *wardens*; Sable Reach
sends plain trade nouns, because they are contractors and their kit is rented; the Hollow
gets abstract nouns only, since nothing in the game ever says what it is.

| Unit | Faction | Role |
|---|---|---|
| **Warden Drone** | Ordinal | Act I baseline. |
| **Warden Mote** | Ordinal | Act I air. Unseen without a scan relay. |
| **Warden Heavy** | Ordinal | Act I armour. |
| **Reach Picket** | Sable Reach | Act II escort. The one Reach unit carrying no tap. |
| **Reach Sapper** | Sable Reach | Act II. The largest single drain in the act. |
| **Reach Skiff** | Sable Reach | Act II air. |
| **Reach Breacher** | Sable Reach | Act II. Screened. |
| **Reach Bulwark** | Sable Reach | Act II heavy. Screened and plated. |
| **Hollow Shard** | the Hollow | Act III escort. One piece of an Echo, travelling alone. |
| **Hollow Drift** | the Hollow | Act III air. |
| **Hollow Echo** | the Hollow | Act III baseline. Screened. |
| **Hollow Vessel** | the Hollow | Act III mid. Carries most of the act's drain. |
| **Hollow Column** | the Hollow | Act III heavy. The largest draw in the game. |

### Cast

| Name | Role | Voice |
|---|---|---|
| **Vasquez** | Field lead. Player's counterpart. | Dry, decisive, allergic to ceremony. Short sentences. |
| **Okonkwo** | Lattice specialist. Civilian. | Precise, hedges nothing, explains too much when frightened. |
| **Control** | Meridian Actual duty officer. Rotating. | Procedural. Reads bad news in the same tone as good. |
| **Ferrar** | Sable Reach negotiator. Act II. | Warm, reasonable, entirely untrustworthy. Uses first names. |

---

## Naming rules

- Anchors are numbered, never named, in UI: `ANCHOR 07`. Act titles are prose.
- Ordinal things get hard consonants and no apostrophes: *korrite*, *bindstone*, *ward*.
  Apostrophes in alien words are a franchise tell — do not use them.
- Meridian things get plain military English: *shutter*, *breaker*, *scan relay*.
- Never invent a Latin or Greek compound for Ordinal tech. They are older than both and
  the language should not flatter human roots.

## Adding a term

1. Confirm it collides with nothing in the banned table.
2. Add it to the right table above with a one-line meaning.
3. Note it in `docs/DECISIONS.md` if it changes an established name.
4. `grep -ri "<old term>" --include=* .` before renaming anything already in use.
