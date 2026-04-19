from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Set, Union


Expr = Union[str, List["Expr"]]


class PDDLFormulaError(ValueError):
    """Raised when a raw PDDL goal formula is malformed or invalid."""


@dataclass(frozen=True)
class PredicateSignature:
    name: str
    arg_types: Sequence[str]


BOX_WORLD_PREDICATES: Dict[str, PredicateSignature] = {
    "holding": PredicateSignature("holding", ("box",)),
    "hands-empty": PredicateSignature("hands-empty", ()),
    "robot-at": PredicateSignature("robot-at", ("location",)),
    "box-at": PredicateSignature("box-at", ("box", "location")),
    "forbidden-stack": PredicateSignature("forbidden-stack", ("box", "box")),
    "on": PredicateSignature("on", ("box", "object")),
    "clear": PredicateSignature("clear", ("object",)),
    "black": PredicateSignature("black", ("object",)),
    "white": PredicateSignature("white", ("object",)),
}

LOGICAL_FORMS = {"and", "or", "not", "exists", "forall", "="}
VALID_TYPES = {"object", "box", "location"}


def validate_goal_formula(
    text: str,
    *,
    boxes: Iterable[str],
    locations: Iterable[str],
    predicates: Dict[str, PredicateSignature] | None = None,
) -> None:
    """Parse and validate a BOX-WORLD PDDL goal formula."""
    expr = parse_formula(text)
    context = _ValidationContext(
        boxes=set(boxes),
        locations=set(locations),
        predicates=predicates or BOX_WORLD_PREDICATES,
    )
    _validate_formula(expr, context, {})


def parse_formula(text: str) -> Expr:
    tokens = _tokenize(text)
    if not tokens:
        raise PDDLFormulaError("formula is empty")

    expr, next_index = _parse_expr(tokens, 0)
    if next_index != len(tokens):
        raise PDDLFormulaError(f"unexpected token after formula: {tokens[next_index]!r}")
    if not isinstance(expr, list):
        raise PDDLFormulaError("formula must be a parenthesized expression")
    return expr


def _tokenize(text: str) -> List[str]:
    tokens: List[str] = []
    current: List[str] = []
    in_comment = False

    def flush_current() -> None:
        if current:
            tokens.append("".join(current))
            current.clear()

    for char in text:
        if in_comment:
            if char == "\n":
                in_comment = False
            continue

        if char == ";":
            flush_current()
            in_comment = True
            continue

        if char.isspace():
            flush_current()
            continue

        if char in "()":
            flush_current()
            tokens.append(char)
            continue

        if char in {'"', "'"}:
            raise PDDLFormulaError("quoted strings are not supported in goal formulas")

        current.append(char)

    flush_current()
    return tokens


def _parse_expr(tokens: Sequence[str], index: int) -> tuple[Expr, int]:
    if index >= len(tokens):
        raise PDDLFormulaError("unexpected end of formula")

    token = tokens[index]
    if token == "(":
        items: List[Expr] = []
        index += 1
        while index < len(tokens) and tokens[index] != ")":
            item, index = _parse_expr(tokens, index)
            items.append(item)
        if index >= len(tokens):
            raise PDDLFormulaError("unbalanced parentheses: missing ')'")
        if not items:
            raise PDDLFormulaError("empty expressions are not supported")
        return items, index + 1

    if token == ")":
        raise PDDLFormulaError("unbalanced parentheses: unexpected ')'")

    return token, index + 1


@dataclass
class _ValidationContext:
    boxes: Set[str]
    locations: Set[str]
    predicates: Dict[str, PredicateSignature]


def _validate_formula(expr: Expr, context: _ValidationContext, env: Dict[str, str]) -> None:
    if not isinstance(expr, list):
        raise PDDLFormulaError(f"expected formula, got atom {expr!r}")

    head = _expect_symbol(expr[0], "formula head")

    if head == "and":
        for child in expr[1:]:
            _validate_formula(child, context, env)
        return

    if head == "or":
        if len(expr) == 1:
            raise PDDLFormulaError("'or' requires at least one child formula")
        for child in expr[1:]:
            _validate_formula(child, context, env)
        return

    if head == "not":
        if len(expr) != 2:
            raise PDDLFormulaError("'not' requires exactly one child formula")
        _validate_formula(expr[1], context, env)
        return

    if head in {"exists", "forall"}:
        if len(expr) != 3:
            raise PDDLFormulaError(f"'{head}' requires a variable list and one child formula")
        var_env = _parse_typed_variables(expr[1], env)
        child_env = env.copy()
        child_env.update(var_env)
        _validate_formula(expr[2], context, child_env)
        return

    if head == "=":
        if len(expr) != 3:
            raise PDDLFormulaError("'=' requires exactly two terms")
        _term_type(expr[1], context, env)
        _term_type(expr[2], context, env)
        return

    _validate_predicate_atom(head, expr[1:], context, env)


def _validate_predicate_atom(
    predicate_name: str,
    args: Sequence[Expr],
    context: _ValidationContext,
    env: Dict[str, str],
) -> None:
    if predicate_name in LOGICAL_FORMS:
        raise PDDLFormulaError(f"{predicate_name!r} cannot be used as a predicate")

    signature = context.predicates.get(predicate_name)
    if signature is None:
        raise PDDLFormulaError(f"unknown predicate {predicate_name!r}")

    if len(args) != len(signature.arg_types):
        raise PDDLFormulaError(
            f"predicate {predicate_name!r} expects {len(signature.arg_types)} arguments, got {len(args)}"
        )

    for arg, expected_type in zip(args, signature.arg_types):
        actual_type = _term_type(arg, context, env)
        if not _is_subtype(actual_type, expected_type):
            raise PDDLFormulaError(
                f"predicate {predicate_name!r} expects {expected_type}, got {actual_type} term {_format_expr(arg)!r}"
            )


def _parse_typed_variables(expr: Expr, outer_env: Dict[str, str]) -> Dict[str, str]:
    if not isinstance(expr, list):
        raise PDDLFormulaError("quantifier variable declaration must be a parenthesized list")

    symbols = [_expect_symbol(item, "quantifier variable declaration") for item in expr]
    if not symbols:
        raise PDDLFormulaError("quantifier variable declaration cannot be empty")

    env: Dict[str, str] = {}
    pending_vars: List[str] = []
    index = 0

    while index < len(symbols):
        symbol = symbols[index]
        if symbol == "-":
            if not pending_vars:
                raise PDDLFormulaError("'-' in quantifier declaration must follow one or more variables")
            if index + 1 >= len(symbols):
                raise PDDLFormulaError("missing type after '-' in quantifier declaration")
            type_name = symbols[index + 1]
            if type_name not in VALID_TYPES:
                raise PDDLFormulaError(f"unknown quantifier type {type_name!r}")
            for variable in pending_vars:
                if variable in env or variable in outer_env:
                    raise PDDLFormulaError(f"duplicate variable declaration {variable!r}")
                env[variable] = type_name
            pending_vars = []
            index += 2
            continue

        if not _is_variable(symbol):
            raise PDDLFormulaError(f"expected variable in quantifier declaration, got {symbol!r}")
        pending_vars.append(symbol)
        index += 1

    if pending_vars:
        raise PDDLFormulaError(f"missing type for variable(s): {', '.join(pending_vars)}")

    return env


def _term_type(expr: Expr, context: _ValidationContext, env: Dict[str, str]) -> str:
    symbol = _expect_symbol(expr, "term")
    if _is_variable(symbol):
        type_name = env.get(symbol)
        if type_name is None:
            raise PDDLFormulaError(f"unbound variable {symbol!r}")
        return type_name

    if symbol in context.boxes:
        return "box"
    if symbol in context.locations:
        return "location"

    raise PDDLFormulaError(f"unknown object {symbol!r}")


def _expect_symbol(expr: Expr, context: str) -> str:
    if not isinstance(expr, str):
        raise PDDLFormulaError(f"expected symbol for {context}, got {_format_expr(expr)!r}")
    return expr


def _is_variable(symbol: str) -> bool:
    return symbol.startswith("?") and len(symbol) > 1


def _is_subtype(actual_type: str, expected_type: str) -> bool:
    if actual_type == expected_type:
        return True
    return expected_type == "object" and actual_type in {"box", "location"}


def _format_expr(expr: Expr) -> str:
    if isinstance(expr, str):
        return expr
    return "(" + " ".join(_format_expr(item) for item in expr) + ")"
