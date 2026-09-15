"""Class-budget guard (design #162 §8, design-decisions #170 §8; engram #238).

Enforces the design's simplicity budget by parsing ``src/semlog/*.py`` with
``ast`` only -- this module never imports production code. The budget: at
most 6 classes in the whole package, each either one of the design's four
concretely-named classes or a direct subclass of stdlib's
``logging.handlers.QueueHandler``/``QueueListener``; zero ABC/ABCMeta/
Protocol bases; zero class-factory calls (``collections.namedtuple``,
``typing.NamedTuple``, or ``type()`` called with three positional
arguments).

STANDARDS.md CP-017 now states this exact "6 classes, 0 abstract classes,
0 class-factory calls" budget (Phase 2 source-budget conformance refactor,
engram #239's follow-up); this class shape and the budget concept were
stated in design #162/#170 §8 all along, though the line-count number
itself has since moved on its own (1000 in that design, 1500 now in
STANDARDS.md; see tests/test_source_budget.py for the current figure). This module
therefore deliberately carries no ``Proves:`` line: CP-017's own text
requires the whole budget to be "published, and enforced in continuous
integration" (published AND enforced in CI), and no CI workflow exists yet
(Phase 4, tasks 4.5/4.6). Citing CP-017 here now, before that clause is
provable, would trip the traceability checker's stale-allowlist ratchet
(``tests/test_traceability.py``'s ``NOT_YET_PROVEN``) on a requirement
that is only partly proven -- the same treatment ``tests/test_namespace.py``'s
``ClosedAttributeGroupTests`` gave LRC-013 before its completion, and
``tests/test_pipeline_order.py`` still gives CP-016.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "src" / "semlog"

MAX_CLASSES = 6
ALLOWED_NAMES = frozenset({"Formatter", "Snapshot", "WSGIMiddleware", "ASGIMiddleware"})
ALLOWED_SUBCLASS_BASES = frozenset({"QueueHandler", "QueueListener"})
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


def _is_allowed(node):
    """A class is allowed when it is one of the design's four concretely
    named classes, or directly subclasses ``QueueHandler``/``QueueListener``
    (task 2.34/2.36, not written yet)."""
    if node.name in ALLOWED_NAMES:
        return True
    return any(_name_of(base) in ALLOWED_SUBCLASS_BASES for base in node.bases)


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
    """Design #162 §8 / design-decisions #170 §8 (engram #238): at most 6
    classes in `src/semlog`, each design-named or a direct QueueHandler/
    QueueListener subclass, zero ABC/ABCMeta/Protocol bases, zero
    class-factory calls. Parses source with `ast` only; never imports
    production code (a class-budget violation must not need a working
    import to be caught)."""

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

    def test_at_most_six_classes_total(self):
        found = [f"{fname}:{node.name}" for fname, node in self.classdefs]
        self.assertLessEqual(
            len(self.classdefs),
            MAX_CLASSES,
            f"{len(self.classdefs)} classes defined, budget is "
            f"{MAX_CLASSES}: {', '.join(found)}",
        )

    def test_every_class_is_design_named_or_a_transport_subclass(self):
        disallowed = [
            f"{fname}:{node.name}"
            for fname, node in self.classdefs
            if not _is_allowed(node)
        ]
        self.assertEqual(
            [],
            disallowed,
            "class(es) outside the design's 6-class budget (Formatter, "
            "Snapshot, WSGIMiddleware, ASGIMiddleware, or a direct "
            "QueueHandler/QueueListener subclass)",
        )

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
