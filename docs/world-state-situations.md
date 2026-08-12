# WorldState: situations

What the world is currently *doing*. A siege, a fire, a festival, an investigation, a
reconstruction — a process that started, is going somewhere, and will one day end.

This is the fourth piece of `WorldState`, after time, facts and locations.

---

## The distinction this system exists to keep

```
WorldFact       what is objectively true now
Situation       what ongoing process exists now
GameEvent       what happened
ScheduledEvent  what is expected to be processed later
```

Worked through one night in Asterfall:

```
Situation:       Siege of Asterfall
GameEvent:       Eastern gate breached
LocationState:   eastern gate = destroyed
ScheduledEvent:  evaluate the siege again in six hours
```

Four models, four questions. The siege is not the breach; the breach is not the ruined
gate; the ruined gate is not the next evaluation. A system that collapses any pair
loses one of the answers, and the one it usually loses is *why*.

---

## Objective, not known

A `Situation` is world state. It is not player knowledge, NPC knowledge, rumour, memory
or something the narrator said. A conspiracy exists whether or not anyone has noticed
it, which is exactly why there is no `known_by_player` field and must not be one.

Who knows what is `KnowledgeState`'s question, and `KnowledgeState` does not exist yet.
See [Hidden situations](#hidden-situations-and-the-gap-that-is-not-closed) for what that
currently costs.

---

## The model

```yaml
Situation:
  id, session_id

  category: SituationCategory     # conflict, hazard, social, political, ...
  subtype: string | null          # siege, fire, festival, bridge_reconstruction

  title, description
  status: SituationStatus         # planned, active, dormant, resolved, cancelled

  intensity: 0..100               # how strongly it is manifesting
  threat: 0..100                  # how dangerous it currently is
  momentum: -100..100             # which way it is going
  importance: 1..5                # how much attention it deserves

  scope: SituationScope           # local, regional, global, entity_specific
  primary_location_id: UUID | null
  parent_situation_id: UUID | null

  started_at, last_progressed_at, resolved_at   # session elapsed_minutes
  source_event_id: UUID | null

  situation_metadata: {}          # small, flat, scalar
  tags: []
```

Everything temporal is **fictional** time from
[Time V1](world-state-time.md) — session `elapsed_minutes`. `created_at` and
`updated_at` exist, are wall-clock, and carry no simulation meaning whatsoever. Nothing
reads a real clock to decide how long a siege has lasted.

### Category and subtype

`category` is a closed enum of ten. `subtype` is an open, normalised identifier.

```yaml
category: conflict       subtype: siege
category: hazard         subtype: fire
category: social         subtype: festival
category: project        subtype: bridge_reconstruction
category: investigation  subtype: murder_investigation
```

The enum is closed because the engine branches on it — cadence, and eventually
resolvers. The subtype is open because `conflict` is not a thing that happens to
anyone, and no enum survives contact with fiction across genres. Its *shape* is
constrained (lowercase, underscores) so `Siege`, `siege ` and `siege` are one thing.

### Three numbers, because one would be a lie

The single-`severity` design fails on the first festival.

| | intensity | threat |
|---|---|---|
| City-wide festival | 90 | 5 |
| Siege | 80 | 90 |
| Investigation closing in | 70 | 20 |
| Vast distant storm | 100 | 60 |

**Intensity** is how strongly the process is manifesting. Deliberately neutral: 90 is a
raging fire and also a packed street.

**Threat** is how dangerous it currently is. Independent of intensity, and *not* a
probability — `threat = 80` does not mean an 80% chance of anything. It is a domain
measure that steers priority and context; outcomes belong to resolution logic.

**Momentum** is direction and speed. Negative is shrinking, zero is stable, positive is
growing. **Growing is not worsening.** `+50` on a fire is spreading; `+50` on a
reconstruction is work accelerating. Any code that reads positive momentum as bad news
has imported a tone this model does not have.

**Importance** is independent of all three: how much prompt budget and simulation
attention this deserves. A vast distant storm is `intensity 100, importance 1`; a small
investigation pointed at the player is `intensity 30, importance 5`.

### Positive situations are half of what this is for

Festivals, economic booms, reconstructions, research projects, political campaigns,
peace negotiations, tournaments, migrations, public celebrations. None of these is a
special case of a threat model — they are ordinary uses of this one.

---

## Lifecycle

```
planned ──> active ──> resolved
   │          ↕
   │       dormant ──> resolved
   │
   └──> cancelled          (active and dormant may also be cancelled)
```

| status | meaning |
|---|---|
| `planned` | prepared or expected, not yet begun |
| `active` | progressing, or able to |
| `dormant` | still real, currently going nowhere |
| `resolved` | reached a conclusion |
| `cancelled` | prevented or abandoned before it could conclude |

**Dormant is not a pause button.** `active ↔ dormant` goes both ways, because a cold war
warms and a stalled inquiry gets a new witness. That round trip is why `dormant` exists
rather than being spelled `active` with `momentum = 0`: a process that is going nowhere
is different from one that is going nowhere *right now*, and only the second wakes up
when something happens.

**Terminal means terminal.** Resolved and cancelled have no outgoing edges. A siege that
ended did not un-end, and a world where it can is one where the transcript and the state
disagree about the same week. Re-opening is refused with a message that says what to do
instead: start a new situation with this one as its parent.

**Concluded rows stay.** Nothing deletes a situation for reaching a terminal status.
That is why the entity is `Situation` and not `ActiveSituation` — "what is going on
right now" is a *query*, and a table of only live rows would have nowhere to put the
siege that ended last week, which is most of what makes a session worth reading
afterwards.

---

## Participants

```yaml
SituationParticipant:
  situation_id, entity_type (character | faction | other), entity_id, role
```

A reference and a role, nothing more. Nothing here describes what a participant *can
do* — no soldiers, no money, no influence, no health. Those belong to whatever domain
owns the entity, and a copy here would be a second answer that drifts from the first.

`role` is open and normalised: `attacker`, `defender`, `investigator`, `target`,
`organizer`, `beneficiary`, `opponent`. One entity may hold two roles in one situation —
being both `investigator` and `target` is a good story — so the uniqueness key is the
pair, not the entity.

Normalised into its own table rather than a JSON array on `situations`, because the
query that makes it worth having is the reverse one: *every situation involving this
character*. That is what StoryContext selection uses and what future simulation
prioritisation will lean on hardest.

Characters are resolved against the world. **Factions have no table yet**, so their ids
are accepted on trust — recorded here rather than quietly tolerated.

---

## Parent situations

```
War
└── Siege of Asterfall
    └── Food Crisis
        └── Disease Outbreak
```

Causal and organisational only. It says the food crisis exists *because of* the siege
and should be read alongside it. That is all it says.

**Lifecycle is not inherited.** Resolving the war does not resolve the siege, and
lifting the siege does not end the hunger — a city does not stop starving because a
treaty was signed somewhere else. The grain has to arrive, and whether it does is a
separate process with its own resolution. Application logic may of course decide,
explicitly, that resolving one thing resolves another; that decision belongs to whatever
knows why, not to this link.

Cycles are prevented in `world_situations.hierarchy.check_parent_situation`. Only the
single-node case (`parent = self`) is expressible as a CHECK constraint in SQLite or
PostgreSQL; the rest runs before every write that sets the link.

A generic relation graph (`caused_by`, `exacerbated_by`, `mitigated_by`, `related_to`) is
documented as future work and deliberately not built. `parent_situation_id` and
`source_event_id` are enough for V1.

---

## Provenance

`source_event_id` explains how a situation began:

```
KING_ASSASSINATED  →  Succession Crisis
FIRE_STARTED       →  Tavern Fire
```

Gameplay-created situations should normally carry one. Two documented exceptions:

* **seed** — a world's starting processes. Nothing *happened* to start them; the world
  began that way.
* **admin / world authoring** — out-of-band by nature.

---

## Metadata and tags

`situation_metadata` is a small, flat, scalar-only bag for subtype-specific detail that
has not earned a model yet: an investigation's stage, a ritual's completion. Bounded and
validated at the boundary, no nesting, no executable content.

It is **not** a general escape hatch. When a subtype's data outgrows a handful of
scalars, the answer is a typed model and a specialised resolver, not a deeper dict. And
it is never a place to keep state another domain owns — see
[Situation is not the world it changes](#situation-is-not-the-world-it-changes).

`tags` (`military`, `political`, `urgent`, `public`, `secret`) support retrieval and
filtering. **A tag never replaces a field.** `dangerous` is not a substitute for
`threat`, because nothing can compare two tags.

---

## Progression

```
Situation + elapsed interval + WorldRules + participants + places + RNG
                             ↓
                 SituationProgressionService
                             ↓
                 SituationProgressionResult
```

### There is no tick

Nothing loops over minutes, and nothing may:

```python
for every minute:          # <- never
    for every situation:
        progress()
```

Different processes operate at different temporal scales:

| kind | cadence |
|---|---|
| fire | minutes |
| siege | hours |
| investigation | a day |
| war, politics, economy | days |
| construction | weeks |

One cadence for all of them would be wrong for most of them, and the cheap wrong answer
— evaluate everything every minute — costs the most for the processes that need it
least. Progression is asked for over an **interval**, and how often that happens is
decided by scheduling, by events, or by a person. Never by the clock passing.

There is also no `tick_interval_minutes` on `Situation`. A process that only moves when
something happens to it should have nothing to schedule.

### The request

```yaml
SituationProgressionRequest:
  situation_id
  from_time, to_time    # absolute positions on the session clock
  trigger: scheduled | event | explicit | simulation
```

Absolute, not a duration, for the same reason `ScheduledEvent.due_at` is: a request
saying "six hours" would mean something different depending on when it was read. A
backward interval is refused; a zero-length one is legitimate, because an event-triggered
evaluation happens at an instant.

### The result

```yaml
SituationProgressionResult:
  deltas: {intensity, threat, momentum}
  status_change, resolution_reason
  generated_events: [GameEvent descriptors]
  state_mutations: [StateMutation]        # the existing typed union
  new_situations: [StartSituation]
  next_progression_at: int | null
```

Nothing in it has happened yet. `state_mutations` uses the **existing** mutation union —
there is no second mutation concept for situations, and a resolver that wants to change
a location says so with `UpdateLocationState` like every other caller. There is
deliberately no escape hatch for "run this SQL", "call this service" or "store this
script".

Deltas rather than values, so a resolver never has to have read the current state
correctly: the authoritative value is read inside the transaction and the delta applied
to it, then clamped.

### The resolver boundary

```
(category, subtype) -> a registered resolver
(category, None)    -> a registered resolver for the whole category
otherwise           -> GenericProgressionResolver
```

A lookup table, not an `if subtype == ...` chain. `FireSituationResolver`,
`SiegeSituationResolver` and `InvestigationResolver` are all future work; when they
arrive they register themselves and nothing else changes.

**`GenericProgressionResolver` is a placeholder and is labelled one everywhere it
appears.** It knows exactly one thing: a process with momentum drifts in the direction of
its momentum, and momentum decays unless something sustains it. It does not know that
fire spreads through timber, that sieges starve cities, that investigations need
witnesses or that reconstruction needs materials. What it does provide is a real exercise
of the boundary — the interval arithmetic, the clamping, the WorldRules read, the
dormancy transition and the next-evaluation scheduling all run through it.

It is deliberately symmetric. Negative momentum shrinks intensity exactly as positive
momentum grows it, so a fire brigade containing a blaze and a festival winding down are
the same arithmetic with a different sign.

### Randomness

Uncertain progression must come from a dedicated **seeded game RNG** so a session
replays. This project has none, so nothing here is stochastic — same inputs, same answer,
every time. `random`, wall-clock time and model sampling temperature are all
disqualified: the first two make a replay diverge, and the third makes token sampling the
arbiter of whether a city starves.

---

## WorldRules integration

Progression reads **resolved rule values**, never preset names:

```python
if preset == "dark_fantasy":   # <- never
```

Currently consumed:

* `simulation.world_continues_without_player` — false means the world waits. Honoured as
  a refusal to move, not as a smaller movement: "the world does not act on its own" is
  not a speed setting.
* `danger.escalation_rate` — scales drift. A world at 80 moves twice as fast as one at
  the balanced 40; one at 10 moves at a quarter the speed.

Exposed for the future SimulationEngine: `simulation.simulation_scope`,
`simulation.npc_autonomy`, `simulation.faction_autonomy`, `consequences.persistence`,
`rules.enforcement`, `resources.scarcity`. Situations carry `importance`, `scope`,
`primary_location_id` and participants precisely so that prioritisation can later be
deterministic.

### Off-screen progression

If `world_continues_without_player` is true, relevant situations may progress while the
player's character is elsewhere **in fictional time**. Leave a city with an epidemic at
intensity 30, return ten fictional days later, and it may be at 70 — or resolved.

This is emphatically **not** wall-clock progression while the app is closed. Only
fictional `TimeState` advancement matters. Nothing runs in the background; there is no
worker, no queue and no cron.

### The world can solve its own problems

Unattended situations do not always get worse. NPC fire brigades contain fires, doctors
control epidemics, peace negotiations succeed, reconstruction finishes. A model in which
the world only ever deteriorates without the protagonist is one where the protagonist is
the only thing that ever helps — the opposite of what world autonomy is for.

They can also worsen, when the rules and the state support it: sieges breach walls,
famines deepen, crises escalate. Content does not freeze waiting for the player unless
the world's rules require it, and missed opportunities are real — a summit the player
ignores happens anyway.

---

## StateMutation integration

```
StateMutation
├── SetFact
├── RemoveFact
├── UpdateLocationState
├── UpdateConnectionState
├── StartSituation
├── UpdateSituation
└── ResolveSituation
```

Situation lifecycle changes are **typed mutations**, never generic `SetFact`.
`SetFact(world.siege_status = "ongoing")` would be a string in a JSON column; a situation
has a lifecycle, three bounded numbers, participants, a parent, a location and two
timestamps, and every one of those is something a query will need.

| mutation | shape |
|---|---|
| `StartSituation` | no `id` field — the application mints it |
| `UpdateSituation` | deltas only; terminal statuses refused |
| `ResolveSituation` | sets `resolved_at`; `reason` required |

`UpdateSituation` and `ResolveSituation` share a target key, so one batch cannot both
nudge a siege and lift it — a caller that sends both has not decided what happened.

### A batch cannot nest a situation inside one it just started

`parent_situation_id` on a `StartSituation` may only name a situation that **existed
before the batch began**. Naming one that does not is a `NotFoundError`, refused during
validation, before anything is written.

This is a consequence of `StartSituation` carrying no `id`: the id is minted at write
time, deliberately, so that nothing outside `state_service` chooses situation identity.
A batch therefore has no vocabulary for referring to one of its own results — "the war
two mutations ago" is not something that can be written down — and for a while the
docs claimed the opposite.

A caller that wants a tree writes the root, commits, and starts the children against the
id it got back. The alternative — local aliases a later mutation could resolve — is a
mutation scripting language, and no use case has asked for one. Batches stay flat lists
of independent changes.

### Atomicity

A siege progressing:

```
intensity +12                UpdateSituation
EAST_GATE_BREACHED           GameEvent
gate condition = destroyed   UpdateLocationState
crossing impassable          UpdateConnectionState
food crisis begins           StartSituation
```

Five changes across three domains, one decision. They go into **one**
`StateMutationBatch`, are validated to completion before the first write, and share
**one** `state_revision` bump. A failure anywhere refuses the whole thing and the
revision does not move. There is no version of that outcome where the gate falls and the
crisis does not.

### Situation is not the world it changes

A reconstruction project finishing does **not** mean the bridge's condition lives in
`situation_metadata`:

```yaml
# Wrong
Situation metadata:
  bridge_condition: repaired
# while LocationConnectionState says: destroyed
```

The project produces a `BRIDGE_REPAIRED` event and an `UpdateConnectionState`. Spatial
state stays the one place that knows whether the bridge stands. Use `Situation` to track
the ongoing *process*; use dedicated domains to track the resulting *state*.

---

## Scheduled progression

A resolver may ask for the next evaluation at an absolute session minute, and a real
`ScheduledEvent` of type `situation.progress` is written for it:

```
fire          next evaluation = +15 min
siege         next evaluation = +6 h
investigation next evaluation = +1 day
```

Absolute, never `"in six hours"`.

> **Nothing dispatches that event automatically yet, and it is no longer consumed
> silently.** `advance_time` marks it `DUE` and leaves it there — see
> [DUE is not PROCESSED](world-state-time.md#due-is-not-processed). Time owns chronology
> and cannot progress a situation, so a `situation.progress` event stays owed until
> something executes it. `GET /api/v1/dev/sessions/{id}/scheduled-events/due` lists what
> is owed, and `POST /api/v1/dev/sessions/{id}/situations/{sid}/progress` is the one
> dispatcher that exists: it runs the progression and acknowledges the event in the same
> transaction. A due list that keeps growing is the visible backlog that used to be
> invisible, and the World Simulation Scheduler that will drain it is out of scope here.

---

## Story Director authority

The model may **propose**. It may not change anything.

`SituationProposal` carries `category`, `subtype`, `title`, `description`, `scope`,
`primary_location_id` and `reason`. Notice what is absent: **no `intensity`, no `threat`,
no `momentum`, no `importance`, no `status`, and no way to name an existing situation.**

A location the story mentions is a noun. A situation is a process with three bounded
numbers, a lifecycle and a claim on future simulation — and a model that could set those
could declare a war at intensity 100 by writing an atmospheric sentence, or end a siege
because the scene felt like it should be over.

So the model says *what kind of thing began*, and the application decides every number.
A narrated situation starts at a fixed, deliberately modest opening state (intensity 20,
threat 0, momentum 20, importance 2). If a process deserves to start large, a game system
starts it large — one with a reason beyond having just written the word "inferno".

The alternative considered and rejected: let the model propose numbers and clamp them.
Clamping `intensity: 100` to 40 still lets a sentence decide that a fire is severe, and
"the model may not alter intensity" stops meaning anything the moment it may suggest one.

Existing situations are read-only to the director in the strongest available sense:
`TurnGeneration` has **no field** that could address one. `UpdateSituation` requires
mechanical authority, which `story_director` does not have, and a
`StateMutationBatch(authority=story_director, ...)` carrying a situation mutation cannot
be constructed at all.

---

## StoryContext

Relevant situations reach the prompt, never all of them. The selector is deterministic
and bounded, in priority bands:

1. **here** — centred on the scene's location, *or on a place containing it*. A siege of
   the city is happening in the tavern.
2. **involving** — a character in the scene is a participant.
3. **regional** — important enough (≥3) that the region would be talking about it.
4. **global** — world-scale and critical (≥4).

Anything in none of those bands is **omitted entirely**, not ranked low: a minor local
process three regions away is irrelevant, and giving it a rank would let it displace
something that matters on a quiet turn.

Within a band: importance, threat, most recently progressed, title. Total ordering, so
two reads of an unchanged session agree — a list that reshuffles between turns is a
prompt that will not cache.

Concluded situations are omitted by default. At most six reach the prompt. What the
director sees:

```
# What is going on  (authoritative: you may narrate these, never change them)
- The failing wards (ward failure, local) — active, intensity 60/100, danger 55/100, growing, running 6 hours
- The contested succession (succession crisis, regional) — dormant, intensity 30/100, danger 40/100, steady, running 6 hours
```

Duration is rendered for reading; the authoritative minute counts stay in the database,
where arithmetic belongs.

### Hidden situations, and the gap that is not closed

A conspiracy exists objectively whether or not anyone has noticed it. With no
`KnowledgeState`, this system cannot ask "does the player know?", so the honest options
were to send every live situation and hope, or to hold back the ones a world has
explicitly marked secret. **It does the second: a `secret` tag keeps a situation out of
player-facing context.**

That is a *convention*, not a mechanism, and should be read as one. It protects against
the obvious leak — an assassination plot narrated into the open the turn it begins — and
protects against nothing else. Real perception, discovery and per-character knowledge are
`KnowledgeState`'s, and until it exists the limitation lives in
`app/application/situation_context.py`.

---

## World templates

A world may declare processes it already has under way, the same way it declares starting
facts:

```json
POST /api/v1/worlds
{
  "name": "...",
  "initial_situations": [
    {"category": "hazard", "subtype": "ward_failure", "title": "The failing wards",
     "intensity": 45, "threat": 55, "momentum": 15, "importance": 4, "scope": "local"}
  ]
}
```

Stored as `StartSituation` documents rather than situation rows, because a situation
belongs to a *session* and this is a template. Each session materialises its own copies
with their own ids and diverges immediately — lifting the siege in one save leaves the
template, and every other save, untouched. Nothing writes back to the world.

---

## HTTP surface

```
GET  /api/v1/sessions/{id}/situations
       ?status= &live_only= &category= &scope= &location_id=
       &participant_id= &participant_type=
GET  /api/v1/sessions/{id}/situations/{situation_id}
```

Session-scoped without exception — unlike geography there is no template tier to read,
and an endpoint with no session in its path could not return one without choosing a save
on the caller's behalf. A situation belonging to another session is a 404, which is
correct: from here, "exists elsewhere" and "does not exist" are the same thing.

`live_only` defaults to true. An explicit `status` overrides it.

**There is no POST, PATCH or DELETE.** A REST endpoint that set `intensity = 90` would
have no event explaining it, would not move the state revision, and would bypass the
transition rules — the exact hole the mutation boundary exists to close. This is asserted
by a test that inspects the OpenAPI document, so a write endpoint appearing here is a
failing build.

### Developer-only

```
POST /api/v1/dev/sessions/{id}/situations/{situation_id}/progress
```

Off unless `dev_endpoints_enabled`. Runs one situation's progression from where it was
last evaluated **to where the session clock actually is** — the interval is not the
caller's to choose, so a progression can only ever account for fictional time the session
has lived through. To exercise a long interval, advance the clock first:

```bash
curl -X POST .../dev/sessions/$S/advance-time -d '{"requested_minutes":360,"reason":"debug"}'
curl -X POST .../dev/sessions/$S/situations/$I/progress -d '{}'
```

Nothing about it is a shortcut: it goes through the same `evaluate_and_apply` a future
SimulationEngine will, so a paused world still refuses to move, an invalid transition is
still refused, and every value is still clamped.

---

## Frontend

An inert four-line panel on the session screen: title, status, intensity, direction. No
control, no editor, nothing to click — situations move when a game system moves them, and
neither narration nor a UI panel is one. The same decision the fictional clock makes.

Explicitly not built: strategy UI, world-simulation dashboard, war map, progress editor,
quest tracker.

---

## Deliberately not implemented

A complete world simulation engine. Fire spread, warfare, epidemiology, politics or
economics as simulations. Weather. `FactionState`. `CharacterState`. NPC autonomy.
Participant resource simulation. A quest system. A game RNG. A `SituationRelation` graph.
`KnowledgeState`. Background wall-clock processing, worker queues, cron.

This task built the Situation domain and one generic progression boundary. Everything
above is what plugs into it.

---

## Related

* [Simulation time](world-state-time.md) — the clock every temporal field here uses
* [World facts](world-state-facts.md) — objective truth, authority, the mutation batch
* [Locations](world-state-locations.md) — where a situation is centred
* [World rules](world-rules.md) — what progression consumes
* [AI contract](ai-contract.md) — the proposal boundary
