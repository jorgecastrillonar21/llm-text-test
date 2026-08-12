# WorldState: simulation time

A story needs to know what time it is. Not what time it is for the player — what time
it is *in the fiction*, so that a market can be shut at three in the morning, a wound
can be three days old, and a caravan can arrive while the player is somewhere else.

This is the first piece of the future `WorldState`. It ships the clock and nothing
else: no locations, no NPC schedules, no weather, no travel, no combat. Those systems
all need to reason about time, so time goes in first and they get to depend on
something that already works.

## Simulation time is not turn index

Four distinctions, kept apart on purpose:

| | what it counts | who moves it |
|---|---|---|
| `elapsed_minutes` | fictional minutes since this session began | application code, explicitly |
| `turn_index` | exchanges between the player and the story | every submitted turn |
| calendar date | a projection of `elapsed_minutes` | nobody — it is derived |
| real-world time | wall clock | nobody in this system |

Four turns of dialogue can share a single fictional minute. One action — sleeping,
travelling, a season passing between chapters — can cost eight hours, three days or six
months while the turn counter moves by one. Neither number can be computed from the
other, so both are stored, and **nothing infers the clock from how many messages
exist**.

`elapsed_minutes = 0` is the beginning of *this GameSession*. It is not the beginning
of the fictional universe: what date that instant corresponds to is the world's
business, not the session's.

## Minutes, and only minutes

One simulation unit is one fictional minute. There are no seconds in the game clock
and there will not be. A future combat system may model rounds and actions internally,
but it converts the result to whole minutes before it touches this.

The consequence worth stating: **ordering cannot come from the clock**, because
everything in a turn usually shares a minute. That is what `event_sequence` is for —
see below — and it is why no fake seconds were invented to get a sort key.

## The clock only moves forward

    new_elapsed_minutes >= current_elapsed_minutes

This holds for every normal operation, and it is enforced in three places: the request
model refuses negative durations, `TimeState.advance` refuses them again, and the
column has a check constraint.

Flashbacks, memories, visions and dream sequences are changes of narrative viewpoint,
not rewinds. Whatever eventually tells those stories will not touch this clock. Time
travel and rewind mechanics are out of scope; if either is ever built it gets its own
explicitly-authorised path rather than a negative number slipped into this one.

## The calendar is a projection

```text
session elapsed_minutes  +  world initial_datetime
              ↓
        Calendar (STANDARD_CALENDAR)
              ↓
   "2 June, 842 · 16:42 · afternoon"
```

Nothing derived is stored. There is no `hour` column, no `day` column, no
`time_of_day` column — which means there is nothing that can disagree with the clock.

A world declares the fictional instant its sessions start at:

```yaml
initial_datetime:
  year: 842
  month: 5
  day: 13
  hour: 13
  minute: 0
```

It is fixed at creation, like the language and the rules. Moving a world's origin
would silently reinterpret every fictional timestamp already recorded against it.
Omitted, it defaults to the first morning of year one — obviously fictional, because a
real-looking date would imply a history the world was never given.

`Calendar` is deliberately not `datetime`. The standard library assumes the Gregorian
calendar: leap years, a year-1 floor, timezones, and month lengths a world author
cannot change. `Calendar` is arithmetic over a list of months, so a ten-hour day still
has a recognisable afternoon.

This release ships exactly one calendar — twelve familiar months, 365 days, no leap
rule — and worlds cannot yet define their own. The shape is there so that custom month
names, month lengths, week structures and eras become a matter of storing a different
`Calendar`; the storage, the editor and the era handling are **not built**. A visible
consequence: month names are English regardless of the world's language, and they will
stay that way until a world can bring its own.

`TimeOfDay` — `dawn`, `morning`, `afternoon`, `evening`, `night`, `late_night` — is
derived from the fraction of the day elapsed, and is never persisted.

## Who is allowed to move the clock

> The Story Director may narrate that an action takes time. The application decides
> and commits how much.

The language model has no field in its response schema that reaches the clock, and it
will not be given one. Letting it choose durations would make token sampling the
arbiter of how long a journey took, which is the same mistake as letting `temperature`
resolve a dice roll. See [ai-contract.md](ai-contract.md).

Everything goes through `app/application/time_service.py`. The callers it was built
for — `ActionResolutionService`, `TravelService`, `RestService`, `SimulationService` —
do not exist yet. That is the point: the authority boundary is in place before the
systems that will need it, so none of them can grow a private path to
`elapsed_minutes`.

**Submitting a turn does not advance time.** Deciding that "I search the room
carefully" costs twelve minutes needs a duration model that has not been built, and the
wrong way to get one is to ask the model. Until then the clock moves only when
something explicitly asks it to.

### `simulation.time_progression`

The `WorldRules` setting decides which callers a world admits:

| setting | reasons permitted |
|---|---|
| `paused` | `narrative`, `debug` |
| `action_based` | the above, plus `action`, `rest`, `travel` |
| `active` | the above, plus `simulation` |

`paused` means the world does not move on its own, not that a scenario may never state
that a week went by. `active` means simulation systems may request time *while you are
playing* — it does **not** mean the world advances while the application is closed.

A refusal is an error, not a silent no-op: a caller that believed time passed and was
wrong needs to know.

## Requests and results

```yaml
TimeAdvanceRequest:
  requested_minutes: 480
  reason: rest
  detail: "Sleeping at the inn"     # free text, for the audit trail
  interruptible: true
  source_event_id: null
```

```yaml
TimeAdvanceResult:
  requested_minutes: 480
  advanced_minutes: 192
  started_at: 28980
  ended_at: 29172
  interrupted: true
  interruption: { event_id: ..., event_type: fire_in_the_stables, at: 29172 }
  due_event_ids: [...]              # reached, not executed — see below
```

`advanced_minutes` may be less than requested and never more. The future duration
pipeline — base duration, contextual modifiers, bounded variance — resolves *before*
the call, so the request is always the authoritative ask and an interruption is the
only thing that can shorten it. The result validates its own arithmetic: an
`ended_at` that disagrees with `started_at + advanced_minutes` is refused rather than
returned.

### The duration model that does not exist yet

```text
Action → base duration → contextual modifiers → bounded variance → interruptions → actual
```

    Say hello               ~0 minutes
    Search a room carefully ~10–20 minutes
    Sleep                   480 minutes
    Travel                  distance + terrain + weather + transport + condition

None of that is implemented. When the variance step arrives it will draw from the
seeded game RNG, never from model sampling: a duration has to be reproducible from a
recorded seed for a save to be trustworthy.

## Scheduled events

A minimal, generic model for "at minute 29400 of this session, something of type X
happens".

```yaml
ScheduledEvent:
  id: UUID
  session_id: UUID
  due_at: 29400          # absolute session time, never a delay
  type: string
  payload: {}            # small, and genuinely arbitrary for now
  status: pending | due | processed | cancelled
  interrupt_player_action: false
```

`due_at` is always absolute. `schedule_event` takes a *delay* and converts it
immediately, so what lands in the row is `current_elapsed_minutes + 4320` rather than
"in three days" — a phrase that would mean something different every time it was read.

Advancing time looks at what is due in the span, not at every minute of it:

```text
current_time → target_time → pending events due by then → mark DUE in order → maybe stop
```

Advancing six months costs the same as advancing six minutes. There is no per-minute
loop and there must never be one.

An event still pending but already behind the clock becomes due at the current time,
never by rewinding to its `due_at`. Late is a better failure than lost.

**Interruption.** If an advance is `interruptible` and a due event has
`interrupt_player_action`, the clock stops at that event's minute; everything scheduled
after it stays pending for the next advance. A non-interruptible advance — an authored
"three months later" — runs straight past.

### DUE is not PROCESSED

The four statuses are `pending`, `due`, `processed` and `cancelled`, and the middle two
mean different things:

```text
pending     scheduled, the clock has not reached it
due         the clock reached it, and nobody has answered it yet
processed   the work this event owned was actually carried out
cancelled   it never will be
```

**Time owns chronology and nothing else.** `advance_time` marks the events it walks past
`DUE` and returns their ids. It cannot progress a situation, land a caravan or open a
shop, and it does not know which service could. Reaching an event and executing one used
to be the same line of code, which meant every advance quietly recorded work as finished
that no code had done — and removed it from the pending query, so nobody could find it
afterwards.

The seam is:

```text
advance toward target
      ↓
return the due work, stopping at an interrupting event
      ↓
the owning dispatcher executes it        ← not Time's, and not Time's business
      ↓
complete_scheduled_event                 ← only now is it PROCESSED
      ↓
advance again
```

`load_due_work` is the dispatcher's read: what the clock has reached and nobody has
answered. `complete_scheduled_event` is refused unless the event is `DUE` — a pending
event has not been reached, and a processed one has already been done, which is what
stops a retried dispatch executing the same fictional moment twice. It is called
*after* the work, in the same transaction as whatever the work changed; acknowledging
first and executing afterwards is exactly how the previous design managed to mark
processed what a crash then made sure never happened.

An interrupting event that nobody answers stops the clock at its minute every time, for
as long as it is owed. That is not a deadlock to design around — it is the honest
answer: the world cannot get past something that is supposed to happen and has not.
`cancel_scheduled_event` is how to say it never will.

Terminal is terminal. `processed` and `cancelled` never return to `pending` or `due`,
and `require_transition` enforces it in the domain. There are no retries, queues,
workers or cron: this is fictional scheduling, not infrastructure scheduling.

**Nothing in the game schedules anything yet.** No shop closes, no caravan arrives, no
rent falls due, and there is no World Simulation Scheduler dispatching due work. Those
are domain features and they are deliberately not built; what exists is the generic
infrastructure and the seam they will consume, plus dev endpoints and tests that
exercise it.

## Time is global to a session

If four characters act during the same four-hour interval, global simulation time
advances four hours **once** — not four hours per actor. Future off-screen simulation
consumes the same interval rather than adding its own.

## Real-time synchronisation is off, and stays off

```yaml
real_time_sync:
  enabled: false
```

Closing the application for seven real-world days does not advance the fictional world
seven days. Nothing runs in the background; nothing catches up on startup. A future
opt-in mode would be a separate feature with its own design, not a default.

## Events carry both times

```yaml
GameEvent:
  turn_index: 412       # which exchange
  occurred_at: 28980    # when in the fiction
  event_sequence: 105   # stable order within that minute
```

Multiple events routinely share a fictional minute, so `event_sequence` — a
monotonically increasing per-session counter, unique by database constraint — provides
deterministic ordering. Reads sort by `(occurred_at, event_sequence)`.

### Auditability

"Why did the clock advance from 14:00 to 18:25?" is answerable from the events already
being written, so there is no separate audit table. Every advance that moved the clock
or resolved something records a `time.advanced` event:

```text
travel: requested 265 min | advanced 265 min | 840 -> 1105 | Riverwood to the Capital
```

## Persistence

| where | what |
|---|---|
| `game_sessions.elapsed_minutes` | the clock. `BigInteger`, non-negative by check constraint |
| `worlds.initial_datetime` | five small integers as one JSON value |
| `game_events.occurred_at` | fictional time of an event |
| `game_events.event_sequence` | per-session ordering, unique with `session_id` |
| `scheduled_events` | id, session, `due_at`, type, payload, status, interrupt flag |

No `world_state_json` blob. The clock is one number that is read and written on its
own; burying it in a document would make it harder to query and no easier to evolve.

Migration `c4f1ab6d5e73` adds all of it. Existing sessions get `elapsed_minutes = 0`
and existing events get `occurred_at = 0`: a save written before this has no record of
fictional time, and inventing one would be worse than saying the story starts now.
Existing events are ordered by insertion, the only signal they carry.

> **Migrations and foreign keys.** SQLite cannot alter a column in place, so Alembic's
> batch mode rebuilds the table: copy, `DROP TABLE`, rename. With `PRAGMA
> foreign_keys=ON`, that DROP fires every `ON DELETE CASCADE` aimed at the table —
> rebuilding `worlds` deletes every character, session, message and memory in the
> database, silently and successfully. `migrations/env.py` therefore runs with
> enforcement off and checks `PRAGMA foreign_key_check` afterwards, and
> `tests/test_migrations.py` migrates a populated database and asserts the rows are
> still there. This was a real bug, found by writing that test.

## API

The clock is served with the session rather than from its own endpoint — the screen
that shows it already loads that:

```json
GET /api/v1/sessions/{id}
{
  "turn_index": 4,
  "elapsed_minutes": 29022,
  "time": {
    "elapsed_minutes": 29022,
    "display": { "date": "2 June, 842", "time": "16:42",
                 "period": "afternoon", "elapsed": "20 days, 3 hours" }
  }
}
```

There is no endpoint that sets the clock in normal operation, because no gameplay
system produces durations yet.

### Development endpoints

Mounted only when `APP_ENV` is `development` or `test` — an allowlist, so an
unrecognised value switches them off rather than on.

```text
POST   /api/v1/dev/sessions/{id}/advance-time
POST   /api/v1/dev/sessions/{id}/scheduled-events
GET    /api/v1/dev/sessions/{id}/scheduled-events/due
DELETE /api/v1/dev/scheduled-events/{id}
```

They are not a back door. All of them go through the same application service any future
caller will use: a paused world still refuses, the never-backward rule still holds,
scheduled events still become due, and the advance is still audited. The only privilege
is the `debug` reason, which every world accepts.

The `due` read is the dispatcher's read, exposed because no dispatcher exists yet. A due
list that keeps growing is the symptom to look for: it means something is being scheduled
that nothing owns. `situation.progress` is the one type with an owner today, and it is
answered through `POST /api/v1/dev/sessions/{id}/situations/{sid}/progress`, which
executes the progression and acknowledges the event in the same transaction. `DELETE`
works on pending and due events alike — deciding that work the clock reached is moot is a
legitimate verdict, and a different one from having carried it out.

## What the Story Director sees

One line in the context:

```text
Now: 2 June, 842, 16:42 (afternoon) — 20 days, 3 hours into this story
```

The raw counter is not sent. A narrator has no use for "29022" and would only be
tempted to do arithmetic on it. The system prompt states that the clock is fact, that
the model cannot change it, and that a turn covers seconds or minutes rather than
announcing that a night has passed.

## Frontend

The session header shows the date, the hour and the part of the day, and nothing else.
No calendar view, no scheduling UI, no timeline editor, no sleep or travel controls —
those belong to systems that do not exist. The part of the day is translated; the date
and hour are the strings the backend derived.

## Explicitly not built

The complete `WorldState`; a fantasy-calendar engine or editor; weather; travel;
combat duration; NPC schedules or autonomy; the World Simulation Scheduler that will
dispatch due work; quest deadlines beyond generic scheduling; buffs, debuffs, poison or
sleep mechanics; the seeded game RNG; real-time synchronisation; background simulation
while the application is closed.

Each has an extension point rather than an implementation, and the difference is
deliberate.
