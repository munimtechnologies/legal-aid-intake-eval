# legal-aid-intake-eval

An open record format and reference metrics for evaluating **AI intake in legal aid**: voice agents, chat or online screeners, set against the traditional intake they sit beside, across one or more organisations.

Legal aid programs are now piloting AI voice agents that answer the intake line, screen eligibility and write to the case management system. Funders ask whether those pilots work, and a shared way of measuring them is still missing. The pieces that exist are:

- a pre-deployment **quality bar**: the Legal Help Commons draft *Voice AI Systems: Conformance Standards* (Stanford Legal Design Lab and the Voice AI Intake Cohort; draft for comment). It sets gates and thresholds and comes with a scenario test suite;
- **single-pilot write-ups**, such as the Legal Aid Chicago family-law voice pilot on JusticeBench (417 calls, August 2025). That pilot's satisfaction survey went only to callers who were screened in and later called back, and no independent evaluation is described;
- a student prototype rubric (Stanford "AI for Legal Help", with the Legal Aid Society of San Bernardino), with no published data or schema.

What none of them gives is a **de-identified, per-contact record that two programs can fill in the same way**, plus code that turns it into comparable numbers. This repository is a first draft of that.

## What is here

| Path | What it is |
|---|---|
| `schema/call_record.schema.json` | JSON Schema (2020-12) for one intake contact: site, baseline or pilot period, channel, language, consent, outcome, where callers dropped off, disclosures delivered, human transfer on request, safety flag, a human-review block, callback, case opened, banded demographics and survey offer/response. No names, phone numbers, addresses, birth dates, transcripts or free text, by design. |
| `intake_eval.py` | Validator and metrics, standard library only. |
| `docs/indicators.md` | Each indicator: definition, which data it comes from, and how it maps to LSC TIG evaluation plans (goals, objectives; administrative, survey and qualitative data). |
| `test_intake_eval.py` | Tests, including that the schema file and the code agree. |

## Indicators computed

For the whole file and for each site, period, channel, language or demographic group (groups under `--min-cell`, default 10, are suppressed):

- **Funnel**: consent rate, completion rate (completed ÷ consented), abandonment by step, screen-in rate, referral rate when screened out, required-field completeness, median duration, after-hours share.
- **Human review sample**: wrongful screen-out rate (screened out, reviewer says should have been screened in), wrongful screen-in rate, agreement and Cohen's kappa with reviewers, safety-flag recall, reviewer-found legal-advice incidents, data errors per reviewed call.
- **AI conduct**: share of contacts where each disclosure was delivered (AI self-identification, not-a-lawyer, consent captured, follow-up timeline); human transfer completed when asked.
- **Downstream**: callback completion, median hours to callback, cases opened when screened in.
- **Survey coverage by outcome**: who was *offered* feedback, split by screened in, screened out and other, with a flag when screened-out callers were offered it at under half the rate of screened-in callers. Satisfaction gathered only from people who got help says nothing about people who were turned away.

Every proportion comes with its count, denominator and a 95% Wilson interval, because pilot samples are small.

## Use

```
python intake_eval.py --synthetic 400 --seed 7 > synthetic.jsonl
python intake_eval.py synthetic.jsonl --by site,period,channel,language,rural > report.json
python -m unittest -v
```

`--synthetic` prints made-up records that describe no real person or program. They are for trying the tool, not evidence of anything.

The validator rejects any field outside the schema and any value shaped like an email address, US phone number or SSN. (A pseudonymous id that happens to look like a phone number is rejected too; use letters in ids.)

## Limits

- Version 0.1.0 is a draft, not yet used on a live program. Field names will change once it meets real LegalServer exports and real evaluation plans.
- It measures intake, not legal outcomes. Whether a screened-in case was helped needs case-closing data that differs by program.
- The human-review numbers are only as good as the sample. Draw it at random, within each outcome, and include screened-out callers.
- Thresholds are left to each program and its funder. Where the Legal Help Commons draft standard sets them, use that document; this repository does not restate it.
- Contact data about people seeking legal help is sensitive. Run this where the program's own data rules allow. It needs no network and sends nothing anywhere.

## Licence and credits

Code: Apache-2.0. Schema and documentation: CC BY 4.0.

By MV2 of Munim, Inc., made with AI agents working in his name. This is an evaluation aid for programs and funders. It is not legal advice and does not decide anyone's eligibility.
