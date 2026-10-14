# Indicators (version 0.1.0)

Each indicator below is computed by `intake_eval.py` from records that follow `schema/call_record.schema.json`. Every proportion is reported with its count (x), denominator (n) and a 95% Wilson interval.

LSC's guidance on TIG evaluation plans names three broad kinds of evidence: **administrative** data (for example case management system counts by region or group, and system test results), **survey** data, and **qualitative** data from interviews, observation and focus groups, and says the strongest evaluations combine them ([TIG Project Evaluation Plan: Identifying Evaluation Methods and Data Sets](https://www.lsc.gov/i-am-grantee/lsc-reporting-requirements/tig-reporting/tig-evaluation-plans/tig-project-evaluation-1)). The column "Kind" uses those three words. This file covers the first two. Qualitative work (staff interviews, caller interviews, observation of intake shifts) has no place in a per-contact record and should be planned alongside it.

## Access and the funnel

| Indicator | Definition | Kind | Source fields |
|---|---|---|---|
| After-hours share | contacts outside staffed hours ÷ contacts with `after_hours` recorded | administrative | `after_hours` |
| Consent rate | consent given ÷ contacts that reached the consent step | administrative | `consent` |
| Completion rate | consented contacts not abandoned ÷ consented contacts | administrative | `consent`, `outcome` |
| Abandonment by step | count of `abandoned` contacts by `abandoned_at_step` | administrative | `outcome`, `abandoned_at_step` |
| Screen-in rate | screened in ÷ completed contacts with a screening decision | administrative | `outcome` |
| Referral rate when screened out | screened out with a referral ÷ all screened out | administrative | `outcome` |
| Field completeness | required fields populated ÷ required fields, summed over completed contacts | administrative | `required_fields_total`, `required_fields_filled` |
| Median duration | median `duration_seconds` of completed contacts | administrative | `duration_seconds` |

## Accuracy and safety (human review sample)

A reviewer (an intake supervisor or advocate at the program) reads a random sample of contacts **within each outcome**, so screened-out callers are reviewed too, and fills the `review` block.

| Indicator | Definition | Kind |
|---|---|---|
| Wrongful screen-out rate | reviewed screened-out contacts the reviewer says should have been screened in ÷ reviewed screened-out contacts | administrative (review) |
| Wrongful screen-in rate | reviewed screened-in contacts the reviewer says should not have been ÷ reviewed screened-in contacts | administrative (review) |
| Agreement, Cohen's kappa | AI decision against reviewer decision over all reviewed decisions | administrative (review) |
| Safety-flag recall | contacts where the AI raised a safety flag ÷ contacts the reviewer found a safety indicator in | administrative (review) |
| Legal-advice incidents | count of reviewed contacts where the reviewer found the AI gave specific legal advice, with the number reviewed | administrative (review) |
| Data errors per reviewed call | mean of `review.data_errors` | administrative (review) |

Because the sample is drawn within each outcome, report these rates per outcome as they are. Do not reweight them into one overall accuracy figure unless the sampling fractions are recorded.

## Conduct of the AI

| Indicator | Definition | Kind |
|---|---|---|
| Disclosures delivered | for each of AI self-identification, not-a-lawyer, consent captured and follow-up timeline: delivered ÷ contacts where it was checked | administrative (logs or sampled transcripts) |
| Human transfer on request | transfers completed ÷ contacts where the caller asked for a person | administrative |

## Downstream

| Indicator | Definition | Kind |
|---|---|---|
| Callback completion | callbacks completed ÷ callbacks needed | administrative |
| Median hours to callback | over completed callbacks | administrative |
| Case opened when screened in | cases opened ÷ screened-in contacts with `case_opened` known | administrative |

## Caller experience and who was asked

| Indicator | Definition | Kind |
|---|---|---|
| Survey offered, by outcome | offered ÷ contacts, separately for screened in, screened out and other | survey |
| Response rate, by outcome | responded ÷ offered | survey |
| Mean satisfaction, by outcome | mean of `satisfaction_1_5` among responses | survey |
| Screened-out under-surveyed (flag) | true when screened-out callers were offered the survey at under half the rate of screened-in callers | survey |

## Comparisons and equity

`--by` splits every headline indicator by any of `site`, `period` (baseline or pilot), `channel`, `language`, `after_hours`, `age_band`, `disability_reported`, `rural` or `county_fips`. Groups smaller than `--min-cell` (default 10) are suppressed, both for privacy and because rates on tiny groups mislead.

For a baseline-versus-pilot comparison, read the two periods' intervals side by side. Overlapping 95% intervals do not by themselves show "no difference", and with pilot-sized samples many real differences will not be detectable. Say so in the report rather than over-reading a point estimate.

## Mapping to an evaluation plan

LSC asks a TIG evaluation plan for one goal and several measurable objectives. A typical mapping for an AI intake project:

| Plausible objective | Indicators |
|---|---|
| More people can reach intake, including after hours | after-hours share, consent rate, completion rate and abandonment by step (pilot against baseline) |
| Screening is at least as accurate and safe as before | wrongful screen-out and screen-in rates, kappa, safety-flag recall, legal-advice incidents |
| Staff time moves to eligible clients | screen-in rate, field completeness, data errors, callback completion and time, plus staff interviews |
| Equitable access | every funnel and accuracy indicator by language, age band, disability, rural |
| Callers are treated well, including those turned away | survey offered and satisfaction by outcome, plus caller interviews |

The objectives are examples. Each program's approved evaluation plan governs.

Licence: CC BY 4.0. By MV2 of Munim, Inc., made with AI agents working in his name. Not legal advice.
