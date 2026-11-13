import os
import sys
import unittest

_BACKEND_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, os.pardir)
)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from app.routes.participant.participant import _build_cp_quick_search_clause


class TestCpQuickSearchClause(unittest.TestCase):
    def test_blank_returns_none(self):
        self.assertEqual(_build_cp_quick_search_clause(""), (None, []))
        self.assertEqual(_build_cp_quick_search_clause("   "), (None, []))

    def test_includes_connectto_ipport(self):
        clause, params = _build_cp_quick_search_clause("10.1.2")
        self.assertIsNotNone(clause)
        self.assertIn("cp.connectto_ipport", clause)
        self.assertIn("cp.id", clause)
        self.assertIn("cp.name", clause)
        self.assertIn("cp.comments", clause)
        self.assertIn("cp.in_next_plugin", clause)
        self.assertIn("cp.out_next_plugin", clause)
        self.assertEqual(params, ["%10.1.2%"] * 6)
        self.assertEqual(clause.count("%s"), 6)


if __name__ == "__main__":
    unittest.main()
