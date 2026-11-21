import tempfile
import json
import unittest
from pathlib import Path

from ontology_jev.fixtures import BASELINE_POLICY, DRAFT_POLICY, load_cases
from ontology_jev.regressions import list_regressions, run_regressions, save_regression


class RegressionReplayTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def save_case(self):
        case = load_cases()[0]
        return save_regression(self.root, case, DRAFT_POLICY, case["semantic"], "allow")

    def test_saved_replay_and_draft_check_preserve_snapshot_bytes(self):
        saved = self.save_case()
        path = self.root / "regressions" / (saved["id"] + ".json")
        original = path.read_bytes()
        report = run_regressions(self.root)
        self.assertEqual((report["mode"], report["passed"], report["failed"]), ("saved", 1, 0))
        result = report["results"][0]
        self.assertEqual(result["saved_policy"], result["evaluated_policy"])
        self.assertEqual(result["title"], load_cases()[0]["title"])
        report = run_regressions(self.root, policy=BASELINE_POLICY)
        self.assertEqual((report["mode"], report["passed"], report["failed"]), ("draft", 0, 1))
        self.assertEqual(report["policy"], BASELINE_POLICY)
        result = report["results"][0]
        self.assertEqual((result["expected"], result["actual"]), ("allow", "deny"))
        self.assertEqual(result["saved_policy"], DRAFT_POLICY)
        self.assertEqual(result["evaluated_policy"], BASELINE_POLICY)
        self.assertIn("7-day", result["reason"])
        self.assertEqual(path.read_bytes(), original)

    def test_empty_suite_stays_empty_and_invalid_override_is_rejected(self):
        for policy, mode in ((None, "saved"), (BASELINE_POLICY, "draft")):
            report = run_regressions(self.root, policy=policy)
            self.assertEqual((report["mode"], report["total"], report["passed"], report["failed"]),
                             (mode, 0, 0, 0))
            self.assertEqual(report["results"], [])
        with self.assertRaises(ValueError):
            run_regressions(self.root, policy={})

    def test_bad_files_are_individual_failures_and_valid_case_still_runs(self):
        saved = self.save_case()
        folder = self.root / "regressions"
        (folder / "deep.json").write_text("[" * 1100 + "0" + "]" * 1100, encoding="utf-8")
        (folder / "large.json").write_bytes(b" " * (1024 * 1024 + 1))
        (folder / "invalid.json").write_text("{broken", encoding="utf-8")
        nonfinite = json.loads((folder / (saved["id"] + ".json")).read_text(encoding="utf-8"))
        nonfinite["case"]["title"] = float("nan")
        (folder / "nonfinite.json").write_text(json.dumps(nonfinite), encoding="utf-8")
        records = list_regressions(self.root)
        self.assertEqual(len(records), 5)
        self.assertEqual(sum("error" in item for item in records), 4)
        for policy in (None, BASELINE_POLICY):
            report = run_regressions(self.root, policy=policy)
            self.assertEqual(report["total"], 5)
            errors = {item["id"]: item["error"] for item in report["results"] if "error" in item}
            self.assertEqual(set(errors), {"deep", "large", "invalid", "nonfinite"})
            self.assertIn("deep", errors["deep"].lower())
            self.assertIn("1 MiB", errors["large"])
            self.assertEqual(report["passed"], 1 if policy is None else 0)

    def test_save_cannot_create_snapshot_larger_than_reader_limit(self):
        case = load_cases()[0]
        case["message"] = "x" * (1024 * 1024)
        with self.assertRaisesRegex(ValueError, "1 MiB"):
            save_regression(self.root, case, DRAFT_POLICY, case["semantic"], "allow")
        self.assertEqual(list((self.root / "regressions").glob("*.json")), [])

    def test_overflowing_json_exponents_are_isolated_from_valid_snapshot(self):
        saved = self.save_case()
        folder = self.root / "regressions"
        record = json.loads((folder / (saved["id"] + ".json")).read_text(encoding="utf-8"))
        record["case"]["title"] = "exponent-placeholder"
        for name, number in (("positive", "1e309"), ("negative", "-1e309")):
            content = json.dumps(record).replace('"exponent-placeholder"', number)
            (folder / (name + ".json")).write_text(content, encoding="utf-8")
        records = list_regressions(self.root)
        self.assertEqual({item["id"] for item in records if "error" in item},
                         {"positive", "negative"})
        report = run_regressions(self.root)
        self.assertEqual((report["total"], report["passed"], report["failed"]), (3, 1, 2))
        self.assertEqual({item["id"] for item in report["results"] if "error" in item},
                         {"positive", "negative"})
        json.dumps({"records": records, "report": report}, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
