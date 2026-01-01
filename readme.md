# Box-World JSON Planner Interface (v1)

This repository provides a Python tool for specifying **box rearrangement planning problems**
in a structured **JSON format**, compiling them into **PDDL problem instances** for the
`BOX-WORLD` domain, and optionally **solving** them using an external planner
(e.g., Fast Downward).

The tool supports two modes:
- **convert** — JSON → PDDL problem file
- **solve** — JSON → PDDL → planner → **JSON plan output**

----------------------------------------------------------------------
## Quick Start
----------------------------------------------------------------------

```bash
./json-to-pddl.py convert example.json -o problem.pddl
./json-to-pddl.py solve example.json --plan-out plan.json

----------------------------------------------------------------------
## Dependencies and Installation (Fast Downward)
----------------------------------------------------------------------

The `solve` mode of this tool requires an external PDDL planner.
It has been tested with **Fast Downward 24.06.1**.

Fast Downward is **not included** in this repository and must be installed separately.

----------------------------------------------------------------------
## Installing Fast Downward (Tarball Method)
----------------------------------------------------------------------

### 1. Download the Fast Downward tarball

Download the latest Fast Downward release tarball from:

https://www.fast-downward.org/latest/releases/

For example:

```bash
wget https://www.fast-downward.org/latest/files/release24.06/fast-downward-24.06.1.tar.gz
```

---

### 2. Extract the tarball

```bash
tar -xzf fast-downward-24.06.1.tar.gz
cd fast-downward-24.06.1
```

This will create a directory containing the Fast Downward source code.

---

### 3. Build Fast Downward

```bash
python3 build.py
```

This compiles Fast Downward and creates the launcher script:

```
fast-downward.py
```

---

### 4. Verify the installation

```bash
./fast-downward.py --help
```

You should see the Fast Downward help message.

---

### 5. Using Fast Downward with this tool

By default, the solver assumes the planner is available at:

```
././fast-downward-24.06.1/fast-downward.py
```

You can override the planner path explicitly:

```bash
./json-to-pddl.py solve problem.json \
  --planner /path/to/fast-downward.py \
  --plan-out plan.json
```

----------------------------------------------------------------------
## Tested Configuration
----------------------------------------------------------------------

- Fast Downward 24.06.1
- Python 3.9+
- Linux / WSL environments



----------------------------------------------------------------------
## Command-Line Usage
----------------------------------------------------------------------

### Convert JSON to PDDL

```bash
./json-to-pddl.py convert problem.json -o problem.pddl
```

If `-o` is omitted, the generated PDDL problem is printed to stdout.

---

### Solve JSON problem and return a plan

```bash
./json-to-pddl.py solve problem.json \
  --domain box-world-domain.pddl \
  --planner ./fast-downward.py \
  --plan-json-out plan.json
```

If `--plan-json-out` is omitted, the plan JSON is printed to stdout.

The planner is expected to write multiple plan files of the form:

```
plan.1, plan.2, ..., plan.N
```

Larger numeric suffixes correspond to better plans.  
The tool automatically selects the **best plan**.

----------------------------------------------------------------------
## JSON Input Format
----------------------------------------------------------------------

### 2.1 Top-Level Structure

```json
{
  "problem_name": "...",
  "locations": [... or {...}],
  "boxes": [... or {...}],
  "initial_state": { ... },
  "forbidden_stack": [...],   // optional
  "goal": { ... }
}
```

Required fields:
- `problem_name`
- `locations`
- `boxes`
- `initial_state`
- `goal`

Optional fields:
- `forbidden_stack`

----------------------------------------------------------------------
## Locations
----------------------------------------------------------------------

Locations may be specified in either **minimal** or **property-annotated** form.

### Minimal form
```json
"locations": ["L1", "L2", "L3"]
```

### With properties
```json
"locations": {
  "L1": { "color": "white" },
  "L2": { "color": "black" },
  "L3": {}
}
```

Supported property:
- `color`: `"black"` | `"white"`

These map to PDDL predicates:
- `(black L)`
- `(white L)`

Unknown properties are ignored by the generator.

----------------------------------------------------------------------
## Boxes
----------------------------------------------------------------------

Boxes follow the same structure and semantics as locations.

### Minimal form
```json
"boxes": ["B1", "B2", "B3"]
```

### With properties
```json
"boxes": {
  "B1": { "color": "black" },
  "B2": {},
  "B3": { "color": "white" }
}
```

Properties are optional and handled identically to location properties.

----------------------------------------------------------------------
## 5. Initial State
----------------------------------------------------------------------

The **entire initial world state** is specified under `initial_state`.

### Example
```json
"initial_state": {
  "robot_at": "L1",
  "holding": null,
  "stacks": {
    "L1": ["B1", "B2", "B3"]
  }
}
```

Fields:
- `robot_at` (required)  
  Starting location of the robot.

- `holding` (optional)  
  Name of a box the robot starts holding, or `null`.  
  If omitted or `null`, the robot starts with empty hands.

- `stacks` (required)  
  Mapping from **non-empty** locations to stacks of boxes.

### Stack Ordering (IMPORTANT)

Stacks are listed **from top to bottom**.

```json
"L1": ["B1", "B2", "B3"]
```

represents:

```
B1   (top)
B2
B3   (bottom)
L1   (location)
```

Locations omitted from `stacks` are assumed empty.

----------------------------------------------------------------------
## Initial State Semantics
----------------------------------------------------------------------

For a stack at location `L` with boxes `[t0, t1, ..., tk]` (top → bottom), the
following PDDL facts are generated:

- `(on t0 t1)`
- `(on t{i} t{i+1})` for `i = 0..k-1`
- `(on tk L)`
- `(clear t0)`
- `(box-at ti L)` for all boxes in the stack

For locations not listed in `stacks`:
- `(clear L)`

Robot hand state:
- If `holding = B`: `(holding B)`
- Otherwise: `(hands-empty)`

Invariant:
Each box must appear **exactly once** across `{holding} ∪ stacks`.

----------------------------------------------------------------------
## Forbidden Stacking (Optional)
----------------------------------------------------------------------

```json
"forbidden_stack": [
  ["B2", "B1"],
  ["B3", "B2"]
]
```

Each pair `[top, bottom]` generates:
```
(forbidden-stack top bottom)
```

If omitted, no stacking constraints are imposed.

----------------------------------------------------------------------
## Goal Specification (v1)
----------------------------------------------------------------------

Goals describe the desired final configuration of the world.

### Structured Goal Predicates

Supported structured predicates:
- `on` — box on box or box on location
- `box-at` — box at a location
- `clear` — object (box or location) is clear

All structured goal predicates are **implicitly conjoined**.

### Example
```json
"goal": {
  "on": [
    ["B2", "B3"],
    ["B3", "L2"]
  ],
  "clear": ["B2"]
}
```

Equivalent PDDL goal:
```lisp
(and
  (on B2 B3)
  (on B3 L2)
  (clear B2)
)
```

---

### Verbatim PDDL Goal Formulas

Advanced users may include raw PDDL formulas using the `pddl` field.

```json
"goal": {
  "pddl": [
    "(robot-at L2)",
    "(exists (?x - box) (and (clear ?x) (not (holding ?x))))"
  ]
}
```

Notes:
- Each formula must be a **string**
- All formulas are conjoined with structured goals
- Verbatim PDDL is not parsed or validated by the tool

----------------------------------------------------------------------
## JSON Output Format (Solve Mode)
----------------------------------------------------------------------

When run in **solve** mode, the tool outputs a **JSON plan file**.

### Format
```json
{
  "plan": [
    "(move l1 l2)",
    "(pickup b1 l2)",
    "(stack b1 b2 l2)"
  ],
  "cost": 12
}
```

Fields:
- `plan`: ordered list of action strings (as produced by the planner)
- `cost`: integer plan cost, or `null` if unavailable

If `--plan-out` is not specified, this JSON object is printed to stdout.

----------------------------------------------------------------------
## Minimal Example
----------------------------------------------------------------------

```json
{
  "problem_name": "tiny",
  "locations": ["L1", "L2"],
  "boxes": ["B1"],
  "initial_state": {
    "robot_at": "L1",
    "stacks": {
      "L1": ["B1"]
    }
  },
  "goal": {
    "on": [["B1", "L2"]]
  }
}
```

----------------------------------------------------------------------
## Version Notes
----------------------------------------------------------------------

- This document describes **v1** of the Box-World JSON format.
- Structured goals are limited to conjunctions.
- Future versions may add:
  - disjunction (`or`)
  - quantifiers (`exists`, `forall`)
  - richer plan metadata and validation
