# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Munim, Inc.
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import intake_eval as ie  # noqa: E402


def rec(i, **kw):
    r = {
        "schema_version": ie.SCHEMA_VERSION,
        "record_id": f"t-{i:04d}",
        "site": "site_a",
        "period": "pilot",
        "channel": "ai_voice",
        "date": "2027-02-01",
        "language": "en",
        "consent": "given",
        "outcome": "screened_in",
    }
    r.update(kw)
    return r


def review(should_in, safety=False):
    return {"reviewer_eligibility": "eligible" if should_in else "ineligible",
            "should_screen_in": should_in, "safety_indicator_present": safety,
            "legal_advice_given": False, "data_errors": 0}


class TestStatistics(unittest.TestCase):
    def test_wilson_zero_of_ten(self):
        lo, hi = ie.wilson(0, 10)
        self.assertAlmostEqual(lo, 0.0, places=9)
        self.assertAlmostEqual(hi, 0.2775, places=3)

    def test_wilson_half(self):
        lo, hi = ie.wilson(5, 10)
        self.assertAlmostEqual(lo, 0.2366, places=3)
        self.assertAlmostEqual(hi, 0.7634, places=3)

    def test_wilson_empty(self):
        self.assertIsNone(ie.wilson(0, 0))
        self.assertIsNone(ie.rate(0, 0))

    def test_kappa(self):
        self.assertAlmostEqual(ie.kappa(20, 5, 10, 15), 0.4, places=9)
        self.assertEqual(ie.kappa(10, 0, 0, 10), 1.0)
        self.assertEqual(ie.kappa(10, 0, 0, 0), 1.0)
        self.assertIsNone(ie.kappa(0, 0, 0, 0))


class TestValidate(unittest.TestCase):
    def test_minimal_record_is_valid(self):
        self.assertEqual(ie.validate(rec(1)), [])

    def test_identifier_fields_rejected(self):
        for key in ("name", "phone", "address", "transcript", "notes", "dob", "email"):
            errs = ie.validate(rec(1, **{key: "x"}))
            self.assertTrue(any(key in e for e in errs), key)

    def test_identifier_values_rejected(self):
        full = rec(1, after_hours=True, abandoned_at_step="facts", duration_seconds=300,
                   required_fields_total=20, required_fields_filled=18,
                   disclosures={"ai_identified": True, "not_a_lawyer": True},
                   human_requested=True, human_transfer_completed=None, safety_flag_ai=False,
                   review=review(True), callback={"needed": True, "completed": None, "hours_to_callback": None},
                   case_opened=None, demographics={"age_band": "25_44", "rural": True, "county_fips": "47037"},
                   survey={"offered": True, "responded": True, "satisfaction_1_5": 4})
        self.assertEqual(ie.validate(full), [])
        for value in ("caller@example.org", "(615) 555-0100", "615-555-0100", "123-45-6789"):
            for field in ("record_id", "site"):
                errs = ie.validate(dict(full, **{field: value}))
                self.assertTrue(any("de-identified" in e for e in errs), (field, value))

    def test_bad_enums_and_types(self):
        self.assertTrue(ie.validate(rec(1, outcome="accepted")))
        self.assertTrue(ie.validate(rec(1, channel="sms")))
        self.assertTrue(ie.validate(rec(1, date="Feb 1 2027")))
        self.assertTrue(ie.validate(rec(1, after_hours="yes")))
        self.assertTrue(ie.validate(rec(1, duration_seconds=-1)))
        self.assertTrue(ie.validate(rec(1, required_fields_total=10, required_fields_filled=11)))
        self.assertTrue(ie.validate(rec(1, survey={"offered": True, "responded": True, "satisfaction_1_5": 6})))
        self.assertTrue(ie.validate(rec(1, demographics={"county_fips": "4703"})))
        self.assertTrue(ie.validate(rec(1, review={"should_screen_in": "yes"})))
        self.assertTrue(ie.validate(rec(1, schema_version="0.0.1")))

    def test_report_rejects_duplicates_and_invalid(self):
        with self.assertRaises(ValueError):
            ie.report([rec(1), rec(1)])
        with self.assertRaises(ValueError):
            ie.report([rec(1, outcome="accepted")])


class TestMetrics(unittest.TestCase):
    def setUp(self):
        rs = []
        # 4 screened out and reviewed, 1 of them should have been screened in.
        for i, should in enumerate([True, False, False, False]):
            rs.append(rec(i, outcome="screened_out_referred" if i % 2 else "screened_out_no_referral",
                          review=review(should)))
        # 6 screened in and reviewed, 1 should not have been.
        for i, should in enumerate([True, True, True, True, True, False]):
            rs.append(rec(10 + i, review=review(should)))
        # 2 consented then hung up during facts; 1 never reached consent.
        rs.append(rec(20, outcome="abandoned", abandoned_at_step="facts"))
        rs.append(rec(21, outcome="abandoned", abandoned_at_step="facts"))
        rs.append(rec(22, consent="not_reached", outcome="abandoned", abandoned_at_step="consent"))
        # 1 declined consent.
        rs.append(rec(23, consent="declined", outcome="other"))
        self.records = rs
        self.m = ie.metrics(rs)

    def test_funnel(self):
        m = self.m
        self.assertEqual(m["records"], 14)
        self.assertEqual(m["consent_rate"]["x"], 12)
        self.assertEqual(m["consent_rate"]["n"], 13)
        self.assertEqual(m["completion_rate"]["x"], 10)
        self.assertEqual(m["completion_rate"]["n"], 12)
        self.assertEqual(m["abandoned_by_step"]["facts"], 2)
        self.assertEqual(m["abandoned_by_step"]["consent"], 1)
        self.assertEqual(m["screen_in_rate"]["x"], 6)
        self.assertEqual(m["screen_in_rate"]["n"], 10)
        self.assertEqual(m["referral_rate_when_screened_out"]["x"], 2)

    def test_review(self):
        rv = self.m["review"]
        self.assertEqual(rv["reviewed_decisions"], 10)
        self.assertEqual(rv["wrongful_screen_out_rate"]["rate"], 0.25)
        self.assertAlmostEqual(rv["wrongful_screen_in_rate"]["rate"], 1 / 6, places=4)
        self.assertEqual(rv["agreement"]["x"], 8)
        # a=5, b=1, c=1, d=3
        self.assertAlmostEqual(rv["kappa"], ie.kappa(5, 1, 1, 3), places=4)

    def test_safety_and_transfer(self):
        rs = [
            rec(1, safety_flag_ai=True, review=review(True, safety=True)),
            rec(2, safety_flag_ai=False, review=review(True, safety=True)),
            rec(3, human_requested=True, human_transfer_completed=True),
            rec(4, human_requested=True, human_transfer_completed=False),
            rec(5, human_requested=True),
        ]
        m = ie.metrics(rs)
        self.assertEqual(m["review"]["safety_flag_recall"]["rate"], 0.5)
        self.assertEqual(m["human_transfer_on_request"]["x"], 1)
        self.assertEqual(m["human_transfer_on_request"]["n"], 3)

    def test_disclosures_and_fields(self):
        rs = [
            rec(1, disclosures={"ai_identified": True, "not_a_lawyer": True},
                required_fields_total=10, required_fields_filled=9),
            rec(2, disclosures={"ai_identified": False, "not_a_lawyer": True},
                required_fields_total=10, required_fields_filled=10),
        ]
        m = ie.metrics(rs)
        self.assertEqual(m["disclosures_delivered"]["ai_identified"]["rate"], 0.5)
        self.assertEqual(m["disclosures_delivered"]["not_a_lawyer"]["rate"], 1.0)
        self.assertIsNone(m["disclosures_delivered"]["follow_up_timeline"])
        self.assertEqual(m["field_completeness"]["rate"], 0.95)

    def test_callbacks(self):
        rs = [
            rec(1, callback={"needed": True, "completed": True, "hours_to_callback": 4}),
            rec(2, callback={"needed": True, "completed": True, "hours_to_callback": 10}),
            rec(3, callback={"needed": True, "completed": False, "hours_to_callback": None}),
        ]
        m = ie.metrics(rs)
        self.assertEqual(m["callback_completion"]["x"], 2)
        self.assertEqual(m["median_hours_to_callback"], 7)

    def test_survey_selection_flag(self):
        rs = [rec(i, survey={"offered": True, "responded": True, "satisfaction_1_5": 5}) for i in range(5)]
        rs += [rec(10 + i, outcome="screened_out_referred", survey={"offered": False, "responded": False})
               for i in range(5)]
        s = ie.survey_coverage(rs)
        self.assertTrue(s["screened_out_under_surveyed"])
        self.assertEqual(s["screened_in"]["mean_satisfaction"], 5)
        rs2 = [dict(r, survey={"offered": True, "responded": False}) for r in rs]
        self.assertFalse(ie.survey_coverage(rs2)["screened_out_under_surveyed"])


class TestBreakdownAndReport(unittest.TestCase):
    def test_small_groups_suppressed(self):
        rs = [rec(i) for i in range(12)] + [rec(100 + i, language="ht") for i in range(3)]
        b = ie.breakdown(rs, "language", min_cell=10)
        self.assertTrue(b["ht"]["suppressed"])
        self.assertEqual(b["ht"]["records"], "<10")
        self.assertEqual(b["en"]["records"], 12)

    def test_boolean_and_demographic_groups(self):
        rs = [rec(i, demographics={"rural": i % 2 == 0}) for i in range(20)]
        b = ie.breakdown(rs, "rural", min_cell=5)
        self.assertEqual(sorted(b), ["false", "true"])

    def test_synthetic_report(self):
        rs = ie.synthetic(400, seed=7)
        self.assertEqual(rs, ie.synthetic(400, seed=7))
        for r in rs:
            self.assertEqual(ie.validate(r), [], r["record_id"])
        out = ie.report(rs, by=("site", "period", "channel", "language", "age_band"))
        self.assertEqual(out["overall"]["records"], 400)
        self.assertEqual(set(out["by"]["period"]), {"baseline", "pilot"})
        self.assertEqual(set(out["by"]["site"]), {"site_a", "site_b"})
        self.assertTrue(out["overall"]["survey"]["screened_out_under_surveyed"])
        json.dumps(out)

    def test_cli(self):
        import io
        import contextlib
        import tempfile
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(ie.main(["--synthetic", "50", "--seed", "3"]), 0)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "r.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                f.write(buf.getvalue())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(ie.main([path, "--by", "site", "--min-cell", "5"]), 0)
            self.assertEqual(json.loads(out.getvalue())["overall"]["records"], 50)


class TestSchemaFile(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(HERE, "schema", "call_record.schema.json"), encoding="utf-8") as f:
            self.schema = json.load(f)
        self.p = self.schema["properties"]

    def test_fields_match_code(self):
        self.assertEqual(set(self.p), set(ie.REQUIRED) | set(ie.OPTIONAL))
        self.assertEqual(set(self.schema["required"]), set(ie.REQUIRED))
        self.assertEqual(self.p["schema_version"]["const"], ie.SCHEMA_VERSION)
        self.assertFalse(self.schema["additionalProperties"])
        for obj, keys in (("disclosures", ie.DISCLOSURE_KEYS), ("review", ie.REVIEW_KEYS),
                          ("callback", ie.CALLBACK_KEYS), ("demographics", ie.DEMOGRAPHIC_KEYS),
                          ("survey", ie.SURVEY_KEYS)):
            self.assertEqual(set(self.p[obj]["properties"]), set(keys), obj)
            self.assertFalse(self.p[obj]["additionalProperties"], obj)

    def test_enums_match_code(self):
        self.assertEqual(tuple(self.p["period"]["enum"]), ie.PERIODS)
        self.assertEqual(tuple(self.p["channel"]["enum"]), ie.CHANNELS)
        self.assertEqual(tuple(self.p["consent"]["enum"]), ie.CONSENT)
        self.assertEqual(tuple(self.p["outcome"]["enum"]), ie.OUTCOMES)
        self.assertEqual(tuple(self.p["abandoned_at_step"]["enum"]), ie.STEPS)
        self.assertEqual(tuple(self.p["demographics"]["properties"]["age_band"]["enum"]), ie.AGE_BANDS)
        self.assertEqual(tuple(self.p["review"]["properties"]["reviewer_eligibility"]["enum"]),
                         ie.REVIEWER_ELIGIBILITY)

    def test_no_identifier_fields(self):
        flat = json.dumps(self.schema).lower()
        for key in ('"name"', '"phone"', '"address"', '"dob"', '"email"', '"transcript"', '"notes"', '"ssn"'):
            self.assertNotIn(key + ":", flat)


class TestRepoText(unittest.TestCase):
    def test_licence_and_disclaimer(self):
        with open(os.path.join(HERE, "README.md"), encoding="utf-8") as f:
            readme = f.read()
        self.assertIn("Apache-2.0", readme)
        self.assertIn("CC BY 4.0", readme)
        self.assertIn("not legal advice", readme)


if __name__ == "__main__":
    unittest.main()
