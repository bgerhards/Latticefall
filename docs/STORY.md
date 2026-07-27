# Story bible

Names are governed by `docs/NOMENCLATURE.md`. Read it before writing a line.

---

## Premise

A dead civilization — the Ordinal — built a transit network across an unknown number of
worlds, then wrote a containment order and stopped existing. The network never turned off.

Task Force Meridian is sixty-one people sent through it to hold anchors nobody can close,
against opposition nobody briefed them on, with a power budget that was never sized for a
war. They are not explorers. They are a holding action that has not been told what it is
holding.

**The turn:** the automated wardens Meridian spends Act I destroying were the containment.
Every anchor Meridian powers up and secures is a door propped open for the thing the
Ordinal built the whole network to isolate.

## Themes

- **Competence against scale.** Everyone is good at their job. It is not going to be enough.
- **Power as a moral budget.** Every choice is a choice about what to let fail.
- **The cost of understanding.** Okonkwo is the only one who can read what is happening,
  and every translation makes things worse.

## Cast

| | |
|---|---|
| **Vasquez** | Field lead. Dry, decisive, allergic to ceremony. Deflects with understatement when it is worst. Never says she is afraid; says the equipment is. |
| **Okonkwo** | Lattice specialist, civilian contractor. Precise, hedges nothing, over-explains when frightened. The only one who says what things actually are. |
| **Control** | Meridian Actual duty officer. Procedural. Reads bad news in the same tone as good. Rotates — deliberately not a character, which makes the one time Control breaks tone land hard (anchor 22). |
| **Ferrar** | Sable Reach negotiator. Warm, reasonable, entirely untrustworthy. Uses first names. Never raises his voice, including when ordering people killed. |

---

## Act I — Dead Air · anchors 01–08

Cold, automated, empty. Biome: a shuttered Ordinal facility on a world with no sky worth
looking at. Antagonist: wardens. Power tier: 60–110 MW.

| # | Title | Beat |
|---|---|---|
| 01 | Carrier Signal | Arrival. The anchor has been powered for eleven years with nobody home. Teaches draw vs. cost. Closing line: the containment order names sixty anchors, and this is number four. |
| 02 | Line of Sight | Air units arrive. Scan relay unlocks — and cutting it to afford a volley is the first real power decision. |
| 03 | Nothing Answers | Meridian finds Ordinal remains. They died at their posts, facing *inward*. |
| 04 | The Fourth Door | Okonkwo gets the order half-translated. It is not a defence order. It is a quarantine. |
| 05 | Housekeeping | A quiet one. Vasquez and Okonkwo talk about home. The wardens are almost sympathetic here — they have been doing this alone for a very long time. |
| 06 | Hard Currency | First korrite seam. Establishes why anyone funds this. Ion lance unlocks. |
| 07 | Someone Else's Boots | Fresh tracks. Equipment that is not Ordinal and not Meridian. Somebody has been here for months. |
| 08 | Eleven Years of Nothing | Act finale. The wardens stop attacking Meridian and turn to face the ring. Whatever they were containing has noticed the noise. |

**Act I mechanic:** power scarcity itself. Nothing exotic — the player learns that a built
board is not a solved board.

---

## Act II — Salvage Rights · anchors 09–16

Human, tense, dirty. Biome: anchors half-stripped, scaffolding, contractor lighting bolted
over Ordinal stone. Antagonist: Sable Reach. Power tier: 110–180 MW.

| # | Title | Beat |
|---|---|---|
| 09 | Contract Terms | Ferrar opens with an offer, not a shot. He knows Vasquez's service record. |
| 10 | Right of Way | Sable Reach cuts the bus mid-wave. The enemy attacks your economy directly. |
| 11 | Good Faith | The negotiation anchor. Ferrar is telling the truth about the Ordinal and lying about everything else. |
| 12 | Asset Recovery | Meridian learns Sable Reach has been *selling bind keys*. Anchors are being opened for money. |
| 13 | Duty of Care | A Sable Reach crew is overrun. Vasquez holds the anchor for their evacuation and takes losses doing it. |
| 14 | What He Paid For | Ferrar's contract surfaces. The signatory is a government that briefed Meridian. |
| 15 | Breach of Contract | Open war with Sable Reach. No dialogue from Ferrar this level — conspicuously. |
| 16 | Sable Reach | Act finale. Ferrar's last stand is not against Meridian. He has seen what is coming through and he is trying to close the door he sold. He fails. |

**Act II mechanic:** the bus is now contested. `drains_mw` units steal capacity while alive,
so power is no longer a budget you control alone.

---

## Act III — The Hollow · anchors 17–24

Dread, wrong, vast. Biome: anchors the Lattice itself seems to be metabolizing. Antagonist:
the Hollow. Power tier: 180–260 MW, but capacity now *degrades* during a level.

| # | Title | Beat |
|---|---|---|
| 17 | Circulatory | The deep lattice. Anchors with no bind key, connected to nothing Meridian can map. |
| 18 | Ordinal Arithmetic | Okonkwo finishes the translation. The Ordinal did not lose. They chose containment over survival. |
| 19 | Sixty-One | The number of anchors in the order is the number of people in Task Force Meridian. Coincidence, Okonkwo insists, twice. |
| 20 | The Long Silence | The wardens fight *alongside* Meridian. Nobody comments on it. |
| 21 | Attrition | Capacity degrades wave over wave. There is no build that holds; only choices about what fails first. |
| 22 | Meridian Actual | Home anchor is compromised. Control breaks procedure for the first and only time, mid-sentence. |
| 23 | What Was Held | Okonkwo stays behind to keep a ring powered. The scene is written as a technical handover, not a farewell. |
| 24 | Close It From This Side | The last anchor that can still be shut. Ending is a cost, not a victory. Vasquez does not get a line after the last wave. |

**Act III mechanic:** degrading capacity. The reactor is being drawn on by something else.
The player's mastery of the power system is turned against them — the thing they got good at
is the thing that stops working.

---

## Writing rules

- **No speeches.** Past two sentences, cut it.
- **Exposition as shop talk.** Nobody explains the Lattice to someone who works on it.
- **Underplay every reveal.** The horror lands because the people describing it are bored.
- **Mid-wave lines are interruptible.** No line may carry information the player cannot
  afford to miss — the schema enforces this and the validator checks it.
- **The Hollow is never described directly.** Only its effects, and only in instrument
  readings. If a character says what it looks like, the line is wrong.
- **Never end on triumph.** Meridian does not win. It survives, and less of it each act.
