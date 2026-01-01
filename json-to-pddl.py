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
import os
import re
import glob
import shutil
import tempfile
import subprocess

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
    #             "clear": [box_or_location, ...] 
    #             "pddl": [pddl_atom, ...] }
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
        goal_pddl_raw = []
    if not isinstance(goal_pddl_raw, list):
        raise ValueError('goal.pddl must be a list of strings, ech a PDDL formula')
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
    atoms.extend(spec.goal_pddl)
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

def parse_action_atom(line: str) -> Dict[str, List[str]]:
    """
    Convert a PDDL action atom like:
        "(unstack b1 l1)"
    into:
        {"unstack": ["b1", "l1"]}
    """
    line = line.strip()

    if not (line.startswith("(") and line.endswith(")")):
        raise ValueError(f"Not a valid PDDL atom: {line}")

    # Remove outer parentheses
    inner = line[1:-1].strip()

    if not inner:
        raise ValueError(f"Empty PDDL atom: {line}")

    tokens = inner.split()
    action = tokens[0]
    args = tokens[1:]

    return {action: args}

def parse_fd_plan_text(plan_text: str) -> Tuple[List[str], Optional[int]]:
    """
    Parse a Fast Downward plan file content.
    Returns (steps, cost). Cost may be None if not found.
    """
    steps: List[str] = []
    cost: Optional[int] = None

    # Typical FD plan lines:
    # (move b1 l1 l2)
    # (pickup b1 l2)
    # ; cost = 12 (unit cost)
    for raw in plan_text.splitlines():
        line = raw.strip()
        if not line:
            continue

        # comment lines
        if line.startswith(";"):
            m = re.search(r"cost\s*=\s*([0-9]+)", line)
            if m:
                cost = int(m.group(1))
            continue

        # action lines
        if line.startswith("(") and line.endswith(")"):
            steps.append(parse_action_atom(line))
        else:
            # If your planner outputs non-parenthesized actions, you can decide:
            # steps.append(line)
            # For now, ignore unexpected lines.
            pass

    return steps, cost

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Convert Box-World JSON (v1) to PDDL, and optionally run an external planner."
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    # -------------------------
    # convert subcommand
    # -------------------------
    ap_convert = sub.add_parser("convert", help="Convert JSON instance to a PDDL problem file.")
    ap_convert.add_argument("json_path", help="Path to the JSON instance file")
    ap_convert.add_argument("-o", "--out", help="Output .pddl path (default: stdout)")

    # -------------------------
    # solve subcommand
    # -------------------------
    ap_solve = sub.add_parser("solve", help="Generate PDDL problem and call an external planner; return best plan.")
    ap_solve.add_argument("json_path", help="Path to the JSON instance file")
    ap_solve.add_argument(
        "--domain",
        default="./box-world-domain.pddl",
        help="Path to the PDDL domain file (default: box-world-domain.pddl).",
    )
    ap_solve.add_argument(
        "--planner",
        default="./fast-downward-24.06.1/fast-downward.py",
        help="Path to the FastDownward planner executable (default: ./fast-downward-24.06.1/fast-downward.py).",
    )
    ap_solve.add_argument(
        "--planner-args",
        nargs=argparse.REMAINDER,
        default=["--alias", "seq-sat-lama-2011"],
        help="Extra arguments passed to the planner (everything after --planner-args).",
    )

    # How to tell the planner where to write plan files.
    # Default assumes planner supports: --plan-file <base>
    ap_solve.add_argument(
        "--planner-plan-flag",
        default="--plan-file",
        help='Planner flag used to set the plan output basename (default: "--plan-file").',
    )

    # Optional: keep the generated problem file (and plan files) for debugging
    ap_solve.add_argument(
        "--keep-tmp",
        action="store_true",
        help="Keep temporary directory with generated PDDL + planner outputs.",
    )
    ap_solve.add_argument(
        "--problem-out",
        default=None,
        help="Optional path to write the generated PDDL problem (in addition to running planner).",
    )

    # Final output for the best plan
    ap_solve.add_argument(
        "--plan-json-out",
        default=None,
        help="Write best plan in json format to this path. If omitted, prints plan to stdout.",
    )

    args = ap.parse_args()

    # -------------------------
    # convert mode
    # -------------------------
    if args.cmd == "convert":
        spec = load_ProblemSpec(args.json_path)
        pddl = emit_pddl_problem(spec)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(pddl + "\n")
        else:
            print(pddl)
        return

    # -------------------------
    # solve mode
    # -------------------------
    if args.cmd == "solve":
        # 1) Generate problem PDDL text
        spec = load_ProblemSpec(args.json_path)
        problem_pddl = emit_pddl_problem(spec)

        # 2) Use a temp dir unless keep-tmp is requested (then we still use temp dir but don't delete)
        tmp_ctx = tempfile.TemporaryDirectory()
        tmpdir = tmp_ctx.name

        try:
            # Write problem to temp file
            problem_path = os.path.join(tmpdir, f"{spec.problem_name}.pddl")
            with open(problem_path, "w", encoding="utf-8") as f:
                f.write(problem_pddl + "\n")

            # Optionally also write problem to a user-specified path
            if args.problem_out:
                with open(args.problem_out, "w", encoding="utf-8") as f:
                    f.write(problem_pddl + "\n")

            # Plan basename in temp dir (planner will create plan.1, plan.2, ...)
            plan_base = os.path.join(tmpdir, "plan")

            # 3) Build planner command
            cmd = [args.planner]
            cmd.extend(args.planner_args)
            cmd.extend([
                args.planner_plan_flag,
                plan_base,
                args.domain,
                problem_path,
            ])

            # 4) Run planner
            res = subprocess.run(cmd, capture_output=False, text=True)
            if res.returncode != 0:
                # Surface planner stderr/stdout for debugging
                print("return code:", res.returncode)
                raise RuntimeError(
                    "Planner failed.\n"
                    f"Command: {' '.join(cmd)}\n\n"
                    f"STDOUT:\n{res.stdout}\n\n"
                    f"STDERR:\n{res.stderr}\n"
                )

            # 5) Find best plan file plan.N with largest N
            plan_candidates = glob.glob(plan_base + ".*")
            best_path = None
            best_n = None

            for p in plan_candidates:
                m = re.match(r".*\.(\d+)$", p)
                if not m:
                    continue
                n = int(m.group(1))
                if best_n is None or n > best_n:
                    best_n = n
                    best_path = p

            if best_path is None:
                raise RuntimeError(
                    "Planner succeeded but no plan files were found.\n"
                    f"Looked for files matching: {plan_base}.*\n"
                    f"STDOUT:\n{res.stdout}\n\n"
                    f"STDERR:\n{res.stderr}\n"
                )

            # 6) Return best plan: write to file or print
            with open(best_path, "r", encoding="utf-8") as f:
                best_plan_text = f.read()

            steps, cost = parse_fd_plan_text(best_plan_text)

            plan_json_obj = {
                "plan": steps,
                "cost": cost,
            }

            plan_json_str = json.dumps(plan_json_obj, indent=2)

            if args.plan_json_out:
                # Ensure destination directory exists
                out_dir = os.path.dirname(args.plan_json_out)
                if out_dir:
                    os.makedirs(out_dir, exist_ok=True)
                with open(args.plan_json_out, "w", encoding="utf-8") as f:
                    f.write(plan_json_str + "\n")
            else:
                print(plan_json_str)

            # If keeping tmp, tell user where it is (useful for debugging)
            if args.keep_tmp:
                # Keep temp dir by preventing cleanup below
                print(f"\n[kept tmp dir] {tmpdir}")
                tmp_ctx.cleanup = lambda: None  # type: ignore

        finally:
            # Cleanup temp directory unless keep-tmp was requested and we disabled cleanup
            tmp_ctx.cleanup()
        return

# def main() -> None:
#     ap = argparse.ArgumentParser(
#         description="Convert Box-World JSON (v1) to a BOX-WORLD PDDL problem instance."
#     )
#     ap.add_argument("json_path", help="Path to the JSON instance file")
#     ap.add_argument("-o", "--out", help="Output .pddl path (default: stdout)")
#     args = ap.parse_args()

#     spec = load_ProblemSpec(args.json_path)
#     pddl = emit_pddl_problem(spec)

#     if args.out:
#         with open(args.out, "w", encoding="utf-8") as f:
#             f.write(pddl + "\n")
#     else:
#         print(pddl)


if __name__ == "__main__":
    main()
