import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pddl_formula import PDDLFormulaError, validate_goal_formula


BOXES = {"B1", "B2", "B3"}
LOCATIONS = {"L1", "L2", "L3"}


class PDDLFormulaValidationTests(unittest.TestCase):
    def assert_valid(self, formula: str) -> None:
        validate_goal_formula(formula, boxes=BOXES, locations=LOCATIONS)

    def assert_invalid(self, formula: str, message: str) -> None:
        with self.assertRaisesRegex(PDDLFormulaError, message):
            validate_goal_formula(formula, boxes=BOXES, locations=LOCATIONS)

    def test_valid_atomic_formula(self) -> None:
        self.assert_valid("(robot-at L1)")

    def test_valid_nested_formula(self) -> None:
        self.assert_valid("(and (clear B1) (box-at B1 L2) (not (holding B2)))")

    def test_valid_quantified_formula(self) -> None:
        self.assert_valid("(exists (?x - box) (clear ?x))")

    def test_valid_grouped_typed_variables(self) -> None:
        self.assert_valid(
            "(exists (?l1 ?l2 - location) "
            "(and (clear ?l1) (clear ?l2) (not (= ?l1 ?l2))))"
        )

    def test_rejects_unbalanced_parentheses(self) -> None:
        self.assert_invalid("(and (clear B1))", "unbalanced")

    def test_rejects_unknown_predicate(self) -> None:
        self.assert_invalid("(near B1 L1)", "unknown predicate")

    def test_rejects_wrong_arity(self) -> None:
        self.assert_invalid("(box-at B1)", "expects 2 arguments")

    def test_rejects_unknown_object(self) -> None:
        self.assert_invalid("(robot-at L9)", "unknown object")

    def test_rejects_wrong_type(self) -> None:
        self.assert_invalid("(robot-at B1)", "expects location")

    def test_rejects_unbound_variable(self) -> None:
        self.assert_invalid("(clear ?x)", "unbound variable")

    def test_rejects_unknown_quantifier_type(self) -> None:
        self.assert_invalid("(exists (?x - crate) (clear ?x))", "unknown quantifier type")

    def test_rejects_malformed_quantifier_declaration(self) -> None:
        self.assert_invalid("(exists (?x) (clear ?x))", "missing type")


class JSONIntegrationTests(unittest.TestCase):
    def test_convert_rejects_invalid_goal_pddl(self) -> None:
        problem = {
            "problem_name": "bad-formula",
            "locations": ["L1"],
            "boxes": ["B1"],
            "initial_state": {
                "robot_at": "L1",
                "stacks": {"L1": ["B1"]},
            },
            "goal": {
                "pddl": ["(robot-at B1)"],
            },
        }

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(problem, f)
            path = f.name

        try:
            result = subprocess.run(
                [sys.executable, str(ROOT / "json-to-pddl.py"), "convert", path],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
        finally:
            Path(path).unlink(missing_ok=True)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid goal.pddl[0]", result.stderr)
        self.assertIn("expects location", result.stderr)


if __name__ == "__main__":
    unittest.main()
