# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Munim, Inc.
"""Reference metrics for evaluating AI intake in legal aid.

Reads de-identified per-contact records (one JSON object per line, see
schema/call_record.schema.json), validates them, and computes the intake
indicators described in docs/indicators.md: completion, screen-in and
screen-out rates, wrongful screen-outs found by human review, the estimated
share of eligible callers turned away, agreement with reviewers, safety-flag
recall, disclosures, human transfer on request, callbacks, survey coverage by
outcome, and the same figures split by site, period, channel, language and
other groups, with small groups suppressed.

Standard library only. This is an evaluation aid, not legal advice.

    python intake_eval.py records.jsonl --by site,period,language > report.json
    python intake_eval.py --synthetic 400 --seed 7 > synthetic.jsonl
    python intake_eval.py --plan-zero 0.05
    python intake_eval.py --plan-margin 0.10 0.05
"""
import argparse
import json
import math
import random
import re
import statistics
import sys

SCHEMA_VERSION = "0.2.0"
SUPPORTED_VERSIONS = ("0.1.0", "0.2.0")

PERIODS = ("baseline", "pilot")
CHANNELS = ("ai_voice", "human_phone", "online", "walk_in", "other")
CONSENT = ("given", "declined", "not_reached")
OUTCOMES = (
    "screened_in",
    "screened_out_referred",
    "screened_out_no_referral",
    "transferred_to_human",
    "conflict_hold",
    "abandoned",
    "other",
)
STEPS = ("greeting", "consent", "eligibility", "facts", "wrap_up")
AGE_BANDS = ("under_18", "18_24", "25_44", "45_64", "65_plus", "unknown")
REVIEWER_ELIGIBILITY = ("eligible", "ineligible", "unclear")
DISCLOSURE_KEYS = ("ai_identified", "not_a_lawyer", "consent_captured", "follow_up_timeline")

SCREENED_OUT = ("screened_out_referred", "screened_out_no_referral")
DECIDED = ("screened_in",) + SCREENED_OUT

REQUIRED = ("schema_version", "record_id", "site", "period", "channel", "date", "language", "consent", "outcome")
OPTIONAL = (
    "after_hours", "abandoned_at_step", "duration_seconds", "required_fields_total",
    "required_fields_filled", "disclosures", "human_requested", "human_transfer_completed",
    "safety_flag_ai", "review", "callback", "case_opened", "demographics", "survey",
)
REVIEW_KEYS = ("reviewer_eligibility", "should_screen_in", "safety_indicator_present", "legal_advice_given",
               "data_errors", "random_sample")
CALLBACK_KEYS = ("needed", "completed", "hours_to_callback")
DEMOGRAPHIC_KEYS = ("age_band", "disability_reported", "rural", "county_fips")
SURVEY_KEYS = ("offered", "responded", "satisfaction_1_5")

# Values that look like direct identifiers must never appear in a record.
_PII_PATTERNS = (
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    re.compile(r"\(?\b\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}\b"),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
)
_RECORD_ID = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
_SITE = re.compile(r"^[A-Za-z0-9_-]{2,32}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LANGUAGE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
_FIPS = re.compile(r"^\d{5}$")

Z95 = 1.959963984540054

# Simulation settings for the population estimates; fixed so reports are reproducible.
AUDIT_DRAWS = 10000
AUDIT_SEED = 2026


def _is_bool(v):
    return isinstance(v, bool)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _check_object(name, obj, allowed, errors):
    if not isinstance(obj, dict):
        errors.append(f"{name} must be an object")
        return False
    for k in obj:
        if k not in allowed:
            errors.append(f"{name}.{k} is not a schema field")
    return True


def _check_optional_bool(name, obj, key, errors, nullable=True):
    if key in obj:
        v = obj[key]
        if not (_is_bool(v) or (nullable and v is None)):
            errors.append(f"{name}.{key} must be true, false" + (" or null" if nullable else ""))


def validate(record):
    """Return a list of problems with one record; empty means valid."""
    errors = []
    if not isinstance(record, dict):
        return ["record must be a JSON object"]
    for k in record:
        if k not in REQUIRED and k not in OPTIONAL:
            errors.append(f"{k} is not a schema field")
    for k in REQUIRED:
        if k not in record:
            errors.append(f"{k} is required")
    if errors:
        return errors

    if record["schema_version"] not in SUPPORTED_VERSIONS:
        errors.append(f"schema_version must be one of {SUPPORTED_VERSIONS}")
    if not isinstance(record["record_id"], str) or not _RECORD_ID.match(record["record_id"]):
        errors.append("record_id must be 4-64 letters, digits, _ or -")
    if not isinstance(record["site"], str) or not _SITE.match(record["site"]):
        errors.append("site must be 2-32 letters, digits, _ or -")
    if record["period"] not in PERIODS:
        errors.append(f"period must be one of {PERIODS}")
    if record["channel"] not in CHANNELS:
        errors.append(f"channel must be one of {CHANNELS}")
    if not isinstance(record["date"], str) or not _DATE.match(record["date"]):
        errors.append("date must be YYYY-MM-DD")
    if not isinstance(record["language"], str) or not _LANGUAGE.match(record["language"]):
        errors.append("language must be a BCP 47 tag such as en or es")
    if record["consent"] not in CONSENT:
        errors.append(f"consent must be one of {CONSENT}")
    if record["outcome"] not in OUTCOMES:
        errors.append(f"outcome must be one of {OUTCOMES}")

    for k in ("after_hours", "human_requested", "safety_flag_ai"):
        _check_optional_bool("record", record, k, errors, nullable=False)
    for k in ("human_transfer_completed", "case_opened"):
        _check_optional_bool("record", record, k, errors)
    if "abandoned_at_step" in record and record["abandoned_at_step"] not in STEPS:
        errors.append(f"abandoned_at_step must be one of {STEPS}")
    for k in ("duration_seconds", "required_fields_total", "required_fields_filled"):
        if k in record and not (_is_int(record[k]) and record[k] >= 0):
            errors.append(f"{k} must be a whole number >= 0")
    if (_is_int(record.get("required_fields_total")) and _is_int(record.get("required_fields_filled"))
            and record["required_fields_filled"] > record["required_fields_total"]):
        errors.append("required_fields_filled cannot exceed required_fields_total")

    if "disclosures" in record and _check_object("disclosures", record["disclosures"], DISCLOSURE_KEYS, errors):
        for k in DISCLOSURE_KEYS:
            _check_optional_bool("disclosures", record["disclosures"], k, errors, nullable=False)

    if "review" in record and _check_object("review", record["review"], REVIEW_KEYS, errors):
        rv = record["review"]
        if "reviewer_eligibility" in rv and rv["reviewer_eligibility"] not in REVIEWER_ELIGIBILITY:
            errors.append(f"review.reviewer_eligibility must be one of {REVIEWER_ELIGIBILITY}")
        for k in ("should_screen_in", "safety_indicator_present", "legal_advice_given"):
            _check_optional_bool("review", rv, k, errors)
        _check_optional_bool("review", rv, "random_sample", errors, nullable=False)
        if "data_errors" in rv and not (_is_int(rv["data_errors"]) and rv["data_errors"] >= 0):
            errors.append("review.data_errors must be a whole number >= 0")

    if "callback" in record and _check_object("callback", record["callback"], CALLBACK_KEYS, errors):
        cb = record["callback"]
        _check_optional_bool("callback", cb, "needed", errors, nullable=False)
        _check_optional_bool("callback", cb, "completed", errors)
        h = cb.get("hours_to_callback")
        if h is not None and not (isinstance(h, (int, float)) and not _is_bool(h) and h >= 0):
            errors.append("callback.hours_to_callback must be a number >= 0 or null")

    if "demographics" in record and _check_object("demographics", record["demographics"], DEMOGRAPHIC_KEYS, errors):
        dm = record["demographics"]
        if "age_band" in dm and dm["age_band"] not in AGE_BANDS:
            errors.append(f"demographics.age_band must be one of {AGE_BANDS}")
        for k in ("disability_reported", "rural"):
            _check_optional_bool("demographics", dm, k, errors)
        if "county_fips" in dm and not (isinstance(dm["county_fips"], str) and _FIPS.match(dm["county_fips"])):
            errors.append("demographics.county_fips must be a 5-digit county FIPS code")

    if "survey" in record and _check_object("survey", record["survey"], SURVEY_KEYS, errors):
        sv = record["survey"]
        _check_optional_bool("survey", sv, "offered", errors, nullable=False)
        _check_optional_bool("survey", sv, "responded", errors, nullable=False)
        s = sv.get("satisfaction_1_5")
        if s is not None and not (_is_int(s) and 1 <= s <= 5):
            errors.append("survey.satisfaction_1_5 must be 1-5 or null")

    for s in _strings(record):
        if any(p.search(s) for p in _PII_PATTERNS):
            errors.append("a value looks like an email, phone or SSN; records must be de-identified")
            break
    return errors


def wilson(x, n, z=Z95):
    """Wilson score interval for x successes in n trials; None when n == 0."""
    if n == 0:
        return None
    p = x / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def rate(x, n):
    """A proportion with its 95% Wilson interval, or None when n == 0."""
    if n == 0:
        return None
    lo, hi = wilson(x, n)
    return {"x": x, "n": n, "rate": round(x / n, 4), "ci95": [round(lo, 4), round(hi, 4)]}


def kappa(a, b, c, d):
    """Cohen's kappa for a 2x2 table.

    a: both say in, b: AI in / reviewer out, c: AI out / reviewer in, d: both out.
    """
    n = a + b + c + d
    if n == 0:
        return None
    po = (a + d) / n
    pe = ((a + b) * (a + c) + (c + d) * (b + d)) / (n * n)
    if pe == 1:
        return 1.0 if po == 1 else None
    return (po - pe) / (1 - pe)


def plan_zero_found(upper, z=Z95):
    """Reviews needed in one outcome so that finding no errors caps its 95% Wilson upper bound at `upper`."""
    if not 0 < upper < 1:
        raise ValueError("upper must be between 0 and 1")
    # With x = 0 the Wilson upper bound is z^2 / (n + z^2).
    return math.ceil(z * z * (1 - upper) / upper - 1e-9)


def plan_margin(expected, margin, z=Z95, n_max=100000):
    """Smallest review sample whose 95% Wilson interval is within +/- margin when the true rate is `expected`."""
    if not 0 <= expected <= 1 or not 0 < margin < 0.5:
        raise ValueError("expected must be 0-1 and margin between 0 and 0.5")
    for n in range(1, n_max + 1):
        lo, hi = wilson(expected * n, n, z)
        if (hi - lo) / 2 <= margin:
            return n
    return None


def _review(r):
    return r.get("review") or {}


def _reviewed_at_random(r):
    rv = _review(r)
    return rv.get("should_screen_in") is not None and rv.get("random_sample", True) is True


def _quantile(xs, q):
    i = q * (len(xs) - 1)
    lo, hi = math.floor(i), math.ceil(i)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def _interval(xs):
    xs = sorted(xs)
    return [round(_quantile(xs, 0.025), 4), round(_quantile(xs, 0.975), 4)]


def screen_out_audit(screened_in, screened_out, draws=AUDIT_DRAWS, seed=AUDIT_SEED):
    """Population estimates from a review sample drawn at random within each outcome.

    This is the e-discovery elusion test applied to intake. The share of
    reviewed screened-out contacts that should have been screened in (the
    elusion, reported as wrongful_screen_out_rate) is scaled up to the whole
    screened-out group and set against the eligible callers who were screened
    in. That gives the share of eligible callers the intake turned away, which
    elusion alone does not: an intake that screens out more people can have
    lower elusion and still turn away more eligible callers.

    Intervals come from simulation with a Jeffreys prior in each outcome.
    Reviews marked random_sample false are left out of these estimates.
    """
    groups = {"screened_in": screened_in}
    for o in SCREENED_OUT:
        groups[o] = [r for r in screened_out if r["outcome"] == o]
    strata = {}
    unreviewed = []
    for g, rs in groups.items():
        sample = [r for r in rs if _reviewed_at_random(r)]
        x = sum(1 for r in sample if _review(r)["should_screen_in"])
        strata[g] = {"population": len(rs), "reviewed": len(sample), "should_screen_in": x,
                     "rate": rate(x, len(sample))}
        if rs and not sample:
            unreviewed.append(g)
    excluded = sum(1 for r in screened_in + screened_out
                   if _review(r).get("should_screen_in") is not None and _review(r).get("random_sample") is False)
    out = {
        "method": "stratified elusion test; 95% intervals by simulation with a Jeffreys prior per outcome",
        "strata": strata,
        "excluded_non_random_reviews": excluded,
        "eligible_turned_away_share": None,
        "eligible_turned_away_estimate": None,
        "accuracy_weighted": None,
    }
    total = sum(s["population"] for s in strata.values())
    if total == 0:
        out["status"] = "no screening decisions"
        return out
    if unreviewed:
        out["status"] = "no random review in " + ", ".join(unreviewed)
        return out

    def estimate(p):
        missed = sum(strata[g]["population"] * p[g] for g in SCREENED_OUT)
        kept = strata["screened_in"]["population"] * p["screened_in"]
        correct = kept + sum(strata[g]["population"] * (1 - p[g]) for g in SCREENED_OUT)
        share = missed / (missed + kept) if missed + kept > 0 else None
        return missed, share, correct / total

    point = {g: (s["should_screen_in"] / s["reviewed"] if s["reviewed"] else 0.0) for g, s in strata.items()}
    missed, share, acc = estimate(point)

    rng = random.Random(seed)
    sim_missed, sim_share, sim_acc = [], [], []
    for _ in range(draws):
        p = {g: (rng.betavariate(s["should_screen_in"] + 0.5, s["reviewed"] - s["should_screen_in"] + 0.5)
                 if s["reviewed"] else 0.0) for g, s in strata.items()}
        m, sh, a = estimate(p)
        sim_missed.append(m)
        sim_acc.append(a)
        if sh is not None:
            sim_share.append(sh)

    out["status"] = "ok"
    out["eligible_turned_away_estimate"] = {"estimate": round(missed, 1),
                                            "ci95": [round(v, 1) for v in _interval(sim_missed)]}
    if share is not None and sim_share:
        out["eligible_turned_away_share"] = {"estimate": round(share, 4), "ci95": _interval(sim_share)}
    out["accuracy_weighted"] = {"estimate": round(acc, 4), "ci95": _interval(sim_acc)}
    return out


def metrics(records):
    """Headline indicators for one group of records."""
    n = len(records)
    reached = [r for r in records if r["consent"] in ("given", "declined")]
    consented = [r for r in records if r["consent"] == "given"]
    completed = [r for r in consented if r["outcome"] != "abandoned"]
    decided = [r for r in completed if r["outcome"] in DECIDED]
    screened_in = [r for r in decided if r["outcome"] == "screened_in"]
    screened_out = [r for r in decided if r["outcome"] in SCREENED_OUT]

    steps = {s: 0 for s in STEPS}
    for r in records:
        if r["outcome"] == "abandoned" and r.get("abandoned_at_step") in steps:
            steps[r["abandoned_at_step"]] += 1

    with_fields = [r for r in completed if _is_int(r.get("required_fields_total")) and r["required_fields_total"] > 0]
    total_fields = sum(r["required_fields_total"] for r in with_fields)
    filled_fields = sum(r.get("required_fields_filled", 0) for r in with_fields)

    disclosures = {}
    for k in DISCLOSURE_KEYS:
        seen = [r["disclosures"][k] for r in reached if k in (r.get("disclosures") or {})]
        disclosures[k] = rate(sum(seen), len(seen))

    asked_human = [r for r in records if r.get("human_requested") is True]
    transferred = sum(1 for r in asked_human if r.get("human_transfer_completed") is True)

    # Human review: wrongful screen-outs, wrongful screen-ins, agreement, safety recall.
    rev_out = [r for r in screened_out if _review(r).get("should_screen_in") is not None]
    rev_in = [r for r in screened_in if _review(r).get("should_screen_in") is not None]
    a = sum(1 for r in rev_in if _review(r)["should_screen_in"])
    b = len(rev_in) - a
    c = sum(1 for r in rev_out if _review(r)["should_screen_in"])
    d = len(rev_out) - c
    k = kappa(a, b, c, d)
    safety = [r for r in records if _review(r).get("safety_indicator_present") is True]
    flagged = sum(1 for r in safety if r.get("safety_flag_ai") is True)
    advice_reviewed = [r for r in records if _review(r).get("legal_advice_given") is not None]

    callbacks = [r for r in completed if (r.get("callback") or {}).get("needed") is True]
    cb_done = [r for r in callbacks if r["callback"].get("completed") is True]
    hours = [r["callback"]["hours_to_callback"] for r in cb_done if r["callback"].get("hours_to_callback") is not None]

    opened_known = [r for r in screened_in if r.get("case_opened") is not None]

    return {
        "records": n,
        "after_hours_share": rate(sum(1 for r in records if r.get("after_hours") is True),
                                  sum(1 for r in records if "after_hours" in r)),
        "consent_rate": rate(len(consented), len(reached)),
        "completion_rate": rate(len(completed), len(consented)),
        "abandoned_by_step": steps,
        "screen_in_rate": rate(len(screened_in), len(decided)),
        "referral_rate_when_screened_out": rate(
            sum(1 for r in screened_out if r["outcome"] == "screened_out_referred"), len(screened_out)),
        "field_completeness": rate(filled_fields, total_fields),
        "median_duration_seconds": _median([r["duration_seconds"] for r in completed if _is_int(r.get("duration_seconds"))]),
        "disclosures_delivered": disclosures,
        "human_transfer_on_request": rate(transferred, len(asked_human)),
        "review": {
            "reviewed_decisions": len(rev_in) + len(rev_out),
            "wrongful_screen_out_rate": rate(c, len(rev_out)),
            "wrongful_screen_in_rate": rate(b, len(rev_in)),
            "agreement": rate(a + d, len(rev_in) + len(rev_out)),
            "kappa": None if k is None else round(k, 4),
            "safety_flag_recall": rate(flagged, len(safety)),
            "legal_advice_incidents": sum(1 for r in advice_reviewed if _review(r)["legal_advice_given"]),
            "legal_advice_reviewed": len(advice_reviewed),
            "data_errors_per_reviewed_call": _mean(
                [_review(r)["data_errors"] for r in records if _is_int(_review(r).get("data_errors"))]),
            "population_estimates": screen_out_audit(screened_in, screened_out),
        },
        "callback_completion": rate(len(cb_done), len(callbacks)),
        "median_hours_to_callback": _median(hours),
        "case_opened_when_screened_in": rate(sum(1 for r in opened_known if r["case_opened"]), len(opened_known)),
        "survey": survey_coverage(records),
    }


def survey_coverage(records):
    """Who was asked for feedback, by screening outcome.

    A satisfaction score gathered only from people who were screened in says
    nothing about people who were turned away. This reports the offer rate for
    each outcome and flags the gap.
    """
    groups = {"screened_in": [], "screened_out": [], "other": []}
    for r in records:
        if r["outcome"] == "screened_in":
            groups["screened_in"].append(r)
        elif r["outcome"] in SCREENED_OUT:
            groups["screened_out"].append(r)
        else:
            groups["other"].append(r)
    out = {}
    for g, rs in groups.items():
        offered = [r for r in rs if (r.get("survey") or {}).get("offered") is True]
        responded = [r for r in offered if r["survey"].get("responded") is True]
        scores = [r["survey"]["satisfaction_1_5"] for r in responded if r["survey"].get("satisfaction_1_5") is not None]
        out[g] = {
            "offered": rate(len(offered), len(rs)),
            "response_rate": rate(len(responded), len(offered)),
            "mean_satisfaction": _mean(scores),
        }
    in_off = out["screened_in"]["offered"]
    out_off = out["screened_out"]["offered"]
    flag = False
    if in_off and out_off and in_off["rate"] > 0:
        flag = out_off["rate"] < in_off["rate"] / 2
    out["screened_out_under_surveyed"] = flag
    return out


def _median(xs):
    return round(statistics.median(xs), 2) if xs else None


def _mean(xs):
    return round(statistics.fmean(xs), 3) if xs else None


def _group_value(r, dim):
    if dim in ("age_band", "disability_reported", "rural", "county_fips"):
        v = (r.get("demographics") or {}).get(dim)
    else:
        v = r.get(dim)
    return "unknown" if v is None else str(v).lower() if isinstance(v, bool) else str(v)


def breakdown(records, dim, min_cell=10):
    """Headline indicators per value of one field; groups under min_cell are suppressed."""
    groups = {}
    for r in records:
        groups.setdefault(_group_value(r, dim), []).append(r)
    out = {}
    for value in sorted(groups):
        rs = groups[value]
        if len(rs) < min_cell:
            out[value] = {"suppressed": True, "records": f"<{min_cell}"}
            continue
        m = metrics(rs)
        out[value] = {
            "records": m["records"],
            "completion_rate": m["completion_rate"],
            "screen_in_rate": m["screen_in_rate"],
            "wrongful_screen_out_rate": m["review"]["wrongful_screen_out_rate"],
            "eligible_turned_away_share": m["review"]["population_estimates"]["eligible_turned_away_share"],
            "safety_flag_recall": m["review"]["safety_flag_recall"],
            "human_transfer_on_request": m["human_transfer_on_request"],
            "abandoned": sum(m["abandoned_by_step"].values()),
        }
    return out


def report(records, by=("site", "period", "channel", "language"), min_cell=10):
    """Validate, then build the full report. Raises ValueError on invalid records."""
    problems = []
    seen = set()
    for i, r in enumerate(records):
        for e in validate(r):
            problems.append(f"record {i + 1}: {e}")
        rid = r.get("record_id") if isinstance(r, dict) else None
        if rid in seen:
            problems.append(f"record {i + 1}: duplicate record_id")
        seen.add(rid)
    if problems:
        raise ValueError("; ".join(problems[:20]))
    return {
        "schema_version": SCHEMA_VERSION,
        "min_cell": min_cell,
        "overall": metrics(records),
        "by": {dim: breakdown(records, dim, min_cell) for dim in by},
        "note": "Evaluation indicators computed from de-identified records; not legal advice.",
    }


def synthetic(n=400, seed=7, sites=("site_a", "site_b")):
    """Made-up records for demos and tests. They describe no real person or program."""
    rng = random.Random(seed)
    records = []
    for i in range(n):
        period = "baseline" if i % 2 == 0 else "pilot"
        channel = "human_phone" if period == "baseline" else "ai_voice"
        lang = rng.choices(["en", "es", "ht"], weights=[75, 20, 5])[0]
        consent = rng.choices(CONSENT, weights=[88, 4, 8])[0]
        if consent != "given":
            outcome = "abandoned" if consent == "not_reached" else "other"
        else:
            outcome = rng.choices(
                ["screened_in", "screened_out_referred", "screened_out_no_referral",
                 "transferred_to_human", "abandoned"],
                weights=[38, 30, 8, 9, 15])[0]
        r = {
            "schema_version": SCHEMA_VERSION,
            "record_id": f"syn-{seed}-{i:05d}",
            "site": sites[(i // 2) % len(sites)],
            "period": period,
            "channel": channel,
            "date": f"2027-{1 + i % 4:02d}-{1 + i % 28:02d}",
            "language": lang,
            "consent": consent,
            "outcome": outcome,
            "after_hours": channel == "ai_voice" and rng.random() < 0.3,
            "duration_seconds": rng.randint(60, 900),
            "required_fields_total": 20,
            "required_fields_filled": rng.randint(14, 20),
            "human_requested": rng.random() < 0.08,
            "safety_flag_ai": rng.random() < 0.1,
            "demographics": {
                "age_band": rng.choice(AGE_BANDS),
                "disability_reported": rng.random() < 0.15,
                "rural": rng.random() < 0.35,
            },
        }
        if outcome == "abandoned":
            r["abandoned_at_step"] = "consent" if consent == "not_reached" else rng.choice(STEPS[2:])
        if channel == "ai_voice" and consent != "not_reached":
            r["disclosures"] = {k: rng.random() < 0.97 for k in DISCLOSURE_KEYS}
        if r["human_requested"]:
            r["human_transfer_completed"] = rng.random() < 0.9
        if outcome in DECIDED and rng.random() < 0.3:
            ai_in = outcome == "screened_in"
            agree = rng.random() < 0.88
            r["review"] = {
                "reviewer_eligibility": "eligible" if (ai_in == agree) else "ineligible",
                "should_screen_in": ai_in if agree else not ai_in,
                "safety_indicator_present": r["safety_flag_ai"] or rng.random() < 0.03,
                "legal_advice_given": False,
                "data_errors": rng.choice([0, 0, 0, 1, 2]),
                "random_sample": True,
            }
        if outcome == "screened_in":
            done = rng.random() < 0.85
            r["callback"] = {"needed": True, "completed": done,
                             "hours_to_callback": round(rng.uniform(2, 96), 1) if done else None}
            r["case_opened"] = rng.random() < 0.6
        if outcome in DECIDED:
            offered = rng.random() < (0.6 if outcome == "screened_in" else 0.2)
            responded = offered and rng.random() < 0.4
            r["survey"] = {"offered": offered, "responded": responded,
                           "satisfaction_1_5": rng.randint(2, 5) if responded else None}
        records.append(r)
    return records


def read_jsonl(path):
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("records", nargs="?", help="JSON Lines file of de-identified contact records")
    ap.add_argument("--by", default="site,period,channel,language",
                    help="comma-separated fields to break results down by")
    ap.add_argument("--min-cell", type=int, default=10, help="suppress groups smaller than this")
    ap.add_argument("--synthetic", type=int, metavar="N", help="print N made-up records instead")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--plan-zero", type=float, metavar="UPPER",
                    help="reviews per outcome so that finding no errors caps the rate at UPPER")
    ap.add_argument("--plan-margin", type=float, nargs=2, metavar=("EXPECTED", "MARGIN"),
                    help="reviews per outcome for a 95%% interval of +/- MARGIN around EXPECTED")
    args = ap.parse_args(argv)
    if args.plan_zero is not None or args.plan_margin is not None:
        plan = {}
        if args.plan_zero is not None:
            plan["zero_found"] = {"upper": args.plan_zero, "reviews_per_outcome": plan_zero_found(args.plan_zero)}
        if args.plan_margin is not None:
            expected, margin = args.plan_margin
            plan["margin"] = {"expected": expected, "margin": margin,
                              "reviews_per_outcome": plan_margin(expected, margin)}
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0
    if args.synthetic:
        for r in synthetic(args.synthetic, args.seed):
            print(json.dumps(r, sort_keys=True))
        return 0
    if not args.records:
        ap.error("give a records file, --synthetic N, --plan-zero or --plan-margin")
    by = tuple(d for d in args.by.split(",") if d)
    try:
        out = report(read_jsonl(args.records), by=by, min_cell=args.min_cell)
    except ValueError as e:
        print(f"invalid records: {e}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
