# Epic 001 — WorldRules V1

Task breakdown derived from the WorldRules V1 specification, plus the design
decisions and open questions found while analysing it against the existing codebase.

**Status:** paused after the first slice. Nothing below is guesswork about the current
code — every file path was read before being listed.

---

## 0. Where things stand

| | |
|---|---|
| Branch | `epic-001_world-rules` |
| Base | fast-forwarded to `master` (`81b8908`), so the bug bash is included |
| Done | `apps/api/app/domain/world_rules/enums.py` — every closed vocabulary (commit `28d60ac`) |
| Remaining | sections 1–8 below |

The branch had originally been cut from the initial commit and was missing the bug
bash. That mattered specifically here: WorldRules adds content to the Story Director
prompt, and without the `num_ctx` fix that prompt was being truncated from the head —
exactly where the rules would land. Fast-forwarding was a no-conflict operation
because the branch had no commits of its own.

---

## 1. Domain model

**Package:** `apps/api/app/domain/world_rules/`

- [x] `enums.py` — `Rarity`, `DeathFinality`, `IncapacitationPolicy`, `PowerScale`,
      `PowerGapSignificance`, `PublicAwareness`, `Regulation`, `Discoverability`,
      `ProgressionPace`, `ProgressionCeiling`, `TrainingRequirement`,
      `InformationSpread`, `RecoverySpeed`, `SimulationScope`, `TimeProgression`,
      `ChanceModel`, `ChanceTransparency`, `RulesEnforcement`, `ContentIntensity`,
      `RomancePolicy`, `SexualContentPolicy`, `SubstanceUsePolicy`
- [ ] `rules.py` — a constrained `Setting = Annotated[int, Field(ge=0, le=100)]` plus
      the thirteen section models and `WorldRulesV1`
- [ ] `presets.py` — six preset builders
- [ ] `parsing.py` — `parse_world_rules(raw: dict) -> WorldRulesV1`, explicit version dispatch

### Sections

| Section | Model | Notes |
|---|---|---|
| `narrative` | `NarrativeRules` | `optimism` and `darkness` are **independent axes** — never `darkness = 100 - optimism`. A world can be bleak and ultimately hopeful. |
| `narrative.plot_armor` | `PlotArmorRules` | player / important_npcs / ordinary_npcs |
| `mortality` | `MortalityRules` | includes nested `ResurrectionRules` |
| `danger` | `DangerRules` | frequency / severity / lethality / escalation are four separate dimensions |
| `consequences` | `ConsequenceRules` | |
| `power` | `PowerRules` | includes `RuleBreakingRules`, `HiddenPowerRules`, `AwakeningRules` |
| `supernatural` | `SupernaturalRules` | high-level constraints only, **no magic system** |
| `progression` | `ProgressionRules` | includes `BreakthroughRules`, power loss, regression |
| `society` | `SocietyRules` | systemic tendencies only, no concrete factions or laws |
| `resources` | `ResourceRules` | nested `ScarcityRules` per resource kind |
| `simulation` | `SimulationRules` | configuration only, **no simulation engine** |
| `chance` | `ChanceRules` | |
| `rules` | `RulesEnforcementRules` | |
| `content` | `ContentRules` | narration intensity, separate from danger |

### Semantics that must be documented in code, not just in docs

These are the ones that are easy to get wrong later and expensive to unwind:

1. **0..100 is world configuration/intensity, not probability.** Do not let a future
   resolution layer read these as odds without an explicit mapping.
2. **Plot armor acts before an outcome, never after.** It may make a plausible escape
   *exist*; it may not alter a resolved roll, undo damage, resurrect anyone, invalidate
   a `GameEvent`, or rewrite history. This is the single most abusable knob in the spec.
3. **`danger.lethality` and `content.gore` are orthogonal.** `lethality=100, gore=none`
   must be a valid, coherent world.
4. **`darkness` is not `danger`.** Moral bleakness versus physical risk.
5. **`protagonist_centrality` is not plot armor.** Events gravitating toward the player
   is not the player surviving them.
6. **LLM sampling is not gameplay randomness.** `temperature` must never be the
   authoritative resolution mechanism. `ChanceModel.SEEDED` is the default and a future
   dedicated RNG service owns it.
7. **`rule_breaking` is not "the model may ignore rules when convenient."** When one
   eventually fires it must become a persisted `GameEvent`.

---

## 2. Presets

`WorldRulesPreset` returning plain `WorldRulesV1` values. **No engine code may branch
on a preset name** — after creation only the resolved rules exist.

- [ ] `balanced()` — general-purpose default
- [ ] `cozy_fantasy()` — low danger and lethality, high optimism, substantial protection
- [ ] `shonen()` — high danger, comparatively low lethality, fast progression, high centrality
- [ ] `dark_fantasy()` — high danger and lethality, persistent consequences, low plot armor
- [ ] `simulationist()` — minimal protagonist privilege, high consequence persistence, autonomous world
- [ ] `isekai_power_fantasy()` — high centrality, high protection, fast progression

Tests assert **relative ordering**, not exact numbers:
`dark_fantasy.danger > cozy.danger`, `simulationist.plot_armor.player < isekai.plot_armor.player`,
`shonen.lethality < dark_fantasy.lethality`.

---

## 3. Persistence

- [ ] `worlds.rules_json` JSON column — chosen over a dedicated table. It is static
      configuration; a dozen relational tables would buy nothing and cost joins.
      Validation happens at the boundary through Pydantic, so it is never an
      unvalidated dictionary in practice.
- [ ] Alembic migration, `render_as_batch=True` (already configured) for SQLite:
      add nullable → backfill every existing row with the balanced defaults → alter to
      `NOT NULL`. The default JSON is embedded as a **literal** in the migration rather
      than imported from the model, so the migration keeps producing what it produced
      the day it was written.
- [ ] `seed_demo.py` writes rules for *The Fractured Crown*
- [ ] World creation without rules receives valid defaults

Existing local database has 11 worlds that need backfilling.

---

## 4. API

- [ ] `WorldCreate` accepts **`rules_preset` XOR `rules`** — supplying both is a 422,
      not a silent precedence rule. Ambiguous requests get rejected.
- [ ] `GET /api/v1/worlds/{id}/rules` → full `WorldRulesV1`
- [ ] `WorldRead` stays lean — no rules payload on the list endpoint
- [ ] **`PUT .../rules` deliberately omitted.** See open question Q1.

---

## 5. Story Director integration

- [ ] `WorldRulesContext` — a narrow projection in `application/story_context.py`,
      not the whole document. Persistence internals never reach the model.
- [ ] Mapper from `WorldRulesV1` → `WorldRulesContext` (application layer imports
      domain; the direction `api → application → domain` holds)
- [ ] `rendering.py` renders it as compact prose lines, not raw JSON
- [ ] Include: protagonist centrality, plot armor, mortality, danger, consequence
      persistence, power gap significance, supernatural prevalence, progression tone,
      NPC autonomy, rule enforcement, content settings
- [ ] `story_director.md` gains the ten authority principles; bump `version: 1` → `2`

**Watch the prompt budget.** Measured before this epic: a full context at the current
retrieval caps is ~6.7k tokens against `OLLAMA_NUM_CTX=8192`. The rules block must be
compact, and the measurement should be re-run afterwards — this is the first change
that grows every single prompt.

---

## 6. Frontend

- [ ] `types.ts` — `WorldRules` and preset name types
- [ ] `CreateWorldForm.tsx` — "World Style / Rules Preset" select
- [ ] `WorldPage.tsx` — compact summary card (Danger / Lethality / Plot Armor /
      Consequences / Progression / Simulation)
- [ ] `messages.ts` — en + es strings for every new label
- [ ] Pure `summarizeRules()` helper with its own tests; the 0..100 → Low/Moderate/High
      bucketing is presentation and belongs here

No advanced rules editor in this iteration.

---

## 7. Tests

- [ ] **Validation** — out-of-range numerics rejected, invalid enums rejected, unknown
      version rejected with a clear message, defaults valid
- [ ] **Presets** — all six valid; relative-ordering assertions only
- [ ] **Persistence** — create with defaults / preset / custom rules, reload, rules equivalent
- [ ] **API** — serialization round-trip, both-fields-supplied rejected
- [ ] **StoryContext** — validated rules actually arrive
- [ ] **Frontend** — summary helper

---

## 8. Docs and verification

- [ ] `docs/world-rules.md` — purpose, WorldState distinction, every section, scale
      semantics, presets, plot armor, danger vs lethality, optimism vs darkness,
      consequences, enforcement, the RNG principle, future partial immutability,
      future relationship to WorldState / CharacterSheet / PowerSystem
- [ ] Touch `docs/architecture.md`, `docs/ai-contract.md`, `docs/roadmap.md`, `README.md`
      only where actually needed

**Verification:** `ruff check`, `ruff format --check`, `pytest`, migration up, frontend
lint + test + build, then a live run: create a world by preset → reload → start a
session → execute a mock turn → confirm the existing vertical slice still works.

---

## Open questions

Recorded rather than guessed at.

**Q1 — Mutating rules after a session begins.** The spec allows omitting `PUT`, and
this plan does. Some changes are harmless (`content.romance`), others rewrite physics
mid-save (`death_finality`, `resurrection`, power ceilings). Needs a split between
cosmetic and structural fields plus a decision about sessions already in flight.
Building the endpoint before deciding would ship the ambiguity.

**Q2 — Do rules apply to a session or to a world?** Currently world-scoped. If rules
ever become mutable, an in-flight session arguably needs the snapshot it started under,
which points at copying rules onto `GameSession` at creation. Not decided.

**Q3 — Player/NPC mechanical symmetry.** Plot armor is explicitly asymmetric. Whether
NPC-facing systems later read the same knobs is unresolved.

**Q4 — Rarity → probability.** Deliberately unmapped. The mapping belongs to the
resolution layer and will need context (who, where, under what pressure).

**Q5 — Preset drift.** If a preset's numbers change in a later release, existing worlds
keep their stored resolved rules. That is intended, but it means a preset name is a
creation-time label only and must never be treated as a live reference.

---

## Non-goals

Not in this epic, per the specification: WorldState, CharacterSheet, attributes, skills,
XP, levels, combat, inventory, spell systems, magic resources, dice checks, factions,
reputation mechanics, autonomous NPC simulation, economy, weather, calendar, quests,
character knowledge systems.

---

## Carried over from the bug bash

Still open, unrelated to WorldRules but worth not losing:

- **Secrets leak into narration and memories.** Reproduced in both worlds tested with
  `mistral:7b`. Irreducibly a prompt problem, and it wants a way to compare prompt
  revisions across scenarios before it is worth attempting.
- **No prompt bench exists.** The measurement scripts used during the bug bash live in
  a scratchpad outside the repo. Making them a permanent tool is a prerequisite for any
  serious prompt iteration, including section 5 above.
