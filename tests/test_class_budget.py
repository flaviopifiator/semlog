"""Class-shape guard (design #162 §8, design-decisions #170 §8; engram #238;
STANDARDS.md CP-017).

Enforces CP-017's surviving rules by parsing ``src/semlog/*.py`` with
``ast`` only -- this module never imports production code: zero ABC/
ABCMeta/Protocol bases; zero class-factory calls (``collections.namedtuple``,
``typing.NamedTuple``, or ``type()`` called with three positional
arguments). CP-017's numeric budgets (a maximum source-line count and a
maximum class count) were removed by decision (framework-middlewares
change, decisions #345/#347): the maintainer judged an idiomatic Django
`MIDDLEWARE` class more valuable than a fixed ceiling, and the no-ABC/
no-class-factory rules keep the type surface auditable without capping
its size. The class-count-specific cases this module used to carry
(``test_at_most_six_classes_total``, ``test_every_class_is_design_named_
or_a_transport_subclass``) are deleted along with the ``MAX_CLASSES``/
``ALLOWED_NAMES`` constants that backed them; ``tests/test_source_budget.py``,
which enforced the removed line-count budget, is deleted entirely.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "src" / "semlog"

DISALLOWED_BASES = frozenset({"ABC", "ABCMeta", "Protocol"})
FACTORY_CALL_NAMES = frozenset({"namedtuple", "NamedTuple"})


def _name_of(node):
    """Return the trailing name of an ``ast.Name``/``ast.Attribute`` node,
    or ``None`` for any other expression shape."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _iter_source_files(package_dir):
    return sorted(package_dir.glob("*.py"))


def _iter_classdefs(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            yield node


def _iter_calls(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node


def _has_disallowed_base(node):
    if any(_name_of(base) in DISALLOWED_BASES for base in node.bases):
        return True
    for keyword in node.keywords:
        if keyword.arg == "metaclass" and _name_of(keyword.value) == "ABCMeta":
            return True
    return False


def _is_factory_call(call):
    name = _name_of(call.func)
    if name in FACTORY_CALL_NAMES:
        return True
    return bool(name == "type" and len(call.args) == 3)


class ClassBudgetTests(unittest.TestCase):
    """Proves: CP-017

    Zero ABC/ABCMeta/Protocol bases, zero class-factory calls, in every
    `src/semlog` source file. Parses source with `ast` only; never
    imports production code (a violation must not need a working import
    to be caught)."""

    @classmethod
    def setUpClass(cls):
        cls.classdefs = []
        cls.calls = []
        for path in _iter_source_files(PACKAGE_DIR):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in _iter_classdefs(tree):
                cls.classdefs.append((path.name, node))
            for call in _iter_calls(tree):
                cls.calls.append((path.name, call))

    def test_no_abc_or_protocol_bases(self):
        offenders = [
            f"{fname}:{node.name}"
            for fname, node in self.classdefs
            if _has_disallowed_base(node)
        ]
        self.assertEqual([], offenders, "class(es) with an ABC/ABCMeta/Protocol base")

    def test_no_class_factory_calls(self):
        offenders = [
            f"{fname}:L{call.lineno}"
            for fname, call in self.calls
            if _is_factory_call(call)
        ]
        self.assertEqual(
            [],
            offenders,
            "class-factory call(s) (namedtuple/NamedTuple/type() with 3 "
            "positional arguments)",
        )


if __name__ == "__main__":
    unittest.main()
