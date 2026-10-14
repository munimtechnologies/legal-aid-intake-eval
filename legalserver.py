# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Munim, Inc.
"""Turn a LegalServer report export into de-identified intake records.

Every LegalServer report can be exported to CSV. A program builds one Case
Data report with the columns it has, writes a small mapping file saying which
column is which and what its own lookup values mean, and this script writes
records in the format of schema/call_record.schema.json for intake_eval.py.

Only the mapped columns are read. The Case ID never reaches the records:
record_id is a keyed hash (HMAC-SHA256) of it, with a key the program keeps,
so nobody without the key can link a record to a case by hashing known ids.
Ages become bands; counties become FIPS codes only where the mapping gives one.

It also draws the human review sample at random within each screening outcome,
as a sheet of Case IDs for the program's own reviewers, and joins their answers
back. See docs/legalserver.md.

Standard library only. Runs offline. This is an evaluation aid, not legal advice.

    python legalserver.py export.csv --mapping mapping.json --draw-review 35 --seed 1 > review_sheet.csv
    python legalserver.py export.csv --mapping mapping.json --key-file key.txt --reviews review_sheet.csv > records.jsonl
    python intake_eval.py records.jsonl --by period,channel,language > report.json
"""
import argparse
import csv
import datetime
import hashlib
import hmac
import json
import math
import random
import re
import sys

import intake_eval as ie

# LegalServer's disposition is a fixed system field with these six values
# (help.legalserver.org/article/1632).
DISPOSITIONS = ("Prescreen", "Incomplete Intake", "Pending", "Open", "Closed", "Rejected Matter")
DISPOSITION_ALIASES = {"rejected": "Rejected Matter"}

# What each disposition means for intake evaluation, unless the mapping says otherwise.
# "screened_out" is resolved to referred, not referred or conflict from the rejection reason.
DEFAULT_OUTCOMES = {
    "Prescreen": "other",
    "Incomplete Intake": "abandoned",
    "Pending": "screened_in",
    "Open": "screened_in",
    "Closed": "screened_in",
    "Rejected Matter": "screened_out",
}
CASE_OPENED = {"Open": True, "Closed": True, "Pending": False}
OUTCOME_CHOICES = ie.OUTCOMES + ("screened_out", "drop")

# Common LegalServer language lookup values; extend or override with language_values.
LANGUAGES = {
    "english": "en", "spanish": "es", "haitian creole": "ht", "french": "fr", "portuguese": "pt",
    "vietnamese": "vi", "chinese": "zh", "mandarin": "cmn", "cantonese": "yue", "korean": "ko",
    "japanese": "ja", "tagalog": "tl", "arabic": "ar", "russian": "ru", "ukrainian": "uk",
    "polish": "pl", "german": "de", "italian": "it", "farsi": "fa", "persian": "fa", "dari": "prs",
    "pashto": "ps", "urdu": "ur", "hindi": "hi", "punjabi": "pa", "bengali": "bn", "gujarati": "gu",
    "nepali": "ne", "burmese": "my", "khmer": "km", "lao": "lo", "hmong": "hmn", "somali": "so",
    "amharic": "am", "tigrinya": "ti", "swahili": "sw", "mam": "mam", "k'iche'": "quc",
    "american sign language": "ase", "other": "mis", "unknown": "und",
}

MAPPING_KEYS = (
    "site", "period", "consent", "columns", "channel_values", "channel_default", "language_values",
    "dispositions", "referred_reasons", "referred_codes", "conflict_reasons", "drop_reasons",
    "counties", "disability_values", "note",
)
COLUMN_KEYS = ("case_id", "date", "disposition", "reject_reason", "referral_code", "channel",
               "language", "age", "county", "disability")
REQUIRED_COLUMNS = ("case_id", "date", "disposition")
LIST_KEYS = ("referred_reasons", "referred_codes", "conflict_reasons", "drop_reasons")
DICT_KEYS = ("channel_values", "language_values", "dispositions", "counties", "disability_values")

REVIEW_COLUMNS = ("case_id", "should_screen_in", "reviewer_eligibility", "safety_indicator_present",
                  "legal_advice_given", "data_errors", "random_sample")
_YES = ("yes", "y", "true", "1")
_NO = ("no", "n", "false", "0")
_BLANK = ("", "unclear", "unknown", "n/a")

MIN_KEY_BYTES = 16

_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_US = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def _norm(value):
    return " ".join(str(value).split()).casefold()


def _lookup(d):
    return {_norm(k): v for k, v in (d or {}).items()}


def _cell(row, column):
    if not column:
        return ""
    v = row.get(column)
    return "" if v is None else str(v).strip()


def parse_date(value):
    """YYYY-MM-DD from an ISO or US (M/D/YYYY) date, ignoring any time of day; None if unreadable."""
    if not isinstance(value, str):
        return None
    s = value.strip().split("T")[0].split(" ")[0]
    m = _ISO.match(s)
    if m:
        y, mo, d = m.groups()
    else:
        m = _US.match(s)
        if not m:
            return None
        mo, d, y = m.groups()
    try:
        return datetime.date(int(y), int(mo), int(d)).isoformat()
    except ValueError:
        return None


def age_band(value):
    """Schema age band for an age in years; 'unknown' when blank or implausible."""
    try:
        age = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if not math.isfinite(age) or age < 0 or age > 120:
        return "unknown"
    if age < 18:
        return "under_18"
    if age < 25:
        return "18_24"
    if age < 45:
        return "25_44"
    if age < 65:
        return "45_64"
    return "65_plus"


def record_id(key, site, case_id):
    """Pseudonymous id: a keyed hash of site and Case ID. Without the key it cannot be recomputed."""
    digest = hmac.new(key, f"{site}\x1f{case_id}".encode("utf-8"), hashlib.sha256).hexdigest()
    return "ls-" + digest[:24]


def check_mapping(m):
    """Return a list of problems with a mapping; empty means usable."""
    if not isinstance(m, dict):
        return ["mapping must be a JSON object"]
    problems = []
    for k in m:
        if k not in MAPPING_KEYS:
            problems.append(f"{k} is not a mapping key")
    site = m.get("site")
    if not (isinstance(site, str) and ie._SITE.match(site)):
        problems.append("site must be 2-32 letters, digits, _ or -")
    period = m.get("period")
    if isinstance(period, dict):
        start = period.get("pilot_start")
        if set(period) != {"pilot_start"} or parse_date(start) != start:
            problems.append('period must be "baseline", "pilot" or {"pilot_start": "YYYY-MM-DD"}')
    elif period not in ie.PERIODS:
        problems.append('period must be "baseline", "pilot" or {"pilot_start": "YYYY-MM-DD"}')
    if m.get("consent") not in ie.CONSENT:
        problems.append(f"consent must be one of {ie.CONSENT} (LegalServer has no standard consent field)")

    cols = m.get("columns")
    if not isinstance(cols, dict):
        problems.append("columns must be an object")
        cols = {}
    for k, v in cols.items():
        if k not in COLUMN_KEYS:
            problems.append(f"columns.{k} is not a column key")
        elif not (isinstance(v, str) and v.strip()):
            problems.append(f"columns.{k} must be a column header")
    for k in REQUIRED_COLUMNS:
        if k not in cols:
            problems.append(f"columns.{k} is required")

    for k in DICT_KEYS:
        if k in m and not isinstance(m[k], dict):
            problems.append(f"{k} must be an object")
    for k in LIST_KEYS:
        v = m.get(k, [])
        if not (isinstance(v, list) and all(isinstance(s, str) for s in v)):
            problems.append(f"{k} must be a list of lookup values")
    if problems:
        return problems

    if "channel" not in cols and "channel_default" not in m:
        problems.append("give columns.channel or channel_default")
    if "channel_default" in m and m["channel_default"] not in ie.CHANNELS:
        problems.append(f"channel_default must be one of {ie.CHANNELS}")
    for k, v in m.get("channel_values", {}).items():
        if v not in ie.CHANNELS:
            problems.append(f"channel_values[{k!r}] must be one of {ie.CHANNELS}")
    for k, v in m.get("language_values", {}).items():
        if not (isinstance(v, str) and ie._LANGUAGE.match(v)):
            problems.append(f"language_values[{k!r}] must be a BCP 47 tag such as en or es")
    for k, v in m.get("dispositions", {}).items():
        if k not in DISPOSITIONS:
            problems.append(f"dispositions key {k!r} must be one of {DISPOSITIONS}")
        if v not in OUTCOME_CHOICES:
            problems.append(f"dispositions[{k!r}] must be one of {OUTCOME_CHOICES}")
    for k, c in m.get("counties", {}).items():
        if not (isinstance(c, dict) and isinstance(c.get("fips"), str) and ie._FIPS.match(c["fips"])):
            problems.append(f"counties[{k!r}] needs a 5-digit fips")
        elif set(c) - {"fips", "rural"} or not isinstance(c.get("rural", False), bool):
            problems.append(f"counties[{k!r}] may hold only fips and rural (true or false)")
    for k, v in m.get("disability_values", {}).items():
        if not isinstance(v, bool):
            problems.append(f"disability_values[{k!r}] must be true or false")
    return problems


def classify(rows, mapping):
    """Read report rows (dicts from csv.DictReader) into records without record_id.

    Returns (pairs, problems, stats). pairs is a list of (case_id, record); the
    case id stays with the caller and is never written into a record.
    """
    stats = {"rows": len(rows), "records": 0, "dropped": 0}
    if not rows:
        return [], ["the export has no rows"], stats
    cols = mapping["columns"]
    header = set(rows[0])
    missing = [h for h in cols.values() if h not in header]
    if missing:
        return [], [f"column {h!r} is not in the export" for h in missing], stats

    dispositions = {_norm(d): d for d in DISPOSITIONS}
    dispositions.update(DISPOSITION_ALIASES)
    outcomes = dict(DEFAULT_OUTCOMES)
    outcomes.update(mapping.get("dispositions", {}))
    referred_reasons = {_norm(s) for s in mapping.get("referred_reasons", [])}
    referred_codes = {_norm(s) for s in mapping.get("referred_codes", [])}
    conflict_reasons = {_norm(s) for s in mapping.get("conflict_reasons", [])}
    drop_reasons = {_norm(s) for s in mapping.get("drop_reasons", [])}
    channels = _lookup(mapping.get("channel_values"))
    channel_default = mapping.get("channel_default")
    languages = dict(LANGUAGES)
    languages.update(_lookup(mapping.get("language_values")))
    counties = _lookup(mapping.get("counties"))
    disability = _lookup(mapping.get("disability_values"))
    period = mapping["period"]

    pairs, problems, seen = [], [], set()
    for i, row in enumerate(rows, start=2):  # line 1 of the CSV is the header
        where = f"row {i}"
        case_id = _cell(row, cols["case_id"])
        if not case_id:
            problems.append(f"{where}: no case id")
            continue
        if case_id in seen:
            problems.append(f"{where}: case id repeated (a report column with several values per case?)")
            continue
        seen.add(case_id)

        raw = _cell(row, cols["disposition"])
        disposition = dispositions.get(_norm(raw))
        if disposition is None:
            problems.append(f"{where}: disposition {raw!r} is not one of {DISPOSITIONS}")
            continue
        reason = _norm(_cell(row, cols.get("reject_reason")))
        code = _norm(_cell(row, cols.get("referral_code")))
        outcome = outcomes[disposition]
        if outcome == "drop" or (reason and reason in drop_reasons):
            stats["dropped"] += 1
            continue
        if outcome == "screened_out":
            if reason and reason in conflict_reasons:
                outcome = "conflict_hold"
            elif (reason and reason in referred_reasons) or (code and code in referred_codes):
                outcome = "screened_out_referred"
            else:
                outcome = "screened_out_no_referral"

        raw = _cell(row, cols["date"])
        date = parse_date(raw)
        if date is None:
            problems.append(f"{where}: date {raw!r} is not YYYY-MM-DD or M/D/YYYY")
            continue

        if "channel" in cols:
            raw = _cell(row, cols["channel"])
            channel = channels.get(_norm(raw), channel_default)
            if channel is None:
                problems.append(f"{where}: intake type {raw!r} is not in channel_values")
                continue
        else:
            channel = channel_default

        raw = _cell(row, cols.get("language"))
        language = languages.get(_norm(raw)) if raw else "und"
        if language is None:
            problems.append(f"{where}: language {raw!r} is not in language_values")
            continue

        record = {
            "schema_version": ie.SCHEMA_VERSION,
            "site": mapping["site"],
            "period": period if isinstance(period, str) else
            ("pilot" if date >= period["pilot_start"] else "baseline"),
            "channel": channel,
            "date": date,
            "language": language,
            "consent": mapping["consent"],
            "outcome": outcome,
        }
        if outcome == "screened_in" and disposition in CASE_OPENED:
            record["case_opened"] = CASE_OPENED[disposition]

        demographics = {}
        if "age" in cols:
            demographics["age_band"] = age_band(_cell(row, cols["age"]))
        county = counties.get(_norm(_cell(row, cols.get("county"))))
        if county:
            demographics["county_fips"] = county["fips"]
            if "rural" in county:
                demographics["rural"] = county["rural"]
        flag = disability.get(_norm(_cell(row, cols.get("disability"))))
        if flag is not None:
            demographics["disability_reported"] = flag
        if demographics:
            record["demographics"] = demographics
        pairs.append((case_id, record))
    stats["records"] = len(pairs)
    return pairs, problems, stats


def convert(rows, mapping, key):
    """Records with keyed record ids. Returns (pairs, problems, stats); raises ValueError on a short key."""
    if not isinstance(key, bytes) or len(key) < MIN_KEY_BYTES:
        raise ValueError(f"the key must be at least {MIN_KEY_BYTES} bytes")
    pairs, problems, stats = classify(rows, mapping)
    out = []
    for case_id, record in pairs:
        record = dict(record, record_id=record_id(key, mapping["site"], case_id))
        errors = ie.validate(record)
        if errors:
            problems.append(f"case {len(out) + 1}: " + "; ".join(errors))
        out.append((case_id, record))
    return out, problems, stats


def draw_review(pairs, per_outcome, seed=1):
    """Up to per_outcome Case IDs drawn at random within each screening outcome, sorted.

    The draw does not depend on the export's row order. The sheet lists Case IDs
    only, not which outcome each came from.
    """
    if per_outcome < 1:
        raise ValueError("per_outcome must be at least 1")
    rng = random.Random(seed)
    picked = []
    for outcome in ie.DECIDED:
        ids = sorted(c for c, r in pairs if r["outcome"] == outcome)
        picked += rng.sample(ids, min(per_outcome, len(ids)))
    return sorted(picked)


def _yes_no(value):
    v = _norm(value)
    if v in _YES:
        return True
    if v in _NO:
        return False
    if v in _BLANK:
        return None
    raise ValueError(f"{value!r} is not yes, no or blank")


def attach_reviews(pairs, review_rows):
    """Add reviewers' answers from a filled-in review sheet to the records. Returns problems."""
    by_id = {c: r for c, r in pairs}
    problems = []
    for i, row in enumerate(review_rows, start=2):
        if not any(_cell(row, k) for k in REVIEW_COLUMNS):
            continue
        where = f"review row {i}"
        case_id = _cell(row, "case_id")
        if case_id not in by_id:
            problems.append(f"{where}: case id is not in the export")
            continue
        review = {}
        try:
            for k in ("should_screen_in", "safety_indicator_present", "legal_advice_given"):
                v = _yes_no(_cell(row, k))
                if v is not None:
                    review[k] = v
            eligibility = _norm(_cell(row, "reviewer_eligibility"))
            if eligibility:
                if eligibility not in ie.REVIEWER_ELIGIBILITY:
                    raise ValueError(f"reviewer_eligibility must be one of {ie.REVIEWER_ELIGIBILITY}")
                review["reviewer_eligibility"] = eligibility
            errors = _cell(row, "data_errors")
            if errors:
                if not errors.isdigit():
                    raise ValueError("data_errors must be a whole number")
                review["data_errors"] = int(errors)
            random_sample = _yes_no(_cell(row, "random_sample"))
        except ValueError as e:
            problems.append(f"{where}: {e}")
            continue
        if review:
            review["random_sample"] = True if random_sample is None else random_sample
            by_id[case_id]["review"] = review
    return problems


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _fail(problems):
    for p in problems[:20]:
        print(p, file=sys.stderr)
    if len(problems) > 20:
        print(f"... and {len(problems) - 20} more", file=sys.stderr)
    return 2


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("export", help="CSV export of a LegalServer report")
    ap.add_argument("--mapping", required=True, help="JSON file saying which column is which (docs/legalserver.md)")
    ap.add_argument("--key-file", help=f"file holding the program's secret key for record ids ({MIN_KEY_BYTES}+ bytes)")
    ap.add_argument("--reviews", help="filled-in review sheet (CSV) to join to the records")
    ap.add_argument("--draw-review", type=int, metavar="N",
                    help="print a review sheet of N random cases per screening outcome instead")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args(argv)

    with open(args.mapping, encoding="utf-8") as f:
        mapping = json.load(f)
    problems = check_mapping(mapping)
    if problems:
        return _fail(problems)
    rows = read_csv(args.export)

    if args.draw_review is not None:
        pairs, problems, stats = classify(rows, mapping)
        if problems:
            return _fail(problems)
        w = csv.writer(sys.stdout, lineterminator="\n")
        w.writerow(REVIEW_COLUMNS)
        for case_id in draw_review(pairs, args.draw_review, args.seed):
            w.writerow([case_id, "", "", "", "", "", "yes"])
        return 0

    if not args.key_file:
        ap.error("--key-file is needed to make record ids (or use --draw-review)")
    with open(args.key_file, "rb") as f:
        key = f.read().strip()
    try:
        pairs, problems, stats = convert(rows, mapping, key)
    except ValueError as e:
        return _fail([str(e)])
    if args.reviews and not problems:
        problems = attach_reviews(pairs, read_csv(args.reviews))
        for _, record in pairs:
            problems += ie.validate(record)
    if problems:
        return _fail(problems)
    for _, record in pairs:
        print(json.dumps(record, sort_keys=True))
    print(f"{stats['records']} records from {stats['rows']} rows ({stats['dropped']} dropped)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
