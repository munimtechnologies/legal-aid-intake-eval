# From a LegalServer export to evaluation records

Most US legal aid programs run intake on LegalServer, and AI voice agents now in pilot write their intakes into it. `legalserver.py` turns an ordinary LegalServer report export into the de-identified records that `intake_eval.py` reads. It also draws the random review sample that the population estimates need, and joins the reviewers' answers back.

This page was written from LegalServer's public help pages only (sources below). It has not yet been run on a real program's export. Column headers and lookup values differ between sites, which is why everything site-specific lives in a mapping file you write once.

## 1. Build one report

In LegalServer, create a report on **Case Data** ([Creating New Reports](https://help.legalserver.org/article/2601-creating-new-reports)). Filter it to the intake dates you are evaluating, baseline and pilot together, and include every disposition, rejected matters too. Add these columns where your site has them:

| Mapping key | Typical LegalServer column | Needed? | Becomes |
|---|---|---|---|
| `case_id` | Case ID | yes | a keyed hash in `record_id`; the Case ID itself is never written out |
| `date` | Intake Date | yes | `date`, and `period` when the mapping gives a pilot start date |
| `disposition` | Disposition | yes | `outcome` and `case_opened` (table below) |
| `reject_reason` | Rejection Reason | strongly advised | tells referred, conflict and created-in-error rejections apart |
| `referral_code` | LSC Matter Referral Reporting Code | optional | another way to recognise a referral |
| `channel` | Intake Type, or whatever field marks AI-voice intakes at your site | optional | `channel` |
| `language` | Language | optional | `language` (BCP 47) |
| `age` | Age at Intake (Case Data > Person) | optional | `demographics.age_band` |
| `county` | County of Residence (Case Data > Person > Primary Home Address) | optional | `demographics.county_fips` and `rural`, for counties you list |
| `disability` | your site's disability field | optional | `demographics.disability_reported` |

Leave out names, phone numbers, addresses, dates of birth, notes and transcripts. The script reads only the columns named in the mapping and ignores the rest, but a report without them is safer to pass around.

Keep **one row per case**. A column from a subtable that can hold several values per case (for example several languages or several adverse parties) repeats the case, and the script stops with "case id repeated".

Export with **Export to CSV** ([Exporting Data from Reports](https://help.legalserver.org/article/2610-exporting-data-from-reports)). The Reports API gives the same data as JSON or XML ([Reports API](https://help.legalserver.org/article/1751-reports-api)). This script reads the CSV.

## 2. How dispositions become outcomes

LegalServer's disposition is a fixed system field with six values. Users reach them by steps, not by picking ([Disposition and Case Status Compared](https://help.legalserver.org/article/1632-disposition-and-case-status-compared)). The defaults:

| Disposition | Outcome | Why |
|---|---|---|
| Open | `screened_in`, `case_opened: true` | accepted at the end of intake |
| Closed | `screened_in`, `case_opened: true` | accepted, then closed (including accept-and-close for brief service) |
| Pending | `screened_in`, `case_opened: false` | intake complete, not yet accepted as a case |
| Rejected Matter | `screened_out_referred`, `screened_out_no_referral` or `conflict_hold` | from the rejection reason and referral code, using your lists |
| Incomplete Intake | `abandoned` | intake started and not finished |
| Prescreen | `other` | no screening decision yet |

Change any of these with `"dispositions": {"Pending": "other"}`. The allowed values are the schema's outcomes, `"screened_out"` (resolve by rejection reason) and `"drop"`. Rejections whose reason is in `drop_reasons` (for example "Case created in error", which LegalServer suggests for removing cases, see [Delete Cases](https://help.legalserver.org/article/1702-delete-cases)) are left out and counted as dropped.

Rejecting a matter normally asks for a Rejection Reason and an LSC Matter Referral Reporting Code, both from your site's own lookups ([Reject Reason and Matter Referral Reporting Code Block](https://help.legalserver.org/article/1900-reject-reason-and-matter-referral-reporting-code-block), [Eligibility Screen](https://help.legalserver.org/article/1655-eligibility-screen)). List in `referred_reasons` or `referred_codes` the values that mean the caller was referred elsewhere, and in `conflict_reasons` the ones that mean a conflict of interest.

A rejected matter that was re-opened loses its rejection fields in LegalServer ([Re-open Case Block](https://help.legalserver.org/article/2445-re-open-case-block)), so the export shows its current state. Run the export after the evaluation period has settled, and say in the report when it was run.

## 3. Write the mapping

Start from [`legalserver-mapping.example.json`](legalserver-mapping.example.json). The keys:

- `site`: short code for your organisation.
- `period`: `{"pilot_start": "YYYY-MM-DD"}` (intakes from that date on are `pilot`), or a fixed `"baseline"` or `"pilot"` when you export the two periods separately.
- `consent`: LegalServer has no standard field for consent to an AI intake. If a record exists only after the caller consented, use `"given"`. If your site records it in a custom field, export the periods separately and say so in the report.
- `columns`: your report's column headers, exactly as exported.
- `channel_values`, `channel_default`: your intake type values mapped to `ai_voice`, `human_phone`, `online`, `walk_in` or `other`. Ask your AI intake vendor which field and value its integration writes, so AI-voice intakes can be told apart.
- `language_values`: your language lookup values mapped to BCP 47 tags. Common names (English, Spanish, Haitian Creole, Vietnamese, Mam, K'iche' and others) are built in. Blank becomes `und`, "Other" becomes `mis`. An unknown value stops the script and names it.
- `referred_reasons`, `referred_codes`, `conflict_reasons`, `drop_reasons`: lists of your lookup values. Matching ignores case and extra spaces.
- `counties`: `{"Davidson": {"fips": "47037", "rural": false}}` for counties you want to report on. Counties you leave out get no county or rurality.
- `disability_values`: `{"Yes": true, "No": false}` if you map a disability column.
- `note`: free text, ignored.

## 4. Draw the review sample, then convert

```
python legalserver.py export.csv --mapping mapping.json --draw-review 35 --seed 2027 > review_sheet.csv
```

This picks up to 35 cases **at random within each screening outcome** (screened in, screened out with a referral, screened out without one), the stratified sample the population estimates rely on. Use `python intake_eval.py --plan-zero 0.10` to choose the number. The draw does not depend on the export's row order, and the same seed gives the same sheet. The sheet lists Case IDs only, sorted, without the intake's decision. Where your reviewers can open the call record before they see the disposition, ask them to. Note the seed in your report.

Reviewers fill in, for each case: `should_screen_in` (yes, no, or blank if unclear), `reviewer_eligibility` (eligible, ineligible, unclear), `safety_indicator_present`, `legal_advice_given` (yes or no) and `data_errors` (a number). `random_sample` is pre-filled `yes`. Reviews prompted by a complaint or flag can be added as extra rows with `random_sample` set to `no`. They are reported, but kept out of the estimates. Rows left blank are ignored.

The review sheet holds Case IDs, so keep it where your client data lives.

```
python legalserver.py export.csv --mapping mapping.json --key-file key.txt --reviews review_sheet.csv > records.jsonl
python intake_eval.py records.jsonl --by period,channel,language,rural > report.json
```

`key.txt` is a secret your program creates once and keeps, for example with `python -c "import secrets; print(secrets.token_hex(32))" > key.txt`. Record ids are an HMAC-SHA256 of site and Case ID under that key. The same case keeps the same id across exports, and an evaluator without the key cannot link a record back to a case by hashing Case IDs. Keep the key away from the records file. Losing it only means new exports get new ids.

## What a LegalServer export cannot give

These schema fields stay empty unless you add them from elsewhere, and their indicators then show `null`:

- **AI conduct**: disclosures delivered, human transfer on request, the agent's safety flag, the step where a caller hung up, call duration. These live in the voice agent's own logs or dashboard, or come from the review.
- **Calls that never became a record.** A caller who hangs up before the agent writes to LegalServer is not in the export. Compare the vendor's call count with the export's count and report the gap.
- **Callbacks and surveys**, unless your site records them in fields you can map later.
- **After-hours share.** Intake Date has no time of day. The script drops any time it finds.

## Limits

- Built from public documentation, not yet from a real export. Expect to adjust the mapping on first use. Please open an issue if a default is wrong for your site.
- The default reading of Pending as screened in may not fit your workflow. Override it if Pending means "still being screened" at your site.
- A matter's disposition can change after the export (a Pending case accepted later, a rejected matter re-opened). Fix the export date and keep it.
- This is an evaluation aid. It does not decide anyone's eligibility and is not legal advice.

Code Apache-2.0; this page CC BY 4.0. By MV2 of Munim, Inc., made with AI agents working in his name.
