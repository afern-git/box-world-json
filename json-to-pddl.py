#!/usr/bin/env python3
"""
json_to_pddl.py

Convert a Box-World JSON instance (v1) into a PDDL problem instance compatible with
the BOX-WORLD domain discussed in this repo.

Key assumptions (v1):
- initial_state.stacks lists stacks TOP -> BOTTOM (first element is the top box).
- locations and boxes can be either:
    * a list of names, OR
    * an object mapping name -> properties dict
- Supported unary property (for both boxes and locations):
    color = "black" | "white"
  which emits (black X) or (white X) in PDDL.
- Goal (v1) supports conjunction of on-relations:
    "goal": { "on": [ [top, bottom_or_location], "box-at": [ [box, location], ... ], 
                "clear": [box_or_location, ... ], "pddl": "<pddl_formula>" }

The generator infers:
- (on ...) relations from stacks
- (box-at box location) for all boxes in each stack
- (clear top_of_stack) and (clear empty_location)
- robot hand state from holding/null
"""

from __future__ import annotations
import json
import argparse
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Optional, Set, Union


# -----------------------------
# Utilities
# -----------------------------

def atom(pred: str, *args: str) -> str:
    return f"({pred} {' '.join(args)})" if args else f"({pred})"

def and_formula(atoms: List[str]) -> str:
    if not atoms:
        # An empty goal is unusual; represent as (and) to be explicit.
        return "(and)"
    if len(atoms) == 1:
        return atoms[0]
    return "(and " + " ".join(atoms) + ")"

def is_name(s: Any) -> bool:
    return isinstance(s, str) and len(s) > 0

def canonical_keys(obj: Union[List[str], Dict[str, Any]]) -> List[str]:
    """
    Deterministic ordering:
    - If a list is provided, preserve the list order.
    - If a dict is provided, sort keys lexicographically.
    """
    if isinstance(obj, list):
        return obj
    return sorted(obj.keys())


# -----------------------------
# Parsing / normalization
# -----------------------------

@dataclass
class ProblemSpec:
    problem_name: str
    locations: List[str]
    boxes: List[str]
    loc_props: Dict[str, Dict[str, Any]]
    box_props: Dict[str, Dict[str, Any]]
    robot_at: str
    holding: Optional[str]   # None means hands empty
    stacks: Dict[str, List[str]]   # location -> [top..bottom]
    forbidden_stack: List[Tuple[str, str]]
    goal_on: List[Tuple[str, str]]
    goal_at: List[Tuple[str, str]]
    goal_clear: List[str]
    goal_pddl: str = ""  # a pddl formula filled in later

def parse_named_objects(field: Any, kind: str) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
    """
    Accepts either:
      - ["L1", "L2"] or ["B1", "B2"]
      - {"L1": {...}, "L2": {...}} or {"B1": {...}, ...}

    Returns (names, props_map).
    """
    if isinstance(field, list):
        names = field
        if not all(is_name(x) for x in names):
            raise ValueError(f'"{kind}" list must contain only strings')
        props = {n: {} for n in names}
        return names, props

    if isinstance(field, dict):
        names = canonical_keys(field)
        props: Dict[str, Dict[str, Any]] = {}
        for n in names:
            v = field[n]
            if v is None:
                props[n] = {}
            elif isinstance(v, dict):
                props[n] = v
            else:
                raise ValueError(f'"{kind}" dict values must be objects (properties) or null; got {type(v)} for {n}')
        return names, props

    raise ValueError(f'"{kind}" must be either a list of names or an object mapping name -> properties')


def load_ProblemSpec(path: str) -> ProblemSpec:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Required top-level fields
    for key in ["problem_name", "locations", "boxes", "initial_state", "goal"]:
        if key not in data:
            raise ValueError(f"Missing required top-level field: {key}")

    problem_name = data["problem_name"]
    if not is_name(problem_name):
        raise ValueError('"problem_name" must be a non-empty string')

    locations, loc_props = parse_named_objects(data["locations"], "locations")
    boxes, box_props = parse_named_objects(data["boxes"], "boxes")

    loc_set = set(locations)
    box_set = set(boxes)

    # initial_state
    init = data["initial_state"]
    if not isinstance(init, dict):
        raise ValueError('"initial_state" must be an object')

    if "robot_at" not in init:
        raise ValueError('Missing required field: initial_state.robot_at')
    robot_at = init["robot_at"]
    if robot_at not in loc_set:
        raise ValueError(f'initial_state.robot_at="{robot_at}" is not in locations')

    holding = init.get("holding", None)
    if holding is None:
        holding_box: Optional[str] = None
    else:
        if not is_name(holding):
            raise ValueError('initial_state.holding must be a box name string or null')
        if holding not in box_set:
            raise ValueError(f'initial_state.holding="{holding}" is not in boxes')
        holding_box = holding

    if "stacks" not in init:
        raise ValueError("Missing required field: initial_state.stacks")
    stacks_raw = init["stacks"]
    if not isinstance(stacks_raw, dict):
        raise ValueError("initial_state.stacks must be an object mapping location -> list-of-boxes (top->bottom)")

    # Normalize stacks: only non-empty locations should appear, but we allow empty lists and ignore them.
    stacks: Dict[str, List[str]] = {}
    for l, stack in stacks_raw.items():
        if l not in loc_set:
            raise ValueError(f'initial_state.stacks references unknown location "{l}"')
        if stack is None:
            continue
        if not isinstance(stack, list):
            raise ValueError(f'initial_state.stacks["{l}"] must be a list (top->bottom)')
        if len(stack) == 0:
            continue
        if not all(is_name(b) for b in stack):
            raise ValueError(f'initial_state.stacks["{l}"] must contain only box name strings')
        for b in stack:
            if b not in box_set:
                raise ValueError(f'initial_state.stacks["{l}"] contains unknown box "{b}"')
        stacks[l] = stack

    # forbidden_stack (optional)
    forbidden_stack_raw = data.get("forbidden_stack", [])
    if forbidden_stack_raw is None:
        forbidden_stack_raw = []
    if not isinstance(forbidden_stack_raw, list):
        raise ValueError('"forbidden_stack" must be a list of [top,bottom] pairs')
    forbidden_stack: List[Tuple[str, str]] = []
    for pair in forbidden_stack_raw:
        if not (isinstance(pair, list) or isinstance(pair, tuple)) or len(pair) != 2:
            raise ValueError('Each forbidden_stack entry must be a pair [top, bottom]')
        top, bottom = pair[0], pair[1]
        if top not in box_set or bottom not in box_set:
            raise ValueError(f'forbidden_stack pair must reference boxes; got [{top}, {bottom}]')
        forbidden_stack.append((top, bottom))

    # goal (v2): {"on": [[top, support], ...]
    #             "box-at" : [[box, location], ...] 
    #             "clear": [box_or_location, ...] }
    goal = data["goal"]
    if not isinstance(goal, dict):
        raise ValueError('"goal" must be an object')

    goal_on_raw = goal.get("on", [])
    if goal_on_raw is None:
        goal_on_raw = []
    if not isinstance(goal_on_raw, list):
        raise ValueError('goal.on must be a list of [top, support] pairs')
    goal_on: List[Tuple[str, str]] = []
    for pair in goal_on_raw:
        if not (isinstance(pair, list) or isinstance(pair, tuple)) or len(pair) != 2:
            raise ValueError('Each goal.on entry must be a pair [top, support]')
        top, support = pair[0], pair[1]
        if top not in box_set:
            raise ValueError(f'goal.on top must be a box; got "{top}"')
        if support not in box_set and support not in loc_set:
            raise ValueError(f'goal.on support must be a box or location; got "{support}"')
        goal_on.append((top, support))

    goal_at_raw = goal.get("box-at", [])
    if goal_at_raw is None:
        goal_at_raw = []
    if not isinstance(goal_at_raw, list):
        raise ValueError('goal.box-at must be a list of [box, location] pairs')
    goal_at: List[Tuple[str, str]] = []
    for pair in goal_at_raw:
        if not (isinstance(pair, list) or isinstance(pair, tuple)) or len(pair) != 2:
            raise ValueError('Each goal.box-at entry must be a pair [box, location]')
        box, location = pair[0], pair[1]
        if box not in box_set:
            raise ValueError(f'goal.box-at box must be a box; got "{box}"')
        if location not in loc_set:
            raise ValueError(f'goal.box-at location must be a location; got "{location}"')
        goal_at.append((box, location))

    goal_clear_raw = goal.get("clear", [])
    if goal_clear_raw is None:
        goal_clear_raw = []
    if not isinstance(goal_clear_raw, list):
        raise ValueError('goal.clear must be a list of boxes and locations')
    goal_clear: List[str] = []
    for box_or_location in goal_clear_raw:
        if not isinstance(box_or_location, str):
            raise ValueError('Each goal.clear entry must be a box or location name')
        if box_or_location not in box_set and box_or_location not in loc_set:
            raise ValueError(f'goal.clear box or location must be a box or location; got "{box_or_location}"')
        goal_clear.append(box_or_location)

    goal_pddl_raw = goal.get("pddl", [])
    if goal_pddl_raw is None:
        goal_pddl_raw = ""
    if not isinstance(goal_pddl_raw, str):
        raise ValueError('goal.pddl must be a string')
    goal_pddl = goal_pddl_raw

    spec = ProblemSpec(
        problem_name=problem_name,
        locations=locations,
        boxes=boxes,
        loc_props=loc_props,
        box_props=box_props,
        robot_at=robot_at,
        holding=holding_box,
        stacks=stacks,
        forbidden_stack=forbidden_stack,
        goal_on=goal_on,
        goal_at=goal_at,
        goal_clear=goal_clear,
        goal_pddl=goal_pddl
    )

    validate_spec(spec)
    return spec


def validate_spec(spec: ProblemSpec) -> None:
    box_set = set(spec.boxes)

    # Each box appears exactly once across holding ∪ stacks
    used: List[str] = []
    if spec.holding is not None:
        used.append(spec.holding)

    for l, stack in spec.stacks.items():
        used.extend(stack)

    dupes = {b for b in used if used.count(b) > 1}
    if dupes:
        raise ValueError(f"Each box must appear exactly once across holding and stacks. Duplicates: {sorted(dupes)}")

    missing = box_set - set(used)
    if missing:
        raise ValueError(f"Each box must appear in holding or in some stack. Missing: {sorted(missing)}")

    # Basic stack sanity: no repeated box within a single stack
    for l, stack in spec.stacks.items():
        if len(set(stack)) != len(stack):
            raise ValueError(f'Stack at location "{l}" contains duplicate box names: {stack}')


# -----------------------------
# PDDL generation
# -----------------------------

def color_facts(props: Dict[str, Dict[str, Any]]) -> List[str]:
    facts: List[str] = []
    for name, pr in props.items():
        if not isinstance(pr, dict):
            continue
        c = pr.get("color", None)
        if c == "black":
            facts.append(atom("black", name))
        elif c == "white":
            facts.append(atom("white", name))
    return facts


def init_facts(spec: ProblemSpec) -> List[str]:
    facts: List[str] = []

    # robot
    facts.append(atom("robot-at", spec.robot_at))

    # hand state
    if spec.holding is None:
        facts.append(atom("hands-empty"))
    else:
        facts.append(atom("holding", spec.holding))

    # colors for all objects (locations + boxes)
    # Merge maps, with locations/boxes both supported
    merged: Dict[str, Dict[str, Any]] = {}
    merged.update(spec.loc_props)
    merged.update(spec.box_props)
    facts.extend(color_facts(merged))

    # forbidden stack
    for top, bottom in spec.forbidden_stack:
        facts.append(atom("forbidden-stack", top, bottom))

    # stacks: TOP -> BOTTOM
    occupied_locations: Set[str] = set()
    for l, stack in spec.stacks.items():
        if not stack:
            continue
        occupied_locations.add(l)

        # box-at for all boxes in this stack
        for b in stack:
            facts.append(atom("box-at", b, l))

        # on relations: t0 on t1, ..., tk on location
        if len(stack) == 1:
            # single box is both top and bottom
            facts.append(atom("on", stack[0], l))
            facts.append(atom("clear", stack[0]))
        else:
            for i in range(0, len(stack) - 1):
                facts.append(atom("on", stack[i], stack[i+1]))
            facts.append(atom("on", stack[-1], l))
            facts.append(atom("clear", stack[0]))  # top is clear

    # empty locations are clear
    for l in spec.locations:
        if l not in occupied_locations:
            facts.append(atom("clear", l))

    return facts


def goal_formula(spec: ProblemSpec) -> str:
    atoms = [atom("on", top, support) for (top, support) in spec.goal_on]
    atoms.extend([atom("box-at", box, location) for (box, location) in spec.goal_at])
    atoms.extend([atom("clear", name) for name in spec.goal_clear])
    if spec.goal_pddl:
        atoms.append(spec.goal_pddl)
    return and_formula(atoms)


def emit_pddl_problem(spec: ProblemSpec) -> str:
    # deterministic object ordering:
    locs = spec.locations[:] if isinstance(spec.locations, list) else sorted(spec.locations)
    boxes = spec.boxes[:] if isinstance(spec.boxes, list) else sorted(spec.boxes)

    objects_str = " ".join(boxes) + " - box\n          " + " ".join(locs) + " - location"

    init = init_facts(spec)
    init_sorted = sorted(init)  # stable across runs; remove if you prefer input ordering

    lines: List[str] = []
    lines.append(f"(define (problem {spec.problem_name})")
    lines.append("  (:domain BOX-WORLD)")
    lines.append(f"  (:objects {objects_str})")
    lines.append("  (:init")
    for f in init_sorted:
        lines.append(f"    {f}")
    lines.append("  )")
    lines.append(f"  (:goal {goal_formula(spec)})")
    lines.append(")")
    return "\n".join(lines)


# -----------------------------
# CLI
# -----------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Convert Box-World JSON (v1) to a BOX-WORLD PDDL problem instance."
    )
    ap.add_argument("json_path", help="Path to the JSON instance file")
    ap.add_argument("-o", "--out", help="Output .pddl path (default: stdout)")
    args = ap.parse_args()

    spec = load_ProblemSpec(args.json_path)
    pddl = emit_pddl_problem(spec)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(pddl + "\n")
    else:
        print(pddl)


if __name__ == "__main__":
    main()
