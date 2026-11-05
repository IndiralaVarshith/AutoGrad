# Grading evaluation: gemini-final

- **Split:** report (40 of 40 sheets graded)
- **Model:** gemini-3.1-flash-lite
- **Commit:** 3d692e3
- **Criteria:** drafted by Gemini from answerkey.txt and auto-approved by the script, with no teacher review
- **Answer key:** answerkey.txt as written (Q1 = B)
- **Failed sheets:** none
- **Stopped early:** no
- **Run time:** 6.1 minutes in this session
- **Calls per sheet:** 1.0
- **Tokens per sheet:** 6151 in, 3332 out
- **Seconds per sheet:** 9.2
- **Cost per sheet:** Rs 0 actual on the free tier. Notional paid-tier cost Rs 0.618, at {'input': 0.25, 'output': 1.5} USD per 1M tokens (Gemini API pricing page, read 2026-11-06) and Rs 94.48 per USD (frankfurter.dev, rate dated 2026-11-05)
- **Earlier run:** final, 40 sheets, recalculated from saved results with no model calls

## How to read these numbers

Added by hand after the run, from the same saved results and with no model calls.

- **Only full marks stand without a teacher, so every unflagged short answer scored 2.** "Unflagged" against "always 2.0" is therefore the same number by construction. It says nothing about marking skill.
- **The fair test is every mark proposed, flagged or not.** Gemini: 56.1% exact and mean absolute error 0.449, on 462 answers. Always 2.0: 55.1% exact and error 0.563, on 465 answers. So Gemini barely beats the baseline on exact marks and is moderately closer on error.
- **Earlier local models on the same test:** 44.6% exact and error 0.542, on 390 answers. They proposed no mark on 75 answers.
- **Silent wrong marks went up** against the local models (short 28 vs 9, MCQ 20 vs 13), because far more answers now go through unflagged (short 175 vs 62, MCQ 762 vs 644).
- **Teacher data:** one teacher marked these, and the MCQ key mismatch on Q1 is unresolved. Some "wrong" MCQ reads may be errors in the recorded letters; this was not checked.
- **The report sheets were used once on Gemini**, but the local models had already run on them.

## Gemini

| Measure | Result |
|---|---|
| MCQ exact match, of all usable answers | 94.0% (742/789) |
| MCQ exact match, of unflagged answers | 97.4% |
| MCQ flagged | 3.4% (27) |
| MCQ silent wrong | 20 |
| Short answers: exact mark, of all with a teacher mark | 31.6% (147/465) |
| Short answers: exact mark, of unflagged | 84.0% (147/175) |
| Short answers: mean absolute error, unflagged | 0.149 |
| Baseline, always 2.0: exact on the same unflagged answers | 84.0% |
| Baseline, always 2.0: mean absolute error on the same unflagged answers | 0.149 |
| Baseline, always 2.0: exact, of all with a teacher mark | 55.1% |
| Baseline, always 2.0: mean absolute error, all | 0.563 |
| Short answers: within 0.5, of unflagged | 86.3% (151/175) |
| Short answers flagged | 62.4% (290/465) |
| Flagged drafts: mean absolute error (not counted as marks) | 0.632 |
| Short-answer silent wrong: any difference | 28 |
| Short-answer silent wrong: more than 0.5 off | 24 |
| All answers flagged | 31.9% |
| All answers flagged, leaving out blanks | 22.7% |

## Earlier local models, on the same sheets (from saved results)

| Measure | Result |
|---|---|
| MCQ exact match, of all usable answers | 80.0% (631/789) |
| MCQ exact match, of unflagged answers | 98.0% |
| MCQ flagged | 18.4% (145) |
| MCQ silent wrong | 13 |
| Short answers: exact mark, of all with a teacher mark | 11.4% (53/465) |
| Short answers: exact mark, of unflagged | 85.5% (53/62) |
| Short answers: mean absolute error, unflagged | 0.129 |
| Baseline, always 2.0: exact on the same unflagged answers | 85.5% |
| Baseline, always 2.0: mean absolute error on the same unflagged answers | 0.129 |
| Baseline, always 2.0: exact, of all with a teacher mark | 55.1% |
| Baseline, always 2.0: mean absolute error, all | 0.563 |
| Short answers: within 0.5, of unflagged | 88.7% (55/62) |
| Short answers flagged | 86.7% (403/465) |
| Flagged drafts: mean absolute error (not counted as marks) | 0.62 |
| Short-answer silent wrong: any difference | 9 |
| Short-answer silent wrong: more than 0.5 off | 7 |
| All answers flagged | 48.9% |
| All answers flagged, leaving out blanks | 34.2% |

## Teacher data caveats

- MCQ cells without a usable letter were left out: 11.
- Short answers with no valid teacher mark (blank, NC, NM, 4.0) were left out: 135. Of these the app found no answer on 124, flagged a written answer on 4, and marked 7 without flagging.
- App MCQ totals vs the teacher's recorded totals, on sheets with no pending MCQ. The totals are unreliable (see the key mismatch), so this is context only: key as written {'sheets': 32, 'exact': 12, 'within_1': 28}; with Q1 as D, a side note, {'sheets': 32, 'exact': 19, 'within_1': 27}.

## Why answers were flagged

| Reason | Count |
|---|---|
| PARTIAL_HIGH_VALUE | 134 |
| MISSING | 129 |
| ZERO_ON_WRITTEN | 126 |
| EVIDENCE_NOT_FOUND | 39 |
| NUMBER_INFERRED | 20 |
| LOW_CONFIDENCE | 10 |
| UNCLEAR_OPTION | 6 |

## Silent wrong marks

| Student | Question | App | Teacher |
|---|---|---|---|
| Student_1 | Q31 | 2.0 | 1.0 |
| Student_2 | Q1 | B | A |
| Student_3 | Q25 | 2.0 | 1.0 |
| Student_5 | Q21 | 2.0 | 1.0 |
| Student_8 | Q28 | 2.0 | 1.0 |
| Student_9 | Q23 | 2.0 | 1.0 |
| Student_9 | Q28 | 2.0 | 1.0 |
| Student_10 | Q28 | 2.0 | 1.0 |
| Student_12 | Q28 | 2.0 | 1.5 |
| Student_12 | Q34 | 2.0 | 1.5 |
| Student_12 | Q35 | 2.0 | 1.5 |
| Student_13 | Q9 | A | D |
| Student_13 | Q23 | 2.0 | 1.0 |
| Student_15 | Q4 | B | A |
| Student_15 | Q15 | B | A |
| Student_15 | Q17 | B | A |
| Student_15 | Q19 | B | A |
| Student_18 | Q2 | C | D |
| Student_20 | Q23 | 2.0 | 1.0 |
| Student_20 | Q29 | 2.0 | 1.0 |
| Student_22 | Q6 | D | B |
| Student_23 | Q21 | 2.0 | 1.0 |
| Student_25 | Q9 | A | D |
| Student_25 | Q10 | B | D |
| Student_28 | Q29 | 2.0 | 1.0 |
| Student_28 | Q31 | 2.0 | 1.0 |
| Student_30 | Q30 | 2.0 | 1.0 |
| Student_31 | Q31 | 2.0 | 1.5 |
| Student_33 | Q5 | D | A |
| Student_33 | Q6 | A | B |
| Student_33 | Q7 | A | B |
| Student_33 | Q8 | C | D |
| Student_33 | Q9 | A | D |
| Student_33 | Q20 | C | B |
| Student_34 | Q17 | A | B |
| Student_34 | Q18 | C | B |
| Student_34 | Q24 | 2.0 | 1.0 |
| Student_35 | Q32 | 2.0 | 1.0 |
| Student_37 | Q23 | 2.0 | 1.0 |
| Student_37 | Q28 | 2.0 | 1.0 |
| Student_38 | Q20 | B | D |
| Student_38 | Q25 | 2.0 | 1.0 |
| Student_40 | Q28 | 2.0 | 1.0 |
| Student_40 | Q31 | 2.0 | 1.0 |
| Student_46 | Q29 | 2.0 | 1.0 |
| Student_46 | Q30 | 2.0 | 1.0 |
| Student_50 | Q3 | A | D |
| Student_50 | Q23 | 2.0 | 1.0 |
