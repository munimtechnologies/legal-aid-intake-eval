# legal-aid-intake-eval

An open record format and reference metrics for evaluating **AI intake in legal aid**: voice agents, chat or online screeners, set against the traditional intake they sit beside, across one or more organisations.

Legal aid programs are now piloting AI voice agents that answer the intake line, screen eligibility and write to the case management system. Funders ask whether those pilots work, and a shared way of measuring them is still missing. The pieces that exist are:

- a pre-deployment **quality bar**: the Legal Help Commons draft *Voice AI Systems: Conformance Standards* (Stanford Legal Design Lab and the Voice AI Intake Cohort; draft for comment). It sets gates and thresholds and comes with a scenario test suite;
- **single-pilot write-ups**, such as the Legal Aid Chicago family-law voice pilot on JusticeBench (417 calls, August 2025). That pilot's satisfaction survey went only to callers who were screened in and later called back, and no independent evaluation is described;
- a student prototype rubric (Stanford "AI for Legal Help", with the Legal Aid Society of San Bernardino), with no published data or schema.

What none of them gives is a **de-identified, per-contact record that two programs can fill in the same way**, plus code that turns it into comparable numbers. This repository is a first draft of that.

## New in 0.3.0: start from a LegalServer export

Most US legal aid programs run intake on LegalServer, and the AI voice agents in pilot write into it. `legalserver.py` takes an ordinary LegalServer report exported to CSV, plus a short mapping file written once per site, and:

1. **draws the review sample** at random within each screening outcome (screened in, screened out with a referral, screened out without one), as a sheet of Case IDs for the program's own reviewers. The draw is reproducible from a seed and does not depend on row order;
2. **converts** the export into schema records. LegalServer's six fixed dispositions become screening outcomes, and rejection reasons and referral codes separate referred, conflict and created-in-error rejections. Languages become BCP 47 tags and ages become bands. Counties become FIPS codes and rurality only where the mapping lists them;
3. **joins the reviewers' answers** back, so `intake_eval.py` can estimate how many eligible callers were turned away.

The Case ID never reaches the records. `record_id` is an HMAC-SHA256 of site and Case ID under a key the program keeps, so an evaluator without the key cannot link a record to a case by hashing Case IDs. Only mapped columns are read.

```
python legalserver.py export.csv --mapping mapping.json --draw-review 35 --seed 2027 > review_sheet.csv
python legalserver.py export.csv --mapping mapping.json --key-file key.txt --reviews review_sheet.csv > records.jsonl
python intake_eval.py records.jsonl --by period,channel,language,rural > report.json
```

[`docs/legalserver.md`](docs/legalserver.md) covers which report columns to add and how each disposition is read. It also lists what an export cannot give (AI disclosures, transfers and hang-ups before a record exists live in the voice agent's own logs). It was written from LegalServer's public help pages and has not yet been run on a real program's export, so expect to adjust the mapping on first use. The nearest open tools we found are API clients (CLSPhila's LegalServerReader in R, and a Python wrapper of LegalServer's older API). Neither de-identifies records or draws evaluation samples.

## New in 0.2.0: how many eligible callers were turned away

An eligible caller who is wrongly screened out leaves no case behind, so the error is invisible unless someone looks. Version 0.1.0 reported the **wrongful screen-out rate**: of the screened-out calls a reviewer read, the share that should have been screened in. E-discovery calls this the *elusion* rate, and its known weakness carries over: **elusion alone cannot compare two intakes.** An intake that turns away more people can show lower elusion and still miss more eligible callers.

A worked example (made-up numbers, reproduced in the tests). Each period has 1,000 screening decisions, and a reviewer reads 30 random calls from each outcome:

| | Screened in | Screened out | Errors found in 30 screened in | Errors found in 30 screened out (elusion) | Est. eligible callers turned away | Share of eligible callers turned away |
|---|---|---|---|---|---|---|
| Baseline | 800 | 200 | 1 | 3 (10.0%) | 20 | **2.5%** |
| Pilot | 500 | 500 | 1 | 2 (6.7%) | 33 | **6.5%** |

On elusion the pilot looks better. In fact it turns away about 2.6 times the share of eligible callers.

Version 0.2.0 adds the missing step, borrowed from stratified recall estimation in e-discovery review. Every contact is a record, so the size of each outcome group is known. The reviewed sample in each group is scaled up to the whole group, which gives:

- **eligible callers turned away**: an estimated count, and as a share of all eligible callers, the figure a program and its funder actually care about;
- **weighted accuracy**: agreement with reviewers across all decisions, weighted by group size rather than by how many calls happened to be reviewed;
- 95% intervals by simulation (a Jeffreys prior in each outcome group, fixed seed, so reports are reproducible). Finding no errors still gives an honest upper bound.

It also adds `review.random_sample`. Reviews prompted by a complaint or a flag are useful, but they are not a random sample, so they are kept out of these estimates and counted separately. Also new is a **review-size planner**:

```
python intake_eval.py --plan-zero 0.05        # 73 reviews per outcome: finding none caps the rate at 5%
python intake_eval.py --plan-margin 0.10 0.05 # reviews for +/- 5 points around an expected 10%
```

Prior work: elusion and stratified recall sampling are standard in e-discovery validation. Practitioners warn that a sample from the discard pile alone overstates recall, and recommend sampling every group, as in *In re Broiler Chicken* (Douglas Forrest, ["The Elusion Illusion and the AI Revolution"](https://aceds.org/the-elusion-illusion-and-the-ai-revolution-aceds-blog/), ACEDS, 25 June 2026). We found no legal aid intake evaluation, pilot report or draft standard that estimates the share of eligible callers turned away from a stratified review sample. This version applies known statistics to a new setting. It is not a new statistical method.

## What is here

| Path | What it is |
|---|---|
| `schema/call_record.schema.json` | JSON Schema (2020-12) for one intake contact: site, baseline or pilot period, channel, language, consent, outcome, where callers dropped off, disclosures delivered, human transfer on request, safety flag, a human-review block, callback, case opened, banded demographics and survey offer/response. No names, phone numbers, addresses, birth dates, transcripts or free text, by design. Schema version 0.2.0; 0.1.0 records stay valid. |
| `intake_eval.py` | Validator, metrics and review-size planner, standard library only. |
| `legalserver.py` | LegalServer CSV export to schema records, with keyed record ids, the stratified review-sample draw and the join of reviewers' answers. Standard library only. |
| `docs/indicators.md` | Each indicator: definition, which data it comes from, and how it maps to LSC TIG evaluation plans (goals, objectives; administrative, survey and qualitative data). |
| `docs/legalserver.md`, `docs/legalserver-mapping.example.json` | How to build the LegalServer report and write the mapping. |
| `test_intake_eval.py`, `test_legalserver.py` | Tests, including that the schema file and the code agree, the worked example above, and a made-up LegalServer export end to end. |

## Indicators computed

For the whole file and for each site, period, channel, language or demographic group (groups under `--min-cell`, default 10, are suppressed):

- **Funnel**: consent rate, completion rate (completed ÷ consented), abandonment by step, screen-in rate, referral rate when screened out, required-field completeness, median duration, after-hours share.
- **Human review sample**: wrongful screen-out rate (elusion), wrongful screen-in rate, agreement and Cohen's kappa within the reviewed sample, safety-flag recall, reviewer-found legal-advice incidents, data errors per reviewed call.
- **Population estimates** (0.2.0): eligible callers turned away (count and share of all eligible callers) and weighted accuracy, with 95% intervals.
- **AI conduct**: share of contacts where each disclosure was delivered (AI self-identification, not-a-lawyer, consent captured, follow-up timeline); human transfer completed when asked.
- **Downstream**: callback completion, median hours to callback, cases opened when screened in.
- **Survey coverage by outcome**: who was *offered* feedback, split by screened in, screened out and other, with a flag when screened-out callers were offered it at under half the rate of screened-in callers. Satisfaction gathered only from people who got help says nothing about people who were turned away.

Every proportion comes with its count, denominator and a 95% interval, because pilot samples are small.

## Use

```
python intake_eval.py --synthetic 400 --seed 7 > synthetic.jsonl
python intake_eval.py synthetic.jsonl --by site,period,channel,language,rural > report.json
python -m unittest -v
```

`--synthetic` prints made-up records that describe no real person or program. They are for trying the tool, not evidence of anything.

The validator rejects any field outside the schema and any value shaped like an email address, US phone number or SSN. (A pseudonymous id that happens to look like a phone number is rejected too; use letters in ids.)

## Limits

- Version 0.3.0 is a draft, not yet used on a live program. The LegalServer adapter is built from public documentation, not a real export. Field names may change once the kit meets real exports and real evaluation plans.
- It measures intake, not legal outcomes. Whether a screened-in case was helped needs case-closing data that differs by program.
- The population estimates are only as good as the sample. Draw it at random within each outcome, include screened-out callers, and mark anything else `random_sample: false`. Reviews where the reviewer could not decide (`should_screen_in` null) are left out. If those calls differ from the rest, the estimate is biased, so report how many there were.
- The intervals ignore the finite-population correction, which makes them slightly wide when a large part of a group is reviewed.
- Callers who hang up before a decision are not in the screened-out group. Abandonment by step is reported separately and is not counted as "turned away".
- Thresholds are left to each program and its funder. Where the Legal Help Commons draft standard sets them, use that document; this repository does not restate it.
- Contact data about people seeking legal help is sensitive. Run this where the program's own data rules allow. It needs no network and sends nothing anywhere.

## Licence and credits

Code: Apache-2.0. Schema and documentation: CC BY 4.0.

By MV2 of Munim, Inc., made with AI agents working in his name. This is an evaluation aid for programs and funders. It is not legal advice and does not decide anyone's eligibility.
