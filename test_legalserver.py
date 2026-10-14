# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Munim, Inc.
import contextlib
import copy
import csv
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import intake_eval as ie  # noqa: E402
import legalserver as ls  # noqa: E402

# A made-up export: no real person, case or program.
EXPORT = """Case ID,Intake Date,Disposition,Rejection Reason,LSC Matter Referral Reporting Code,Intake Type,Language,Age at Intake,County of Residence
26-0000101,01/05/2027,Open,,,Telephone,English,34,Davidson
26-0000102,2027-01-20,Closed,,,AI Voice Agent,Spanish,70,Hickman
26-0000103,01/22/2027 10:15 AM,Pending,,,AI Voice Agent,English,,Davidson
26-0000104,1/25/2027,Rejected Matter,Outside service area,Referred out,AI Voice Agent,Haitian Creole,17,
26-0000105,01/26/2027,Rejected Matter,Over income,,AI Voice Agent,english,52,Davidson
26-0000106,01/27/2027,Rejected Matter,Conflict of interest,,Telephone,English,40,Davidson
26-0000107,01/28/2027,Rejected Matter,Case created in error,,Telephone,English,40,Davidson
26-0000108,01/10/2027,Incomplete Intake,,,Telephone,Mam,22,Hickman
26-0000109,01/29/2027,Prescreen,,,Online Intake,Other,,
"""

MAPPING = {
    "site": "site_a",
    "period": {"pilot_start": "2027-01-15"},
    "consent": "given",
    "columns": {"case_id": "Case ID", "date": "Intake Date", "disposition": "Disposition",
                "reject_reason": "Rejection Reason", "referral_code": "LSC Matter Referral Reporting Code",
                "channel": "Intake Type", "language": "Language", "age": "Age at Intake",
                "county": "County of Residence"},
    "channel_values": {"AI Voice Agent": "ai_voice", "Telephone": "human_phone", "Online Intake": "online"},
    "referred_codes": ["Referred out"],
    "conflict_reasons": ["Conflict of interest"],
    "drop_reasons": ["Case created in error"],
    "counties": {"Davidson": {"fips": "47037", "rural": False}, "Hickman": {"fips": "47081", "rural": True}},
}

KEY = b"0123456789abcdef-test-key"


def rows(text=EXPORT):
    return list(csv.DictReader(io.StringIO(text)))


def by_case(pairs):
    return {c: r for c, r in pairs}


class TestHelpers(unittest.TestCase):
    def test_dates(self):
        self.assertEqual(ls.parse_date("2027-01-05"), "2027-01-05")
        self.assertEqual(ls.parse_date("1/5/2027"), "2027-01-05")
        self.assertEqual(ls.parse_date("01/22/2027 10:15 AM"), "2027-01-22")
        self.assertEqual(ls.parse_date("2027-01-22T10:15:00"), "2027-01-22")
        for bad in ("", "Jan 5 2027", "2027-02-30", "13/01/2027", None):
            self.assertIsNone(ls.parse_date(bad), bad)

    def test_age_bands(self):
        cases = {"0": "under_18", "17": "under_18", "18": "18_24", "24.9": "18_24", "25": "25_44",
                 "44": "25_44", "45": "45_64", "64": "45_64", "65": "65_plus", "101": "65_plus",
                 "": "unknown", "abc": "unknown", "-1": "unknown", "130": "unknown", "nan": "unknown"}
        for value, band in cases.items():
            self.assertEqual(ls.age_band(value), band, value)
            self.assertIn(band, ie.AGE_BANDS)

    def test_record_id_is_keyed(self):
        a = ls.record_id(KEY, "site_a", "26-0000101")
        self.assertEqual(a, ls.record_id(KEY, "site_a", "26-0000101"))
        self.assertNotEqual(a, ls.record_id(b"another-key-of-16-bytes", "site_a", "26-0000101"))
        self.assertNotEqual(a, ls.record_id(KEY, "site_b", "26-0000101"))
        self.assertTrue(a.startswith("ls-") and len(a) == 27)
        self.assertNotIn("0000101", a)

    def test_language_codes_are_valid(self):
        for name, code in ls.LANGUAGES.items():
            self.assertTrue(ie._LANGUAGE.match(code), (name, code))
            self.assertEqual(name, ls._norm(name))


class TestMapping(unittest.TestCase):
    def test_good_mappings(self):
        self.assertEqual(ls.check_mapping(MAPPING), [])
        with open(os.path.join(HERE, "docs", "legalserver-mapping.example.json"), encoding="utf-8") as f:
            self.assertEqual(ls.check_mapping(json.load(f)), [])

    def test_bad_mappings(self):
        def broken(**changes):
            m = copy.deepcopy(MAPPING)
            for k, v in changes.items():
                if v is None:
                    m.pop(k)
                else:
                    m[k] = v
            return ls.check_mapping(m)

        self.assertTrue(broken(site="site a"))
        self.assertTrue(broken(consent=None))
        self.assertTrue(broken(period="trial"))
        self.assertTrue(broken(period={"pilot_start": "15/01/2027"}))
        self.assertTrue(broken(columns={"case_id": "Case ID", "date": "Intake Date"}))
        self.assertTrue(broken(channel_values={"SMS": "sms"}))
        self.assertTrue(broken(language_values={"Creole": "Haitian"}))
        self.assertTrue(broken(dispositions={"Archived": "other"}))
        self.assertTrue(broken(dispositions={"Pending": "accepted"}))
        self.assertTrue(broken(counties={"Davidson": {"fips": "4703"}}))
        self.assertTrue(broken(counties={"Davidson": {"fips": "47037", "name": "x"}}))
        self.assertTrue(broken(drop_reasons="Duplicate"))
        self.assertTrue(broken(colour="blue"))
        no_channel = copy.deepcopy(MAPPING)
        del no_channel["columns"]["channel"]
        self.assertTrue(ls.check_mapping(no_channel))
        self.assertEqual(ls.check_mapping(dict(no_channel, channel_default="ai_voice")), [])
        self.assertTrue(ls.check_mapping([]))


class TestConvert(unittest.TestCase):
    def setUp(self):
        self.pairs, self.problems, self.stats = ls.convert(rows(), MAPPING, KEY)
        self.r = by_case(self.pairs)

    def test_counts(self):
        self.assertEqual(self.problems, [])
        self.assertEqual(self.stats, {"rows": 9, "records": 8, "dropped": 1})
        self.assertNotIn("26-0000107", self.r)

    def test_outcomes(self):
        expected = {
            "26-0000101": "screened_in",
            "26-0000102": "screened_in",
            "26-0000103": "screened_in",
            "26-0000104": "screened_out_referred",
            "26-0000105": "screened_out_no_referral",
            "26-0000106": "conflict_hold",
            "26-0000108": "abandoned",
            "26-0000109": "other",
        }
        self.assertEqual({c: r["outcome"] for c, r in self.r.items()}, expected)
        self.assertTrue(self.r["26-0000101"]["case_opened"])
        self.assertTrue(self.r["26-0000102"]["case_opened"])
        self.assertFalse(self.r["26-0000103"]["case_opened"])
        self.assertNotIn("case_opened", self.r["26-0000104"])

    def test_fields(self):
        r = self.r["26-0000101"]
        self.assertEqual(r["date"], "2027-01-05")
        self.assertEqual(r["period"], "baseline")
        self.assertEqual(r["channel"], "human_phone")
        self.assertEqual(r["language"], "en")
        self.assertEqual(r["consent"], "given")
        self.assertEqual(r["site"], "site_a")
        self.assertEqual(r["demographics"], {"age_band": "25_44", "county_fips": "47037", "rural": False})
        r = self.r["26-0000102"]
        self.assertEqual((r["period"], r["channel"], r["language"]), ("pilot", "ai_voice", "es"))
        self.assertEqual(r["demographics"], {"age_band": "65_plus", "county_fips": "47081", "rural": True})
        self.assertEqual(self.r["26-0000103"]["date"], "2027-01-22")
        self.assertEqual(self.r["26-0000103"]["demographics"]["age_band"], "unknown")
        self.assertEqual(self.r["26-0000104"]["language"], "ht")
        self.assertEqual(self.r["26-0000104"]["demographics"], {"age_band": "under_18"})
        self.assertEqual(self.r["26-0000105"]["language"], "en")
        self.assertEqual(self.r["26-0000108"]["language"], "mam")
        self.assertEqual(self.r["26-0000108"]["period"], "baseline")
        self.assertEqual(self.r["26-0000109"]["channel"], "online")
        self.assertEqual(self.r["26-0000109"]["language"], "mis")

    def test_records_are_valid_and_hold_no_case_ids(self):
        ids = set()
        for case_id, record in self.pairs:
            self.assertEqual(ie.validate(record), [], case_id)
            ids.add(record["record_id"])
            self.assertNotIn(case_id, json.dumps(record))
        self.assertEqual(len(ids), 8)
        out = ie.report([r for _, r in self.pairs], by=("period", "channel"), min_cell=1)
        self.assertEqual(out["overall"]["records"], 8)
        self.assertEqual(out["overall"]["screen_in_rate"]["x"], 3)
        self.assertEqual(out["overall"]["screen_in_rate"]["n"], 5)

    def test_short_key_refused(self):
        with self.assertRaises(ValueError):
            ls.convert(rows(), MAPPING, b"short")

    def test_problems_are_reported(self):
        text = EXPORT.replace("Mam", "Klingon").replace(",Prescreen,", ",Archived,")
        text = text.replace("Online Intake", "Carrier pigeon").replace("01/26/2027", "26 Jan 2027")
        _, problems, stats = ls.classify(rows(text), MAPPING)
        self.assertEqual(len(problems), 3)
        self.assertTrue(any("Klingon" in p for p in problems))
        self.assertTrue(any("Archived" in p for p in problems))
        self.assertTrue(any("26 Jan 2027" in p for p in problems))
        self.assertEqual(stats["records"], 5)

    def test_channel_default_and_overrides(self):
        text = EXPORT.replace("Online Intake", "Carrier pigeon")
        m = dict(MAPPING, channel_default="other", dispositions={"Pending": "other"})
        r = by_case(ls.classify(rows(text), m)[0])
        self.assertEqual(r["26-0000109"]["channel"], "other")
        self.assertEqual(r["26-0000103"]["outcome"], "other")
        self.assertNotIn("case_opened", r["26-0000103"])
        m = dict(MAPPING, period="pilot", referred_codes=[], referred_reasons=["Outside service area"])
        r = by_case(ls.classify(rows(), m)[0])
        self.assertEqual(r["26-0000101"]["period"], "pilot")
        self.assertEqual(r["26-0000104"]["outcome"], "screened_out_referred")

    def test_missing_column_and_duplicates(self):
        m = copy.deepcopy(MAPPING)
        m["columns"]["disability"] = "Disabled"
        pairs, problems, _ = ls.classify(rows(), m)
        self.assertEqual(pairs, [])
        self.assertTrue(any("Disabled" in p for p in problems))
        lines = EXPORT.splitlines()
        _, problems, _ = ls.classify(rows("\n".join(lines + [lines[1]]) + "\n"), MAPPING)
        self.assertTrue(any("repeated" in p for p in problems))
        self.assertTrue(ls.classify([], MAPPING)[1])

    def test_disability(self):
        text = EXPORT.replace("County of Residence", "County of Residence,Disabled")
        lines = text.splitlines()
        lines = [lines[0]] + [line + (",Yes" if i % 2 else ",No") for i, line in enumerate(lines[1:])]
        m = copy.deepcopy(MAPPING)
        m["columns"]["disability"] = "Disabled"
        m["disability_values"] = {"Yes": True, "No": False}
        r = by_case(ls.classify(rows("\n".join(lines) + "\n"), m)[0])
        self.assertFalse(r["26-0000101"]["demographics"]["disability_reported"])
        self.assertTrue(r["26-0000102"]["demographics"]["disability_reported"])


class TestReviewSample(unittest.TestCase):
    def setUp(self):
        self.pairs = ls.classify(rows(), MAPPING)[0]

    def test_one_per_outcome(self):
        picked = ls.draw_review(self.pairs, 1, seed=3)
        self.assertEqual(len(picked), 3)
        outcomes = {by_case(self.pairs)[c]["outcome"] for c in picked}
        self.assertEqual(outcomes, set(ie.DECIDED))
        self.assertEqual(picked, sorted(picked))

    def test_all_when_sample_exceeds_group(self):
        self.assertEqual(ls.draw_review(self.pairs, 10),
                         ["26-0000101", "26-0000102", "26-0000103", "26-0000104", "26-0000105"])

    def test_reproducible_and_order_free(self):
        lines = EXPORT.splitlines()
        reversed_pairs = ls.classify(rows("\n".join([lines[0]] + lines[:0:-1]) + "\n"), MAPPING)[0]
        for seed in range(5):
            self.assertEqual(ls.draw_review(self.pairs, 2, seed), ls.draw_review(reversed_pairs, 2, seed))
        with self.assertRaises(ValueError):
            ls.draw_review(self.pairs, 0)

    def test_attach_reviews(self):
        pairs = ls.convert(rows(), MAPPING, KEY)[0]
        sheet = [
            {"case_id": "26-0000105", "should_screen_in": "Yes", "reviewer_eligibility": "eligible",
             "safety_indicator_present": "no", "legal_advice_given": "", "data_errors": "1", "random_sample": "yes"},
            {"case_id": "26-0000101", "should_screen_in": "yes", "reviewer_eligibility": "",
             "safety_indicator_present": "", "legal_advice_given": "no", "data_errors": "", "random_sample": "no"},
            {"case_id": "26-0000102", "should_screen_in": "", "reviewer_eligibility": "",
             "safety_indicator_present": "", "legal_advice_given": "", "data_errors": "", "random_sample": "yes"},
            {"case_id": "", "should_screen_in": "", "reviewer_eligibility": "",
             "safety_indicator_present": "", "legal_advice_given": "", "data_errors": "", "random_sample": ""},
        ]
        self.assertEqual(ls.attach_reviews(pairs, sheet), [])
        r = by_case(pairs)
        self.assertEqual(r["26-0000105"]["review"], {"should_screen_in": True, "reviewer_eligibility": "eligible",
                                                     "safety_indicator_present": False, "data_errors": 1,
                                                     "random_sample": True})
        self.assertEqual(r["26-0000101"]["review"], {"should_screen_in": True, "legal_advice_given": False,
                                                     "random_sample": False})
        self.assertNotIn("review", r["26-0000102"])
        for _, record in pairs:
            self.assertEqual(ie.validate(record), [])

    def test_attach_reviews_problems(self):
        pairs = ls.convert(rows(), MAPPING, KEY)[0]
        problems = ls.attach_reviews(pairs, [
            {"case_id": "26-9999999", "should_screen_in": "yes"},
            {"case_id": "26-0000105", "should_screen_in": "maybe"},
            {"case_id": "26-0000104", "data_errors": "-1"},
            {"case_id": "26-0000103", "reviewer_eligibility": "probably"},
        ])
        self.assertEqual(len(problems), 4)
        self.assertFalse(any("review" in r for _, r in pairs))


class TestCli(unittest.TestCase):
    def test_draw_convert_and_report(self):
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "export.csv")
            mapping = os.path.join(d, "mapping.json")
            key = os.path.join(d, "key.txt")
            with open(export, "w", encoding="utf-8-sig", newline="") as f:
                f.write(EXPORT)
            with open(mapping, "w", encoding="utf-8") as f:
                json.dump(MAPPING, f)
            with open(key, "wb") as f:
                f.write(KEY + b"\n")

            sheet = io.StringIO()
            with contextlib.redirect_stdout(sheet):
                self.assertEqual(ls.main([export, "--mapping", mapping, "--draw-review", "1", "--seed", "3"]), 0)
            sheet_rows = list(csv.DictReader(io.StringIO(sheet.getvalue())))
            self.assertEqual(len(sheet_rows), 3)
            self.assertEqual(tuple(sheet_rows[0]), ls.REVIEW_COLUMNS)
            sheet_rows[0]["should_screen_in"] = "yes"
            reviews = os.path.join(d, "reviews.csv")
            with open(reviews, "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=ls.REVIEW_COLUMNS)
                w.writeheader()
                w.writerows(sheet_rows)

            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(ls.main([export, "--mapping", mapping, "--key-file", key, "--reviews", reviews]), 0)
            text = out.getvalue()
            self.assertNotIn("26-00001", text)
            records = [json.loads(line) for line in text.splitlines()]
            self.assertEqual(len(records), 8)
            self.assertEqual(sum(1 for r in records if "review" in r), 1)
            self.assertEqual(ie.report(records, min_cell=1)["overall"]["records"], 8)

            with open(key, "wb") as f:
                f.write(b"short")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(ls.main([export, "--mapping", mapping, "--key-file", key]), 2)


if __name__ == "__main__":
    unittest.main()
