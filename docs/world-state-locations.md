# WorldState: locations and spatial state

A story happens somewhere. Not "in a fantasy city" — in a specific tavern, inside a
specific district, with a specific door onto the street that may or may not still open.

Without a spatial model, "where" lives only in prose. The model walks the player through
a wall it invented last paragraph, has someone arrive from a district that has never
existed before, and teleports a character across a country because the sentence needed
them there. None of it is detectable, because there is nothing to detect it against.

This is the third piece of `WorldState`, after [the clock](world-state-time.md) and
[facts](world-state-facts.md). It ships persistent places, how they contain and connect
to one another, and what is currently true about each of them in one save. It ships no
travel, no scenes, no perception and no tactical space — those all need somewhere to
stand, so this goes in first.

## The distinctions this exists to keep

| | what it is | lifetime |
|---|---|---|
| **LocationDefinition** | what and where a place structurally is | the world's |
| **LocationState** | what is currently true about it here | one session's |
| **LocationConnection** | a declared traversal between two places | the world's |
| **LocationConnectionState** | whether that crossing works right now | one session's |
| **LocationZone** | a named area *inside* a place | the world's |
| **SpatialPresence** | where an entity is, exactly | future |
| **SceneState** | the immediate, ephemeral context | future |
| **EncounterSpace** | tactical positions during a fight | future |
| **PlayerKnowledge** | which of this the player has found | future |

Definitions answer *what and where is this place?* State answers *what is true about it
in this save?* Everything in the second half of that table is deliberately absent; see
[Deliberately not built](#deliberately-not-built).

## Containment, connectivity, proximity

The three ideas most easily confused, and the confusion is expensive:

```text
containment    what is inside what        parent_location_id
connectivity   what can be traversed      LocationConnection
proximity      what is near what, in a scene   — not modelled
```

**None implies another.** The Broken Crown being inside Riverwood does not mean there is
a usable way from the town into the tavern — that is the door, and the door is a row
somebody wrote. A cellar whose only stair collapsed is still inside the tavern and no
longer reachable from it. Two characters standing in the same city are not near each
other, and nothing here will ever say they are.

The last one is why there is no `nearby` list anywhere in this system. A model handed
one would write as though the people on it were within arm's reach. Proximity is a
scene-level question, and it belongs to `SceneState`.

## Definitions

```yaml
LocationDefinition:
  world_id:            which world
  origin_session_id:   null for template, a session id for generated canon
  name, description
  category:            world | region | settlement | area | structure | interior | transit | other
  subtype:             the genre-specific noun — tavern, orbital_station, enchanted_forest
  scale:               world | continental | regional | settlement | district | site | building | room | point
  parent_location_id:  what contains it
  importance:          1..5, for context selection only
  tags, spatial_metadata
```

### Category is not scale

Two axes, and neither is derivable from the other:

```yaml
Royal Palace:    category: structure, subtype: royal_palace, scale: building
Palace Gardens:  category: area,      subtype: palace_gardens, scale: site
```

A cupboard and a cathedral are both `structure`. Category says what kind of thing it is;
scale says how much space it takes. The enum for the first stays small because `subtype`
is a free string — that is how one closed vocabulary survives every genre this ever
runs, instead of growing a member per fictional noun.

Subtypes are normalised in shape but not in meaning: `Tavern`, `orbital station` and
`enchanted-forest` become `tavern`, `orbital_station` and `enchanted_forest`. The engine
groups and filters on them, so `Tavern` and `tavern` must not be two things.

### Scale is qualitative, and metric detail is never required

This is a narrative engine, not a GIS. "Building" is what a scene needs; inventing
`width = 87.4m` for a tavern is precision no system consumes. A world that genuinely
measures things can put them in `spatial_metadata`, which is a small flat bag nothing
reads and nothing may come to require.

Scale is also **not** used to validate containment. A cellar under a city wall, a
district containing an abandoned settlement, a pocket dimension behind a door — worlds
are full of places that do not nest by size, and refusing them would be the engine
overruling the author about their own geography.

## Containment is a forest

`parent_location_id`, one column, which gives "at most one direct parent" for free.

The rules that a column cannot express live in
`world_locations.hierarchy.check_parent`, and run before every write that sets
containment:

- the parent must exist
- a location may not be its own parent
- containment may not cycle, at any depth
- a parent must be in the same world
- session-local geography may sit inside template geography; **never the reverse**, and
  never inside another session's

The cycle rule is the one that needs a graph. `A.parent = B` and `B.parent = A` are each
individually fine; together they are a town inside the tavern inside the town, and every
ancestor walk after that runs forever.

```text
World
└── Continent
    └── Kingdom
        └── Region
            └── City
                └── District
                    └── Tavern
                        └── Cellar
```

The same abstraction carries `Planet > Orbital Station > Deck > Laboratory`, and
`World > Material Realm | Spirit Realm` with portals between them. No level is
mandatory, no level is named in code, and there is no special case for dimensions.

### Traversal

`get_parent`, `get_children`, `get_ancestors`, `get_descendants`, `is_within` — all over
a `LocationIndex`, an in-memory view of the definitions a session can see.

Loaded whole rather than walked, because containment questions need more than one node
at a time and answering them one query at a time is how an N+1 gets written. That is
affordable because a narrative world holds tens to low hundreds of places, not a street
network. `MAX_GRAPH_SIZE` (500) is the guard on that assumption, and a world that
exceeds it logs a warning: the fix is a recursive CTE in the adapter, not a bigger
number. **This is a deviation from the spec's suggestion of CTEs up front**, taken
because it keeps every containment rule in the domain and testable without a database.

`is_within(x, x)` is False. "Inside" is a relationship between two different places, and
a caller wanting "here or below" can write that in one obvious line.

## Connections

Never inferred. Sharing a parent connects nothing:

```yaml
LocationConnection:
  from_location_id, to_location_id
  bidirectional:        false is honoured, never quietly reversed
  category:             passage | road | path | vertical | portal | water | air | transit | other
  subtype:              door, stairs, secret_passage, imperial_highway, teleport_gate
  physical_distance:    {value, unit} or nothing
  base_travel_minutes:  nominal crossing time, or nothing
```

**Direction is real.** A drop shaft, a waterfall, a one-way portal: `bidirectional:
false` means the far end is not an exit back, and nothing supplies the return edge.

**Distance is not duration**, and neither is derived from the other:

| | distance | travel |
|---|---|---|
| portal | 4000 km | 1 minute |
| doorway | — | 0 |
| road | 20 km | contextual |

Zero and absent are different: a doorway takes no time, an unmeasured road is
unmeasured. Both are inputs to the future TravelEngine, which will compute an actual
duration from the mode, the traveller and the conditions. Travel modes — walking, horse,
vehicle, train, ship, flight, teleport — are that engine's business and are not assumed
anywhere here.

## State

Per session, and per session only:

```yaml
LocationState:
  condition:              pristine | intact | worn | damaged | heavily_damaged | ruined | destroyed
  accessibility:          open | restricted | closed | blocked | sealed | inaccessible
  security_level:         0..100
  local_danger_modifier:  -100..100
  owner_entity_id, controller_entity_id
```

**Condition and accessibility are independent.** `pristine` + `restricted`, `damaged` +
`open`, `intact` + `sealed`, `destroyed` + `open` are all real situations, and collapsing
them would make "ruins you can walk into" unrepresentable.

**A destroyed definition is never deleted.** The ruins of the Broken Crown remain a valid
destination, a valid subject of facts, and a valid thing to rebuild.

`security_level` is intensity, not probability — a future system turns it into guards,
surveillance and law response, and nothing here reads it as "70% chance of being caught".
`local_danger_modifier` shifts the world's baseline danger rather than replacing it: a
per-location copy of the whole danger configuration would be free to drift from the
world's own rules.

**Ownership is not control.** A palace owned by the Kingdom of Aster and held by the
Northern Rebellion is the interesting case, and one field could not say it.

### Access usually belongs to the edge

```text
Castle (open)
├── Main Gate:      blocked
└── Secret Tunnel:  open
```

The castle is still generally accessible. `LocationState.accessibility` is the
high-level status of the place; `LocationConnectionState.accessibility` is whether a
specific crossing works, and it is usually the one that decides whether somewhere can be
reached. Anything asking "can I get in?" has to read both.

`restricted` counts as passable, deliberately: a guarded gate is a gate, and whether
*this* character may pass is a question for a future access-requirement system —
keys, permissions, faction standing, skills, time windows. That system does not exist
and there is no scripting stored in the database for it.

### Destruction does not cascade

Marking a castle destroyed does not mark everything inside it destroyed. A resolved event
decides consequences explicitly:

```text
Castle        heavily damaged
Throne Room   destroyed
Library       damaged
Dungeon       intact
```

Connection states change the same way — explicitly, through validated mutations.

## Zones

A named area inside a location: the bar, the fireplace, the tables by the window. No
state, no connections, no children, not a travel destination.

**Location or zone?** Make it a location when it needs persistent state, can contain
other places, has its own exits, can be travelled to, or matters outside the immediate
scene. Otherwise a zone.

```text
Tavern cellar          → location (enterable, can flood, can be sealed)
Table by the fireplace → zone
```

### Lazy granularity

> Do not create spatial detail until the game needs persistence at that level.

A world may start with `Royal Academy` and nothing inside it. Play may later establish a
library, a training yard and a classroom. Once accepted and persisted those are stable
canon for that session, and they are not regenerated.

## Template and session canon

```text
world template geography
+ this session's generated places
+ this session's states
= the spatial reality of one save
```

`origin_session_id IS NULL` marks reusable template geography that every session of the
world reads. A non-null value marks canon that gameplay invented in one save.

**Template definitions are never copied per session.** Ten sessions read the same rows
and differ only in their states. That is also why the visibility rule cannot be a foreign
key — "template, or mine" is a disjunction no single FK expresses — so every spatial
query filters on it, and the adapter is the only place that filter is written.

Session-local content is never promoted into the template. That is future editing
tooling and has deliberately no code path: an automatic promotion would let one player's
improvisation rewrite the world everyone else starts from.

### Generated content becomes deterministic canon

The rule the whole design turns on:

> Generative world creation may be stochastic **once**. After validation and persistence,
> the accepted result is deterministic canon for that session.

"Starfall Books, on the east side of Riverwood" may be a roll of the dice the first time.
Afterwards it is in the graph, it arrives in every later prompt as established geography,
and the model is never asked to imagine it again. A world where the bookshop moves each
time it is mentioned is a world with no places in it.

## What the Story Director may do

**May**: narrate known locations, describe their current state, propose small new places,
propose narrative facts about a place, and narrate spatial changes the game has already
resolved.

**May not**: move characters between places, use a blocked connection, repair or destroy
anything, change accessibility, change ownership or control, rewrite containment, rewrite
routes, mint a canonical id, or create major geography.

That is not a prompt-level hope in most cases. `StateMutationBatch` refuses to be
constructed with a spatial mutation under `story_director` authority — there is no open
tier in spatial state, because every field in it changes what a character can physically
do next.

### Location proposals

```yaml
LocationProposal:
  name, description
  category, subtype, scale
  parent_location_id:  an existing place, from the context
```

There is **no `id` field**, and there never will be: a model that could name a uuid could
overwrite a place, and the uuid it would name is one it read in a prompt. The application
mints ids.

Each proposal runs a gauntlet: a parent the session can see → the creation policy allows
something that large → nothing by that name already exists → persisted as session-local
canon → given a starting state. A refusal is not an error; the turn continues.

### Creation policy

| scale | narration may create |
|---|---|
| point, room, building, site | yes |
| district, settlement, regional, continental, world | no |

A shop, a room, a courtyard, a shrine. Not a district — a new district implies streets,
residents and a place in a city nobody authored. An importance of 5 is also refused from
narration: scale is what the model chose, and a "point-scale" location asserted to
reshape the story is what a model produces when it has decided something big and picked a
small enough box for it.

Authored geography is not policed — an author writing a continent is the intended use of
the model. Two tiers and a table, deliberately not a policy engine: a policy engine
written before anything consults it would be guessing at requirements the travel, faction
and quest systems have not stated yet.

## Mutations and atomicity

Spatial changes join facts in one batch:

```text
StateMutation
├── SetFact
├── RemoveFact
├── UpdateLocationState
└── UpdateConnectionState
```

`BRIDGE_COLLAPSED` is one thing that happened and it moves several:

```text
bridge LocationState.condition       → destroyed
bridge ConnectionState.accessibility → blocked
local danger modifier                → +30
```

All of it or none of it, in one transaction, against one `GameEvent`, moving the state
revision once. Validation runs to completion before the first write, so a batch with one
bad mutation is usually a refusal rather than a rollback. See
[world-state-facts.md](world-state-facts.md#atomicity).

Mutations are partial: every field but the target is optional and `None` means "leave it".
A mutation that required the whole state would make "block this gate" a read-modify-write
whose read came from before the turn began.

## Facts and dedicated state do not overlap

A location's condition **is not** a `WorldFact`. The dedicated model gives it a closed
vocabulary, a check constraint, a column the engine can query, and one authority; a fact
would put mechanical state in a free-form JSON column, and then two rows would claim to
know whether the bridge is standing.

This is enforced, not just documented. `system.location_condition` and its neighbours
resolve to the `DEDICATED` policy that **no authority** may write, and `world.condition`
is additionally refused when the subject is a location.

The division cuts both ways. Narrative truths about a place are still facts, and are
exactly what the fact store is for:

```yaml
subject_type: location
subject_id:   <Broken Crown>
property:     narrative.reputation
value:        "Known meeting place for smugglers."
```

## Spatial context

The graph is a graph; a prompt is a page. `spatial_context.py` is the deterministic
function between them, and its whole job is deciding what to leave out.

```text
CURRENT     where the scene is
ADJACENT    one connection away, and what is directly inside
LOCAL       the containing place
REGIONAL    ancestors, capped at four
GLOBAL      not sent
```

Rendered into the prompt as:

```text
# Where this is happening  (authoritative: this is the geography)
Here: The Broken Crown (tavern, damaged)
Within: The Lantern Quarter
Areas here: the bar, the back tables, the fireplace
Inside this place: The Broken Crown cellar (cellar)
Ways out:
- Market Street, via the door, about 0 min  — CLOSED, cannot be used
- The Broken Crown cellar, via the stairs, about 1 min
```

Three decisions worth naming:

- **No ids.** The director narrates geography, it does not address it. An id a model has
  seen is an id it will eventually invent a sibling for.
- **Blocked exits are shown and marked, not hidden.** A model that cannot see the barred
  gate writes the player straight through it.
- **Default state is omitted.** "intact, open" on every place is tokens spent teaching a
  model to ignore the field; the one entry saying "destroyed" is the point.

`ADJACENT` is a retrieval tier, not a distance. It means "one edge away".

### How the scene finds its place

There is no canonical position in this system — `CharacterState` does not exist and is
explicitly out of scope for this release. Until it arrives,
`resolve_scene_location` matches `GameSession.current_location` — a string the player
typed — against location names: exact, case-insensitive, trimmed, and **refusing
ambiguity**. Two places called "Market Street" resolve to nothing rather than to
whichever row came back first.

No fuzzy matching, no substring search, no closest-name. Those turn a missing match into
a *wrong* one, and a wrong match hands the director the exits of somewhere else. Nothing
is stored from it and nothing is written by it; a failed match costs the prompt one
optional section. **This is a temporary bridge and is meant to be deleted** when
`CharacterPosition` supplies a real id.

## Deterministic topology

Given the definitions, the connections and their states, questions like *is there a
route?*, *is this connection open?* and *are these directly connected?* are answered by
code, never by a language model. Route topology is deterministic.

Journey *outcomes* may later be stochastic — encounters, delays, navigation failure,
hazards — and those will use a future seeded game RNG, not model sampling. Sampling
temperature is not a dice roll.

## HTTP surface

```text
GET  /api/v1/worlds/{id}/locations                             template geography
POST /api/v1/worlds/{id}/locations                             author a place
POST /api/v1/worlds/{id}/connections                           author a traversal
POST /api/v1/worlds/{id}/locations/{id}/zones                  author a zone
GET  /api/v1/sessions/{id}/locations                           visible, with state
GET  /api/v1/sessions/{id}/locations/{id}                      one place and its neighbours
GET  /api/v1/sessions/{id}/spatial-context/{id}                the scene-sized view
```

**Deviation from the spec's suggested paths.** §58 proposes bare `/locations/{id}` and
`/locations/{id}/children`. Those are session-scoped here instead, because a read with no
session cannot apply the visibility rule — it would either leak another save's generated
canon or hide half of this one, with no way for the caller to tell which.

There is **no state mutation on this router**. A bridge that collapsed through a REST call
would have no event explaining it and would not move the state revision. Changing what has
happened to a place goes through `state_service`, reachable in development at
`POST /api/v1/dev/sessions/{id}/world-state/changes`.

## Deliberately not built

Named so the absence is a decision:

- **CharacterState and canonical position.** The largest gap, and the one that keeps the
  scene anchored by a name-match today. `SpatialPresence` will be `{location_id, zone_id}`
  — coarse position plus an optional intra-scene area, and no metric coordinates.
- **InTransit.** Being *between* places, with a connection, an origin, a destination, a
  departure and an expected arrival. The graph is shaped to carry it; nothing implements
  it, and travel is not assumed to be instantaneous.
- **TravelEngine.** Routing, mode selection, real durations, encounters en route.
- **SceneState.** The immediate ephemeral context: participants, active zone, temporary
  objects, relative positions. Different lifetime, different owner, different failure
  mode — writing it into a durable table would fill a save's permanent truth with
  furniture.
- **EncounterSpace.** Tactical positions, ranges, cover, hazards. "Elena is 2.7m
  northeast of the player" does not belong in long-term state.
- **InteractionRange.** `contact | immediate | near | scene | extended | remote |
  unlimited` — touch, sword, conversation, bow, radio. Documented so the spatial model
  can support it; not implemented.
- **Perception.** Same location is not line of sight, not hearing, not reach. Walls,
  doors, lighting, noise, cover and skills all matter and none of them belong in
  `LocationState`.
- **KnowledgeState.** No `discovered_by_player` anywhere in spatial data. A secret passage
  objectively exists while the player has no idea it does, and a discovery flag would make
  those two situations the same row. StoryContext will eventually filter hidden geography;
  today it does not, which means the prompt can mention a place the player has not found.
- **Weather and environment.** Will be spatially scoped and inherited down the hierarchy —
  region rains, town inherits, dungeon overrides. No weather field is added here early.
- **Mobile locations.** Ships, trains, caravans, moving fortresses. The architecture
  tolerates a location also being an entity that moves; the container problem is not
  solved.
- **Population, economy, NPC schedules, interactive maps, procedural geography.**

## Code map

```text
app/domain/world_locations/     enums, definitions, connections, states,
                                hierarchy, policy, mutations
app/domain/state_mutations.py   the batch that carries fact and spatial changes
app/application/spatial_service.py    load the graph, materialise state, create places
app/application/spatial_context.py    the scene-sized, deterministic selection
app/application/location_proposals.py review what the director invented
app/api/v1/locations.py         authoring and session-scoped reads
```

Tests: `tests/test_world_locations.py` (domain), `tests/test_spatial_state.py` (services
and adapter against a real database), `tests/test_locations_api.py` (HTTP and the
director's proposals).
