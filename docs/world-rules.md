# World rules

Every world carries a **WorldRules** document: a validated, versioned description of how
that universe works. It is the first thing the Story Director is told and it outranks the
model's instincts about what would make a better scene.

## Rules are not state

This is the distinction the whole design rests on.

| | Answers | Changes | Lives in |
|---|---|---|---|
| **WorldRules** | *How does this universe work?* | essentially never | `worlds.rules_json` (this document) |
| WorldState | *What is true right now?* | constantly | not implemented yet |
| CharacterState | *What is true of this character right now?* | constantly | not implemented yet |

"Resurrection is impossible in this world" is a rule. "The king is dead" is state. If a
value would change during play, it does not belong here — and nothing in this document
is ever written back to during a turn.

## The 0..100 scale

Most values are a `Setting`: a constrained int, `0 <= n <= 100`.

```text
0    effectively absent
25   low
50   moderate
75   high
100  extreme
```

**These are intensities, not probabilities.** `danger.lethality = 40` does not mean 40%
of encounters kill someone; it means lethality sits somewhat below the middle of what
this universe is capable of. Nothing in the codebase reads them as odds, and nothing may.
Turning an intensity into a probability needs context this document does not have — who,
where, under what pressure — and that is a decision for a future rules-resolution layer.

The five bands the web UI shows (`Very low … Very high`) are a reading aid rendered in
`apps/web/src/features/worlds/summarizeRules.ts`. The backend never buckets, and no
behaviour branches on "high".

## The document

`WorldRulesV1` has thirteen sections. Every section is frozen (`frozen=True`) and closed
(`extra="forbid"`): a misspelled key is a loud error, never a value that silently keeps
its default. For a document whose entire job is to constrain a language model, silently
ignoring a setting would be the worst available failure mode.

| Section | What it configures |
|---|---|
| `narrative` | tone, protagonist centrality, coincidence, deus ex machina, plot armor |
| `mortality` | who can die, how final it is, injury, incapacitation, resurrection |
| `danger` | how often, how severe, how fast it escalates, how lethal |
| `consequences` | severity, persistence, social/legal/faction/economic memory, irreversibility |
| `power` | power scale, tier ceilings, what matters (training/talent/experience), rule-breaking |
| `supernatural` | whether it exists, how common, who knows, how regulated |
| `progression` | whether characters grow, how fast, ceilings, breakthroughs, regression |
| `society` | law, corruption, inequality, mobility, information spread, reputation |
| `resources` | scarcity per kind, healing accessibility, recovery speed |
| `simulation` | autonomy, offscreen events, scope, time progression |
| `chance` | how mechanical uncertainty is produced, critical outcomes, rerolls |
| `rules` | which authority wins when drama and rules disagree |
| `content` | how graphic the *description* is |

The authoritative definition — including every default and every field docstring — is
`apps/api/app/domain/world_rules/rules.py`. This page explains the parts that are easy to
get wrong.

### Plot armor acts before an outcome, never after

`narrative.plot_armor` has three tiers: `player`, `important_npcs`, `ordinary_npcs`.

High plot armor means a plausible way out is more likely to **exist in the first place** —
a side door, a witness, an interruption arriving a moment early. Low plot armor means
those escape routes simply are not there.

Once something is resolved, plot armor may **not**:

- alter a rolled result
- undo damage that was dealt
- resurrect anyone
- invalidate a recorded `GameEvent`
- rewrite what already happened

This is the most abusable value in the document. It is stated in `PlotArmorRules`, again
in the Story Director prompt (principle 3), and again here, because the failure mode —
a model quietly retconning a death because the scene wanted it — destroys the thing the
whole engine is for.

### Danger is not lethality

`danger` is four independent dimensions, routinely conflated elsewhere:

```text
encounter_frequency  how often danger appears
encounter_severity   how serious it tends to be
lethality            how likely a serious encounter is to kill or maim permanently
escalation_rate      how fast an unresolved situation gets worse
```

The `shonen` preset exists partly to prove the split is real: `danger.baseline = 75`
against `lethality = 30`. Fights happen constantly and people lose them without dying.
Collapsing these into one "difficulty" number would make that world inexpressible.

None of them is enemy health scaling, and none of them is `content.gore`.

### Optimism and darkness are independent

`narrative.darkness` is **not** `100 - optimism`, and any future change that makes it so
is a regression.

- `optimism` — tendency toward hope, recovery, and chances to rebuild.
- `darkness` — tolerance for tragedy, betrayal, cruelty, moral ambiguity.

A world can be bleak and ultimately hopeful (both high); a world can be flat and
uneventful (both low). Darkness is also distinct from danger: darkness is moral, danger is
physical. A warm world can be deadly, and a bleak world can be survivable.

`protagonist_centrality` is a third separate axis and is **not** plot armor: events
gravitating toward the player is not the player surviving them.

### Consequences persist

`consequences.actions_can_close_content = true` means destroyed opportunities stay
destroyed. Kill the only merchant who sold a thing and that thread is gone — the Story
Director may not conjure an equivalent replacement because the loss was inconvenient.

`irreversible_outcomes` says the universe *permits* permanence. The permanent things
themselves are events and state, not rules.

### Rules enforcement is not difficulty

`rules.enforcement` decides which authority wins when narrative appeal and established
rules disagree:

```text
cinematic      genre logic and dramatic plausibility dominate
flexible       rules matter, but clever plausible exceptions are common
strict         established world rules dominate outcomes   (default)
simulationist  physical and contextual detail is expected to matter heavily
```

It is not a difficulty knob and not a tone knob. A cinematic world can be brutally lethal.

### Content is description, not events

`content.*` steers how graphic the prose is. It never changes an outcome.
`danger.lethality = 100` with `content.gore = none` is a coherent, valid world where
people die constantly and off the page — and there is a test that says so.

### Model randomness is not gameplay randomness

`chance.model` defaults to `seeded`. When mechanical checks arrive they will run through a
dedicated seeded RNG service, so a result is reproducible and auditable.

**`temperature` and token sampling must never resolve a mechanical outcome.** A Story
Director may not decide success by how the sentence wants to continue, and
`chance.narrative_rerolls` is `false` by default and meant to stay that way: a resolved
outcome is never quietly rerolled because it turned out to be inconvenient.

No dice system exists yet. This section is configuration for one, plus a standing
prohibition.

## Versioning

```python
parse_world_rules(raw)   # the only supported way in from storage or a request body
```

Dispatch is an explicit table keyed by `version`, not a migration framework:

```python
_PARSERS: dict[int, Callable[[Mapping[str, Any]], WorldRulesV1]] = {1: _parse_v1}
```

- An unknown version raises `UnsupportedRulesVersionError` naming both what it got and
  what this build supports.
- A malformed document raises `InvalidWorldRulesError` carrying the Pydantic detail.
- **It never falls back to defaults.** A world silently running on rules its author did
  not write is worse than a world that refuses to load. A corrupt row is a 500, and
  `test_a_corrupt_rules_row_fails_loudly_instead_of_defaulting` pins that.

`version` must be a real `int`. `True` is rejected explicitly — `bool` is a subclass of
`int` in Python and would otherwise index straight into the v1 parser.

When V2 arrives it gets its own model and its own entry, plus a `1 -> 2` upgrade if old
documents are worth carrying forward. Building a general migration engine now would mean
inventing requirements for a second version that does not exist.

## Presets

A preset is a **constructor, not a mode**. `build_preset` runs once, at world creation,
and returns a plain `WorldRulesV1`. Nothing downstream stores the name or branches on it —
there is no `if preset is SHONEN` anywhere in the engine and there never should be. Two
worlds whose resolved rules match behave identically regardless of how they were made.

**The preset name is deliberately not persisted.** It would become a lie the moment
someone edited a value, and a preset's numbers may legitimately change between releases;
existing worlds keep the rules they were created with, which is only coherent if the label
was never a live reference.

| Preset | danger | lethality | player armor | optimism | darkness | persistence | pace | NPC autonomy | enforcement |
|---|---|---|---|---|---|---|---|---|---|
| `balanced` | 50 | 40 | 25 | 50 | 50 | 90 | medium | 70 | strict |
| `cozy_fantasy` | 15 | 5 | 70 | 85 | 15 | 50 | slow | 55 | flexible |
| `shonen` | 75 | 30 | 60 | 80 | 40 | 70 | anime_fast | 65 | cinematic |
| `dark_fantasy` | 85 | 90 | 5 | 20 | 90 | 100 | slow | 85 | strict |
| `simulationist` | 55 | 70 | 0 | 50 | 50 | 100 | very_slow | 95 | simulationist |
| `isekai_power_fantasy` | 55 | 25 | 80 | 75 | 30 | 55 | anime_fast | 45 | flexible |

Nine of ~90 values, chosen to show the axes. The numbers are picked to be *coherent with
each other*, not precise — which is why the tests assert relationships (dark fantasy is
deadlier than cozy; simulationist protects the player less than isekai; shonen is
dangerous without being lethal) rather than individual numbers.

`isekai_power_fantasy` is the only preset where resurrection is enabled, which is also the
only combination `death_finality` permits alongside an enabled resurrection block.

There is no "cruelty" setting. Cruelty is an outcome of danger, lethality, consequences
and darkness together, not an authoritative mechanic.

## Coherence validators

Some combinations are contradictions rather than configurations, and are rejected at
construction:

| Rule | Why |
|---|---|
| `death_finality = resurrection_possible` ⟺ `resurrection.enabled` | a world cannot both allow and forbid coming back |
| enabled resurrection cannot have `rarity = impossible` | same contradiction, spelled differently |
| `mundane_ceiling <= superhuman_ceiling <= legendary_ceiling` | inverted tiers are a bug, not a setting |
| `supernatural.enabled = false` ⇒ nothing supernatural exists | otherwise the section contradicts itself |
| `progression.enabled = false` ⇒ breakthroughs off | growth cannot be off and exceptional growth on |

`respawn` and `narrative_checkpoint` are game conventions rather than in-world
resurrection, so they are free to leave the resurrection block disabled.

## Persistence

One `JSON` column, `worlds.rules_json`, not a table per section. Roughly 3 KB per world of
pure static configuration with no independent lifecycle and no query patterns of its own —
a dozen relational tables would buy nothing and cost every read a join. The document is
validated through Pydantic on the way in and on the way out, so the column is never
treated as an arbitrary dictionary.

Migration `5cc072747a2d` adds the column, backfills existing worlds with the balanced
defaults frozen at write time, then sets `NOT NULL` — every world has rules, always. The
backfill literal is deliberately a snapshot: a migration that imported the live model
would rewrite history whenever a default changed.

The demo seed (`app/scripts/seed_demo.py`) creates its world with the `shonen` preset
explicitly rather than relying on the column default, so the seed exercises the same path
the API uses.

## API

```text
POST /api/v1/worlds          rules_preset OR rules, never both
GET  /api/v1/worlds/{id}/rules
```

- Neither field: balanced defaults.
- `rules_preset`: a name from the enum. An unknown name is a 422.
- `rules`: a complete `WorldRulesV1` document, validated at the boundary.
- **Both: 422.** Ambiguity is rejected rather than resolved by a silent precedence rule.
  To start from a preset and adjust it, read the preset's rules and send the modified
  document.

The world list and detail responses do **not** carry rules — ~3 KB per row for a screen
that does not show them. The rules have their own endpoint, fetched once and cached
indefinitely by the client, because they never change after creation.

### No mutation endpoint

There is no `PUT /rules`, deliberately. Changing `content.gore` mid-story is cosmetic;
changing `mortality.death_finality` after someone has died invalidates history. Until
that distinction is modelled, an edit endpoint would be an invitation to corrupt a
save. See [deferred questions](#deferred-questions).

## What the Story Director sees

The full document is ~3 KB of JSON. The prompt budget is a measured constraint — Ollama
runs with `num_ctx = 8192` — so the model gets a projection, not a dump.

```text
WorldRulesV1  --project_world_rules-->  WorldRulesContext  --render_context-->  prose block
```

`application/rules_projection.py` flattens the sections the director can act on into one
AI-facing view; `infrastructure/story/rendering.py` renders it as a `# World rules` block
in plain sentences. The projection is one-way and lossy by design: nothing reconstructs
`WorldRulesV1` from it, and no provider is ever handed the domain object.

Dropped from the projection: `society`, `resources`, the `chance` details beyond model and
rerolls, `power` tier ceilings, and the progression sub-blocks. They configure
deterministic systems that do not exist yet, and every token spent on them is a token not
spent on the transcript.

Measured: the rendered block is **~1.3 KB, roughly 430 tokens** at 3 chars/token, and
varies by under 60 characters across all six presets. Dumping the full document instead
would cost about 3 KB of JSON for strictly less legibility. Prompt sizing overall is a
known Phase 2 concern — see [roadmap.md](roadmap.md#phase-2--narrative-quality); the rules
block is roughly 4% of a full-cap prompt and is not what puts it near the limit.

The ten binding principles — plot armor's ordering, darkness ≠ danger, content ≠ events,
model randomness ≠ gameplay randomness — live in `prompts/story_director.md`, not in the
per-turn block. Principles are constant; only the values change per turn, and paying for
the same paragraph of instructions on every request would be waste.

## Frontend

Minimal by design (this iteration is domain work):

- **World style** picker on the create-world form, sending `rules_preset`.
- A compact read-only summary card on the world screen: danger, lethality, plot armor,
  consequences, progression, world clock, enforcement.

There is no rules editor. Building one before the cosmetic/structural distinction exists
would produce an interface that lets a player break their own save.

## Deferred questions

Open design questions this iteration deliberately did not answer. **None of these are
started.**

1. **Cosmetic vs structural settings.** Which values may be changed after a world has
   been played, and what happens to history when a structural one changes. Blocks any
   edit endpoint or advanced editor.
2. **Partial immutability.** The likely shape is a per-field classification plus a lock
   that engages at the first turn, not a general locking system.
3. **Intensity → probability.** Every mapping from a 0..100 setting to actual odds. Needs
   the resolution layer to exist first, and must never be guessed at inside this document.
4. **Rarity as odds.** Same problem, discrete: `rare` has no number attached on purpose.
5. **World-level vs session-level overrides.** Whether a single save may deviate from its
   world's rules.

## Related future systems

| System | Relationship |
|---|---|
| **WorldState** | reads rules to decide what may exist and what persists; owns everything that changes |
| **CharacterSheet** | reads `power` and `progression` for ceilings and pace; owns actual attributes |
| **PowerSystem** | reads `supernatural` and `power` for what may exist; owns abilities, costs, resources |
| **Rules resolution / dice** | reads `chance`, `danger`, `power` — the layer that finally turns intensities into outcomes, through a seeded RNG the model never touches |
| **World simulation** | reads `simulation`; owns offscreen events and the clock |

Each of those is a separate epic. `simulation.*` in particular is configuration for an
engine that does not exist: nothing in this release schedules an offscreen event or
advances a clock.
