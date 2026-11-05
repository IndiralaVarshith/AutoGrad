# Grading evaluation: gemini-tune1

- **Split:** tune (10 of 10 sheets graded)
- **Model:** gemini-3.1-flash-lite
- **Commit:** 5fd1330
- **Criteria:** drafted by Gemini from answerkey.txt and auto-approved by the script, with no teacher review
- **Answer key:** answerkey.txt as written (Q1 = B)
- **Failed sheets:** none
- **Stopped early:** no
- **Run time:** 1.7 minutes in this session
- **Calls per sheet:** 1.0
- **Tokens per sheet:** 6034 in, 3087 out
- **Seconds per sheet:** 10.0
- **Cost per sheet:** Rs 0 actual on the free tier. Notional paid-tier cost Rs 0.58, at {'input': 0.25, 'output': 1.5} USD per 1M tokens (Gemini API pricing page, read 2026-11-06) and Rs 94.48 per USD (frankfurter.dev, rate dated 2026-11-05)

## Gemini

| Measure | Result |
|---|---|
| MCQ exact match, of all usable answers | 98.5% (195/198) |
| MCQ exact match, of unflagged answers | 99.0% |
| MCQ flagged | 0.5% (1) |
| MCQ silent wrong | 2 |
| Short answers: exact mark, of all with a teacher mark | 46.5% (46/99) |
| Short answers: exact mark, of unflagged | 51.7% (46/89) |
| Short answers: mean absolute error, unflagged | 0.511 |
| Baseline, always 2.0: exact on the same unflagged answers | 59.6% |
| Baseline, always 2.0: mean absolute error on the same unflagged answers | 0.461 |
| Baseline, always 2.0: exact, of all with a teacher mark | 61.6% |
| Baseline, always 2.0: mean absolute error, all | 0.444 |
| Short answers: within 0.5, of unflagged | 60.7% (54/89) |
| Short answers flagged | 10.1% (10/99) |
| Flagged drafts: mean absolute error (not counted as marks) | 0.25 |
| Short-answer silent wrong: any difference | 43 |
| Short-answer silent wrong: more than 0.5 off | 35 |
| All answers flagged | 16.6% |
| All answers flagged, leaving out blanks | 2.9% |

## Teacher data caveats

- MCQ cells without a usable letter were left out: 2.
- Short answers with no valid teacher mark (blank, NC, NM, 4.0) were left out: 51. Of these the app found no answer on 44, flagged a written answer on 1, and marked 6 without flagging.
- App MCQ totals vs the teacher's recorded totals, on sheets with no pending MCQ. The totals are unreliable (see the key mismatch), so this is context only: key as written {'sheets': 8, 'exact': 2, 'within_1': 8}; with Q1 as D, a side note, {'sheets': 8, 'exact': 8, 'within_1': 8}.

## Why answers were flagged

| Reason | Count |
|---|---|
| MISSING | 48 |
| EVIDENCE_NOT_FOUND | 7 |
| LOW_CONFIDENCE | 2 |
| UNCLEAR_OPTION | 1 |

## Silent wrong marks

| Student | Question | App | Teacher |
|---|---|---|---|
| Student_4 | Q23 | 0.0 | 1.0 |
| Student_4 | Q24 | 1.0 | 2.0 |
| Student_4 | Q27 | 1.0 | 2.0 |
| Student_4 | Q29 | 0.0 | 2.0 |
| Student_4 | Q32 | 1.0 | 0.0 |
| Student_4 | Q34 | 1.5 | 2.0 |
| Student_6 | Q24 | 1.0 | 2.0 |
| Student_6 | Q27 | 0.5 | 1.0 |
| Student_6 | Q28 | 2.0 | 1.0 |
| Student_6 | Q31 | 2.0 | 1.0 |
| Student_6 | Q34 | 1.0 | 2.0 |
| Student_7 | Q21 | 1.0 | 2.0 |
| Student_7 | Q24 | 0.5 | 1.0 |
| Student_7 | Q27 | 2.0 | 1.0 |
| Student_7 | Q28 | 1.0 | 2.0 |
| Student_7 | Q29 | 1.5 | 2.0 |
| Student_7 | Q31 | 2.0 | 1.0 |
| Student_7 | Q32 | 0.0 | 1.0 |
| Student_11 | Q23 | 2.0 | 1.0 |
| Student_11 | Q27 | 1.0 | 1.5 |
| Student_11 | Q29 | 0.0 | 2.0 |
| Student_11 | Q30 | 0.0 | 2.0 |
| Student_11 | Q33 | 0.0 | 1.5 |
| Student_11 | Q35 | 0.0 | 1.5 |
| Student_19 | Q29 | 0.0 | 1.0 |
| Student_21 | Q22 | 0.0 | 1.0 |
| Student_21 | Q23 | 0.0 | 1.0 |
| Student_21 | Q24 | 1.0 | 2.0 |
| Student_21 | Q27 | 0.5 | 2.0 |
| Student_21 | Q29 | 1.0 | 2.0 |
| Student_21 | Q34 | 1.0 | 2.0 |
| Student_26 | Q13 | D | B |
| Student_26 | Q21 | 1.0 | 2.0 |
| Student_26 | Q24 | 0.5 | 1.0 |
| Student_26 | Q31 | 0.0 | 1.0 |
| Student_36 | Q21 | 1.0 | 2.0 |
| Student_36 | Q23 | 0.5 | 1.0 |
| Student_36 | Q25 | 1.0 | 2.0 |
| Student_43 | Q22 | 0.0 | 2.0 |
| Student_43 | Q23 | 1.0 | 2.0 |
| Student_43 | Q27 | 1.0 | 2.0 |
| Student_49 | Q19 | B | D |
| Student_49 | Q22 | 0.0 | 2.0 |
| Student_49 | Q29 | 1.5 | 1.0 |
| Student_49 | Q31 | 0.0 | 1.0 |
