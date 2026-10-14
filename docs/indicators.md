# Indicators (version 0.2.0)

Each indicator below is computed by `intake_eval.py` from records that follow `schema/call_record.schema.json`. Every proportion is reported with its count (x), denominator (n) and a 95% interval (Wilson for single rates, simulation for the population estimates).

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

A reviewer (an intake supervisor or advocate at the program) reads a random sample of contacts **within each outcome**, so screened-out callers are reviewed too, and fills the `review` block. Reviews chosen for any other reason (a complaint, a flag, staff choice) carry `random_sample: false`.

| Indicator | Definition | Kind |
|---|---|---|
| Wrongful screen-out rate (elusion) | reviewed screened-out contacts the reviewer says should have been screened in ÷ reviewed screened-out contacts | administrative (review) |
| Wrongful screen-in rate | reviewed screened-in contacts the reviewer says should not have been ÷ reviewed screened-in contacts | administrative (review) |
| Agreement, Cohen's kappa | AI decision against reviewer decision over all reviewed decisions (within the sample, unweighted) | administrative (review) |
| Safety-flag recall | contacts where the AI raised a safety flag ÷ contacts the reviewer found a safety indicator in | administrative (review) |
| Legal-advice incidents | count of reviewed contacts where the reviewer found the AI gave specific legal advice, with the number reviewed | administrative (review) |
| Data errors per reviewed call | mean of `review.data_errors` | administrative (review) |

Sample agreement and kappa treat every reviewed call alike. When 30 calls are reviewed from a group of 800 and 30 from a group of 200, they over-weight the smaller group. Use the weighted accuracy below for any figure about all decisions.

## Population estimates (new in 0.2.0)

Every contact is a record, so the size of each outcome group (screened in, screened out with a referral, screened out without one) is known. The random review sample in each group is scaled up to that group. This is the stratified form of the e-discovery elusion test.

| Indicator | Definition | Kind |
|---|---|---|
| Eligible callers turned away (estimate) | sum over screened-out groups of group size × share of its random sample that should have been screened in | administrative (review) |
| Share of eligible callers turned away | eligible callers turned away ÷ (eligible callers turned away + screened-in group size × share of its random sample that should have been screened in) | administrative (review) |
| Weighted accuracy | sum over groups of group size × share of its random sample decided correctly ÷ all decisions | administrative (review) |

Intervals: 10,000 simulated draws with a Jeffreys prior, Beta(x + ½, n − x + ½), for each group's rate; the 2.5th and 97.5th percentiles of each estimate. The seed is fixed, so the same file always gives the same report. When a group that had decisions has no random reviews, the estimates are left empty and the report names the group.

**Why elusion is not enough.** Two periods, each with 1,000 decisions and 30 random reviews per outcome (the made-up example reproduced in `test_intake_eval.py`):

| | Screened in / out | Errors in 30 screened in | Elusion (screened out) | Eligible turned away | Share of eligible turned away | Weighted accuracy |
|---|---|---|---|---|---|---|
| Baseline | 800 / 200 | 1 | 3 of 30 = 10.0% | 20 | 2.5% | 95.3% |
| Pilot | 500 / 500 | 1 | 2 of 30 = 6.7% | 33 | 6.5% | 95.0% |

The pilot has lower elusion and almost the same accuracy, yet it turns away 2.6 times the share of eligible callers, because it screens out more than twice as many people. Compare intakes on the share of eligible callers turned away, with its interval, never on elusion alone.

**Sizing the review.** `--plan-zero U` gives the number of random reviews per outcome at which finding no errors caps that outcome's rate at U (73 for 5%, 35 for 10%). `--plan-margin P M` gives the number for a 95% interval of ±M around an expected rate P. Agree the sizes with each site before the pilot, so reviewer time is predictable.

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

`--by` splits every headline indicator by any of `site`, `period` (baseline or pilot), `channel`, `language`, `after_hours`, `age_band`, `disability_reported`, `rural` or `county_fips`. That includes the share of eligible callers turned away, computed within each group. Groups smaller than `--min-cell` (default 10) are suppressed, both for privacy and because rates on tiny groups mislead. A random sample drawn within each outcome for the whole site is still random within a language or county, but it may hold very few calls there. Expect wide intervals, or draw extra reviews in the groups the evaluation must report on.

For a baseline-versus-pilot comparison, read the two periods' intervals side by side. Overlapping 95% intervals do not by themselves show "no difference", and with pilot-sized samples many real differences will not be detectable. Say so in the report rather than over-reading a point estimate.

## Mapping to an evaluation plan

LSC asks a TIG evaluation plan for one goal and several measurable objectives. A typical mapping for an AI intake project:

| Plausible objective | Indicators |
|---|---|
| More people can reach intake, including after hours | after-hours share, consent rate, completion rate and abandonment by step (pilot against baseline) |
| Screening is at least as accurate and safe as before | share of eligible callers turned away, weighted accuracy, wrongful screen-in rate, safety-flag recall, legal-advice incidents |
| Staff time moves to eligible clients | screen-in rate, field completeness, data errors, callback completion and time, plus staff interviews |
| Equitable access | every funnel and accuracy indicator by language, age band, disability, rural |
| Callers are treated well, including those turned away | survey offered and satisfaction by outcome, plus caller interviews |

The objectives are examples. Each program's approved evaluation plan governs.

Licence: CC BY 4.0. By MV2 of Munim, Inc., made with AI agents working in his name. Not legal advice.
