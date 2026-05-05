import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PATHS = [ROOT / "main.pyw", *sorted((ROOT / "app").glob("**/*.py"))]


class Pep604AnnotationVisitor(ast.NodeVisitor):
    def __init__(self):
        self.violations = []

    def visit_AnnAssign(self, node):
        self._check_annotation(node.annotation)
        self.generic_visit(node)

    def visit_arg(self, node):
        if node.annotation is not None:
            self._check_annotation(node.annotation)
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        if node.returns is not None:
            self._check_annotation(node.returns)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):
        if node.returns is not None:
            self._check_annotation(node.returns)
        self.generic_visit(node)

    def _check_annotation(self, node):
        for child in ast.walk(node):
            if isinstance(child, ast.BinOp) and isinstance(child.op, ast.BitOr):
                self.violations.append(child.lineno)


def pep604_annotation_lines(source):
    tree = ast.parse(source)
    visitor = Pep604AnnotationVisitor()
    visitor.visit(tree)
    return visitor.violations


class PythonCompatibilityTests(unittest.TestCase):
    def test_runtime_files_do_not_use_pep604_annotations(self):
        violations = []
        for path in RUNTIME_PATHS:
            lines = pep604_annotation_lines(path.read_text(encoding="utf-8"))
            for line in lines:
                violations.append(f"{path.relative_to(ROOT)}:{line}")

        self.assertEqual([], violations, "Python 3.8 runtime contract forbids PEP 604 annotations")

    def test_pep604_annotation_detector_catches_annotations(self):
        lines = pep604_annotation_lines("field: str | None = None\n")

        self.assertEqual([1], lines)

    def test_pep604_annotation_detector_ignores_bitwise_or_expressions(self):
        lines = pep604_annotation_lines("value = MOD_CTRL | MOD_SHIFT\n")

        self.assertEqual([], lines)


if __name__ == "__main__":
    unittest.main()
