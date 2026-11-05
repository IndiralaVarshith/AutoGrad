# Grading evaluation: gemini-tune2

- **Split:** tune (10 of 10 sheets graded)
- **Model:** gemini-3.1-flash-lite
- **Commit:** 443bda0
- **Criteria:** drafted by Gemini from answerkey.txt and auto-approved by the script, with no teacher review
- **Answer key:** answerkey.txt as written (Q1 = B)
- **Failed sheets:** none
- **Stopped early:** no
- **Run time:** 1.1 minutes in this session
- **Calls per sheet:** 1.0
- **Tokens per sheet:** 6151 in, 3083 out
- **Seconds per sheet:** 6.8
- **Cost per sheet:** Rs 0 actual on the free tier. Notional paid-tier cost Rs 0.582, at {'input': 0.25, 'output': 1.5} USD per 1M tokens (Gemini API pricing page, read 2026-11-06) and Rs 94.48 per USD (frankfurter.dev, rate dated 2026-11-05)

## Gemini

| Measure | Result |
|---|---|
| MCQ exact match, of all usable answers | 97.5% (193/198) |
| MCQ exact match, of unflagged answers | 98.0% |
| MCQ flagged | 0.5% (1) |
| MCQ silent wrong | 4 |
| Short answers: exact mark, of all with a teacher mark | 35.4% (35/99) |
| Short answers: exact mark, of unflagged | 85.4% (35/41) |
| Short answers: mean absolute error, unflagged | 0.146 |
| Baseline, always 2.0: exact on the same unflagged answers | 85.4% |
| Baseline, always 2.0: mean absolute error on the same unflagged answers | 0.146 |
| Baseline, always 2.0: exact, of all with a teacher mark | 61.6% |
| Baseline, always 2.0: mean absolute error, all | 0.444 |
| Short answers: within 0.5, of unflagged | 85.4% (35/41) |
| Short answers flagged | 58.6% (58/99) |
| Flagged drafts: mean absolute error (not counted as marks) | 0.702 |
| Short-answer silent wrong: any difference | 6 |
| Short-answer silent wrong: more than 0.5 off | 6 |
| All answers flagged | 31.7% |
| All answers flagged, leaving out blanks | 18.3% |

## Teacher data caveats

- MCQ cells without a usable letter were left out: 2.
- Short answers with no valid teacher mark (blank, NC, NM, 4.0) were left out: 51. Of these the app found no answer on 44, flagged a written answer on 6, and marked 1 without flagging.
- App MCQ totals vs the teacher's recorded totals, on sheets with no pending MCQ. The totals are unreliable (see the key mismatch), so this is context only: key as written {'sheets': 8, 'exact': 2, 'within_1': 8}; with Q1 as D, a side note, {'sheets': 8, 'exact': 8, 'within_1': 8}.

## Why answers were flagged

| Reason | Count |
|---|---|
| MISSING | 47 |
| PARTIAL_HIGH_VALUE | 36 |
| ZERO_ON_WRITTEN | 22 |
| EVIDENCE_NOT_FOUND | 6 |
| LOW_CONFIDENCE | 1 |
| UNCLEAR_OPTION | 1 |

## Silent wrong marks

| Student | Question | App | Teacher |
|---|---|---|---|
| Student_6 | Q28 | 2.0 | 1.0 |
| Student_6 | Q31 | 2.0 | 1.0 |
| Student_7 | Q27 | 2.0 | 1.0 |
| Student_7 | Q31 | 2.0 | 1.0 |
| Student_11 | Q23 | 2.0 | 1.0 |
| Student_19 | Q1 | B | D |
| Student_19 | Q5 | B | A |
| Student_26 | Q13 | D | B |
| Student_49 | Q19 | B | D |
| Student_49 | Q29 | 2.0 | 1.0 |
