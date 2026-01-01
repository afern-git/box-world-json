# Box-World JSON Problem Format (v1)

This document describes a JSON format for specifying box rearrangement planning
problems, which are compiled into PDDL problem instances for the BOX-WORLD domain.

----------------------------------------------------------------------
1. Top-Level Structure
----------------------------------------------------------------------

{
  "problem_name": "...",
  "locations": [... or {...}],
  "boxes": [... or {...}],
  "initial_state": { ... },
  "forbidden_stack": [...],   // optional
  "goal": { ... }
}

Required fields:
- problem_name
- locations
- boxes
- initial_state
- goal

Optional fields:
- forbidden_stack

----------------------------------------------------------------------
2. Locations
----------------------------------------------------------------------

Locations may be specified in either minimal or property-annotated form.

Minimal form:
  "locations": ["L1", "L2", "L3"]

With properties:
  "locations": {
    "L1": { "color": "white" },
    "L2": { "color": "black" },
    "L3": {}
  }

Semantics:
- Each key is a location name
- Properties are optional
- Supported property:
    color = "black" | "white"
  which maps to (black L) or (white L) in PDDL
- Unknown properties are ignored by the generator

----------------------------------------------------------------------
3. Boxes
----------------------------------------------------------------------

Boxes follow the same structure as locations.

Minimal form:
  "boxes": ["B1", "B2", "B3"]

With properties:
  "boxes": {
    "B1": { "color": "black" },
    "B2": {},
    "B3": { "color": "white" }
  }

Properties are optional and handled identically to location properties.

----------------------------------------------------------------------
4. Initial State
----------------------------------------------------------------------

The complete initial world state is specified under initial_state.

Example:
  "initial_state": {
    "robot_at": "L1",
    "holding": null,
    "stacks": {
      "L1": ["B1", "B2", "B3"]
    }
  }

Fields:
- robot_at (required)
    Starting location of the robot

- holding (optional)
    Box name if the robot starts holding a box
    null or omitted means the robot starts with empty hands

- stacks (required)
    Mapping from non-empty locations to stacks

IMPORTANT:
Stacks are listed **from top to bottom**.

Example:
  "L1": ["B1", "B2", "B3"]

represents:

  B1   (top)
  B2
  B3   (bottom)
  L1   (location)

Locations omitted from stacks are assumed empty.

----------------------------------------------------------------------
5. Initial State Semantics
----------------------------------------------------------------------

For a stack at location L with boxes [t0, t1, ..., tk] (top → bottom):

- (on t0 t1)
- (on t{i} t{i+1}) for i = 0..k-1
- (on t{k} L)
- (clear t0)

For all boxes in the stack:
- (box-at ti L)

For locations not listed in stacks:
- (clear L)

Robot hand state:
- If holding = B:
    (holding B)
- Otherwise:
    (hands-empty)

Invariant:
Each box must appear exactly once across {holding} ∪ stacks.

----------------------------------------------------------------------
6. Forbidden Stacking (Optional)
----------------------------------------------------------------------

Example:
  "forbidden_stack": [
    ["B2", "B1"],
    ["B3", "B2"]
  ]

Each pair [top, bottom] generates:
  (forbidden-stack top bottom)

If omitted, no stacking constraints are imposed.

----------------------------------------------------------------------
7. Goal Specification (v1)
----------------------------------------------------------------------

## Goals

The goal specification defines the desired final configuration of the world.  
In v1, goals are expressed as a **conjunction of simple predicates**, with an optional escape hatch for **verbatim PDDL formulas**.

### Supported structured goal predicates

The following goal predicates are currently supported:

- `on` — box on box or box on location  
- `box-at` — box at a specific location  
- `clear` — object (box or location) is clear  

Formulas are lists of formulas specified in PDDL format -- these are interpreted as conjuctive:
- "pddl": ["<formula>", "<formula>", ...]

All structured goal predicates are **implicitly conjoined** along with the verbatim formula if present.

#### Example
```json
"goal": {
  "on": [
    ["B2", "B3"],
    ["B3", "L2"]
  ],
  "clear": ["B2"],
  "pddl": ["(robot-at L2)", "(exists (?x - box) (and (clear ?x) (not (holding ?x))))"]
}

----------------------------------------------------------------------
8. Minimal Example
----------------------------------------------------------------------

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