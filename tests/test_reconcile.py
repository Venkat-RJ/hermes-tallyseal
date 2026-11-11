import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from reconcile import CheckError, check, seal

DEMO = Path(__file__).resolve().parents[1] / "demo"


class ReconciliationTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "source.csv"
        self.report = self.root / "report.csv"
        self.source.write_bytes((DEMO / "source.csv").read_bytes())
        self.report.write_bytes((DEMO / "correct.csv").read_bytes())
        self.pin = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def check(self, groups=None):
        return check(str(self.root), self.pin, groups or ["team"], "amount_minor")

    def test_balanced_errors_are_detected_and_localized(self):
        self.report.write_bytes((DEMO / "balanced-but-wrong.csv").read_bytes())
        result, _ = self.check()
        self.assertEqual(result["status"], "fail")
        self.assertTrue(result["grand_total_matches"])
        self.assertTrue(result["total_row_count_matches"])
        self.assertEqual(result["issues"], [
            {"kind": "total_mismatch", "report_record": 2, "first_source_record": 2},
            {"kind": "total_mismatch", "report_record": 3, "first_source_record": 4},
        ])
        self.assertNotIn("Atlas", str(result))

    def test_duplicate_missing_and_unexpected_groups(self):
        self.report.write_text("team,row_count,total_minor\nAtlas,2,10000\nAtlas,2,6000\nOther,2,4000\n")
        result, _ = self.check()
        self.assertEqual([x["kind"] for x in result["issues"]],
                         ["duplicate_group", "unexpected_group", "missing_group", "missing_group"])
        self.assertTrue(result["grand_total_matches"])

    def test_pass_preserves_exact_bytes_even_when_draft_changes(self):
        result, snapshot = self.check()
        self.assertEqual(result["status"], "pass")
        self.report.write_text("replacement")
        out = self.root / "sealed.csv"
        seal(snapshot, out)
        self.assertEqual(out.read_bytes(), (DEMO / "correct.csv").read_bytes())
        self.assertEqual(hashlib.sha256(out.read_bytes()).hexdigest(), result["report_sha256"])
        with self.assertRaisesRegex(CheckError, "already exists"):
            seal(b"replacement", out)
        self.assertEqual(out.read_bytes(), snapshot)

    def test_source_tamper_blocks_even_if_report_would_match(self):
        self.source.write_bytes(self.source.read_bytes() + b"\n")
        with self.assertRaisesRegex(CheckError, "pinned SHA-256"):
            self.check()

    def test_symlink_and_fifo_are_rejected(self):
        self.report.unlink()
        self.report.symlink_to(DEMO / "correct.csv")
        with self.assertRaisesRegex(CheckError, "opened safely"):
            self.check()
        self.report.unlink()
        os.mkfifo(self.report)
        with self.assertRaisesRegex(CheckError, "regular file"):
            self.check()

    def test_composite_groups_exact_large_totals_and_reordered_headers(self):
        n = 10**24 - 1
        self.source.write_text(f"team,city,amount_minor\nA,\"New,York\",{n}\nA,\"New,York\",{n}\nA,LA,-3\n")
        self.pin = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.report.write_text(f"row_count,city,team,total_minor\n1,LA,A,-3\n2,\"New,York\",A,{2*n}\n")
        result, _ = self.check(["team", "city"])
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["expected_groups"], 2)

    def test_count_is_checked_even_when_amounts_match(self):
        self.report.write_text("team,row_count,total_minor\nAtlas,1,10000\nBoreal,3,6000\nCedar,2,4000\n")
        result, _ = self.check()
        self.assertTrue(result["total_row_count_matches"])
        self.assertEqual([x["kind"] for x in result["issues"]],
                         ["row_count_mismatch", "row_count_mismatch"])

    def test_malformed_report_fails_closed(self):
        for report in (
            "team,row_count,total_minor\nAtlas,2,1e4\n",
            "team,row_count,total_minor,total_minor\nAtlas,2,10000,0\n",
            "team,row_count,total_minor\n\"unterminated,2,10000\n",
            "team,row_count,total_minor\nAtlas,0,10000\n",
            "team,row_count,total_minor,notes\nAtlas,2,10000,extra\n",
        ):
            with self.subTest(report=report):
                self.report.write_text(report)
                with self.assertRaises(CheckError):
                    self.check()


if __name__ == "__main__":
    unittest.main()
