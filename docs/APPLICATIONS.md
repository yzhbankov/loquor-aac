# Applications

Where loquor is worth using, area by area — and where it is not.

Every idea here was put in front of two critics: a **business** one ("who
pays, and what do they already use?") and a **technical** one ("will it
work with these radios, this model, this code?"). Six ideas survived and
are described below with the objections and the answers. The ones that
did not survive are [listed at the end](#what-did-not-survive), because
knowing where not to use it is half of the value of this page.

None of these is deployed. "Real" here means: the pain exists today, the
hardware is off the shelf, and the missing code is named.

## What the technology actually gives

| property | what it means in practice |
|---|---|
| no infrastructure | no broker, server, account or site network; two devices in radio range are enough |
| self-describing devices | a device's card says what it measures and what it can do, with schemas; another device, or a person, uses it without integration work |
| plain language, produced locally | you ask a device a question and it answers from its own readings; raw data stays on the device |
| guarded actions | the LLM proposes, code checks the input against a schema on both devices before anything moves |
| cheap nodes | a device without an LLM still takes part; only the device that decides needs a model |

And its limits, which decide most of the verdicts below:

| limit | consequence |
|---|---|
| radio range: Bluetooth covers a room or an aisle, Wi-Fi one access point or a phone hotspot; relaying is partial | devices must meet; nothing here covers a whole site at once |
| an answer takes seconds; small models misjudge numbers | never in a control loop, never the only safety barrier |
| messages up to 64 KB, no bulk transfer yet | readings and summaries, not images or log files |
| the hub keeps only the latest reading; nothing survives a restart | "what happened since yesterday?" cannot be answered **today** |
| a node needs Linux and Python (a Pi Zero), about a watt | not a coin-cell sensor; no microcontroller firmware yet |
| any device in range may ask by default; pairing has no command yet | not ready for a place where strangers are in range |
| tested between two laptops, with emulated hardware | no field data on range, uptime or battery |

## The test

Use loquor when all three are true:

1. there is **no network you can rely on, or none you are allowed to use**;
2. **devices or people meet ad hoc** — someone walks up, something drives past;
3. the useful output is **a judgement or a summary in words**, not a threshold.

If a threshold is enough, use a rule. If there is a network and a hub,
use the hub. If milliseconds or safety matter, use a controller.

All six survivors have the same shape: **a device you walk up to and
ask, or collect from, with no network.** The areas differ in the sensors.

| # | area | application | closest to real |
|---|---|---|---|
| 1 | laptops | activity journal across your own machines | ★★★ no hardware to buy |
| 2 | smart home | keeper for a cabin, boat or van | ★★★ one Pi, real sensors |
| 3 | warehouse | site-trial data logger | ★★ needs history and pairing |
| 4 | manufacturing | machine that explains its last stop | ★★ same, plus a security review |
| 5 | agriculture | pump house that explains itself | ★★ needs rules |
| 6 | drones | drone as a data collector for field nodes | ★ only if the drone flies there anyway |

---

## 1. Laptops — an activity journal across your own machines

**Situation.** You work on two or three computers. "What did I do
today?" is spread over all of them, and the trackers that answer it
either stay on one machine or sync through a cloud — which a work laptop
often may not use.

**Setup.**

| device | role | parts |
|---|---|---|
| each laptop | agent | sensors: frontmost application, idle time, git commits, files changed (`command` sensors), or an existing local tracker read through the `http` driver |
| a second laptop, later a phone | asks | no LLM needed: it sends `ask` to each laptop and shows the answers |

```
you:          "What did I do since this morning?"
Work Laptop:  "Mostly the loquor repository: three commits, two hours in the editor, forty minutes in calls."
Home Laptop:  "An hour in the browser, then the tax spreadsheet. Idle since 14:10."
```

Each laptop summarises its own log with its own model. Only the summary
crosses the radio.

**Business critic.**
- *"Time trackers exist, some of them local and free."* Do not rebuild
  one. Use it as the sensor. What loquor adds is the part they lack: one
  question answered by every machine, with no sync service.
- *"Nobody pays for this."* Correct. It is the project's own daily-use
  case, not a product. Its worth is that it forces history, pairing and a
  phone client to exist — which every other application here needs.
- *"This is employee monitoring."* On other people's machines it would
  be. It is for devices you own; the profile should require a paired
  asker.

**Technical critic.**
- *"A closed laptop cannot answer."* The asker keeps the last answer per
  device and says how old it is.
- *"The model will invent activities."* It gets totals computed by code
  (minutes per application, commit titles) and only phrases them.
- *"There is no phone client."* True. The first version is laptop to
  laptop; Android through Termux over Wi-Fi is the cheapest thing to try
  and is untested.

**Missing today.** Reading history with a time window; pairing from the
command line; a `digest` command that asks every agent in range.

---

## 2. Smart home — a keeper for a cabin, a boat or a van

**Situation.** A place you leave for weeks, with a battery, a water
tank, a bilge or a cellar that can go wrong, and no internet. You arrive
and want one answer: is anything wrong, and what happened while I was
away?

**Setup.**

| device | role | parts |
|---|---|---|
| a Raspberry Pi on the 12 V supply | agent | battery monitor over `serial`, temperature over 1-wire (`file`), tank and bilge level on `gpio`, a relay as a trigger |
| your laptop | asks | on arrival, over Bluetooth |

```
you:     "Anything wrong since I left?"
Keeper:  "The battery fell to 11.9 V on two nights and recovered by noon. The bilge pump ran once, on the 14th. The cabin stayed above 4 °C."
```

**Business critic.**
- *"In a house, Home Assistant does all of this."* Yes. In a house with
  Wi-Fi, use it — loquor is not a home hub and should not try to be one.
  This is for the place with no network and no hub.
- *"Each vendor has a Bluetooth app."* One app per device, each showing
  its own graph. The keeper reads all of them and answers one question.
- *"Market?"* Hobbyists and makers. It is the cheapest way to get real
  hardware, real uptime and real range numbers for this project.

**Technical critic.**
- *"A Pi with a model drains the battery."* The model runs only when you
  ask. Between questions the Pi samples and logs; a Pi Zero as a node
  plus the model on your laptop is the lower-power layout.
- *"It only knows the latest reading."* That is the gap. Without history
  this application does not exist.
- *"The model will misread the numbers."* Minimum, maximum, trend and
  threshold crossings are computed by code and given to the model as
  facts.

**Missing today.** Reading history on disk with summaries; one `when`
rule (bilge high → pump) so reflexes do not wait for a model.

---

## 3. Warehouse — a site-trial data logger

**Situation.** A vendor installs a system in a customer's warehouse —
positioning, sensors, vehicle terminals — for a trial or commissioning.
The devices need watching for a week. The customer's Wi-Fi is either not
there yet or closed to contractors, and getting access takes longer than
the trial.

**Setup.**

| device | role | parts |
|---|---|---|
| the installed devices | nodes, or read through one | health counters, battery, link quality, error counts — over their own `serial` or `http` interface |
| a logger box left on site (a Pi-class computer) | agent | collects from every device in range, keeps the history |
| the technician's laptop | asks | on the next visit, standing in the aisle |

```
technician:  "What happened in the last 24 hours?"
Logger:      "Eleven of twelve devices reported all day. Device 7 dropped out from 02:10 to 03:40 and its error count rose by 300. Battery on vehicle 3 is at 21 %."
```

**Business critic.**
- *"Customers have Wi-Fi."* Having it and being allowed on it are
  different things. The logger needs no permission from the customer's
  IT, which removes a dependency from every trial.
- *"A technician can pull logs with a cable."* From one device at a
  time, and only what the device kept. The logger records all of them
  continuously and answers in a sentence.
- *"Why an LLM? A dashboard shows this."* A dashboard needs a server and
  someone to read it. For a technician on a ladder, the first sentence
  is the product; the numbers are still there (`loquor ask "Logger"`
  with no question returns the data).

**Technical critic.**
- *"The devices are microcontrollers; they cannot run Python."* Today
  the logger reads them through the interface they already have. A
  device becomes a real node only with the microcontroller firmware,
  which does not exist yet.
- *"One box does not cover a warehouse."* It covers what is in radio
  range. Either several boxes, or the box rides on a vehicle that visits
  every aisle.
- *"Device logs are megabytes."* Counters and summaries fit in a
  message; raw logs wait for bulk transfer.

**Missing today.** History; pairing; the `collect` skill writing to disk;
a bulk transfer for raw logs; microcontroller node firmware.

---

## 4. Manufacturing — a machine that explains its last stop

**Situation.** A machine builder's service technician stands in front of
a stopped machine in a customer's plant. Remote-service gateways exist,
but they need outbound internet from the plant, and many plants refuse
it. The technician gets a cable, vendor software and the operator's
memory of what happened.

**Setup.**

| device | role | parts |
|---|---|---|
| an edge computer in the cabinet | agent, read-only | reads the controller through a function sensor (Modbus, OPC UA — no built-in driver yet); keeps history; **no triggers** |
| the technician's laptop | asks | paired once, over Bluetooth at the cabinet |

```
technician:  "What happened before the stop at 14:10?"
Machine:     "Spindle temperature rose from 61 to 78 °C in the twenty minutes before. Coolant flow fell below 4 l/min at 13:52. Vibration did not change."
```

**Business critic.**
- *"Remote-service products already do this."* Where the plant allows
  them. This is the fallback that needs nothing from the plant's
  network, and it also serves the plant's own maintenance crew.
- *"A wrong diagnosis is a liability."* It does not diagnose. It reports
  what was measured, with the numbers, and the technician decides.
- *"Who buys it?"* The machine builder, as part of the machine. It is a
  long sale; this is not where the project starts.

**Technical critic.**
- *"A radio in a control cabinet is an attack surface."* The strongest
  objection. Answers: the box has no triggers, so nothing can be
  actuated; asking requires a pinned key; Bluetooth limits range to the
  cabinet. Still, the code has had no security review, and without one
  it does not go into a plant.
- *"Controller data changes every few milliseconds."* The sensor
  function samples and aggregates; the model sees statistics and events.
- *"Machines next to each other could cooperate."* They are already
  wired to one controller. That idea is in the rejected list.

**Missing today.** History with event detection; pairing; a Modbus or
OPC UA driver; a security review.

---

## 5. Agriculture — a pump house that explains itself

**Situation.** A polytunnel, a trial plot or a pump house away from the
farm buildings. There is power (mains at the pump, or a solar panel) and
no network. Watering runs on a timer that knows nothing about the soil
or the tank.

**Setup.**

| device | role | parts |
|---|---|---|
| tank node (a Pi Zero) | node | water level; the valve as a `gpio` trigger with a schema maximum |
| soil nodes | nodes | moisture and temperature per bed |
| controller | agent | the brain; reads the nodes, waters through the tank's valve |
| the grower's laptop | asks | from the tractor or at the door |

```
grower:      "Why didn't you water bed 3 this morning?"
Controller:  "Bed 3 was at 41 %, above the 30 % limit. I watered bed 1 for ten minutes; the tank is at 35 %."
```

This is the [greenhouse demo](../demo/greenhouse/README.md) on real
hardware.

**Business critic.**
- *"A timer costs almost nothing."* And waters an empty tank into wet
  soil. The step from a timer to a commercial controller is large in
  price and often brings a subscription; this sits between them.
- *"Farms buy on return, not on novelty."* A large farm buys a
  commercial system. This is for small growers and research plots, where
  being able to ask "why?" and change a rule in a sentence matters.
- *"Long-range radio sensors cover kilometres."* They do, and they need
  a gateway with a backhaul. Where one exists, use it.

**Technical critic.**
- *"A small model must not decide when to water — it gets numbers
  wrong."* Agreed. Watering should be a rule ("below 30 % and tank above
  20 % → valve, 10 minutes"), and the model's job is to explain and to
  advise. Today the brain decides, limited by the valve's schema
  maximum; rules are the missing piece.
- *"Battery sensors cannot run a Pi."* Right. Coin-cell sensors stay
  what they are and a node reads them; a node needs about a watt, so
  mains or a small panel.
- *"Wi-Fi does not reach across a field."* Nodes must be within one
  access point of the controller. Beyond that, see the next application.

**Missing today.** `when` rules; history; pushed readings, so the
controller wakes on an event instead of a timer.

---

## 6. Drones — a drone as a data collector for field nodes

**Situation.** Sensor nodes stand in a field, along a pipeline or on a
remote site, with no coverage. A survey or inspection drone flies there
on schedule anyway.

**Setup.**

| device | role | parts |
|---|---|---|
| field nodes | nodes | solar-powered, logging their sensors |
| the drone's companion computer | collector, **no LLM** | the `collect` skill over Wi-Fi while it loiters at each node |
| the base laptop | agent | summarises what the drone brought back |

**Business critic.**
- *"A long-range radio gateway is cheaper than a flight."* It is. This
  only makes sense when the drone flies the route for another reason, or
  where no gateway can have a backhaul.
- *"So it is a niche of a niche."* Yes. It is here because it is the one
  honest use of loquor on a drone.

**Technical critic.**
- *"The drone is in range for seconds."* Discovery alone takes up to a
  beacon interval (10 s by default). The mission needs a waypoint with a
  loiter of about half a minute per node, and a shorter interval.
- *"A node holds only its latest reading, so there is nothing to
  collect."* Correct, and decisive: without history on the node this
  application returns one number per flight.
- *"Drones coordinating in flight?"* No. See below.

**Missing today.** History on nodes and a "give me everything since"
request; faster discovery; a field test of range from the air.

---

## What did not survive

| idea | why not |
|---|---|
| drones coordinating in flight through their agents | answers take seconds and the radio reaches tens of metres; flight control needs milliseconds and dedicated links |
| machines on one production line talking to each other | they are already wired to one controller and one SCADA; nothing is missing |
| replacing a home hub | Home Assistant and Matter are local, mature and support thousands of devices |
| warehouse robots negotiating routes | a fleet manager does this in real time under safety certification |
| trailer-to-dock cold-chain checks between companies | both sides must adopt it before either benefits, and telematics already covers a single fleet |
| coin-cell sensors as nodes | a node draws about a watt; long-range low-power radio wins, and such sensors belong *behind* a node |
| closing a control loop or acting as a safety barrier | an LLM is slow and sometimes wrong; rules and controllers do this |
| sending images, video or log files | 64 KB per message and no bulk transfer yet |
| the triage demo as a product | it shows two LLMs cooperating; a medical product needs certification this project does not aim for |

## What the survivors need, in order

| missing piece | needed by |
|---|---|
| **reading history** on disk, with a time window and computed summaries | all six |
| **pairing** from the command line, and paired-only sharing as an easy default | 1, 3, 4 |
| a **client for the person**: `loquor chat`, then a phone | 1, 2, 3, 5 |
| **rules**: `when` a reading crosses a limit → a trigger, or wake the brain | 2, 5 |
| collected readings **kept across restarts** and fetched "since" a time | 3, 6 |
| **bulk transfer** for raw logs | 3, 4 |
| **microcontroller node firmware** | 3, 5 |

History comes first: without it, none of the six can answer its own
headline question. The order to build the applications follows from the
same table — the journal (1), then the keeper (2) on one real Pi, then
the logger (3).
