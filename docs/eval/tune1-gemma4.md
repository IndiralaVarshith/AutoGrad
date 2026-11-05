# Grading evaluation: tune1-gemma4

- **Split:** tune (10 of 10 sheets graded)
- **Sheet reader:** gemma4:e4b
- **Commit:** 0bca73e
- **Criteria:** drafted from answerkey.txt and auto-approved by the script, with no teacher review
- **Answer key:** answerkey.txt as written (Q1 = B)
- **Failed sheets:** none
- **Run time:** 30.6 minutes in this session

## Local models only (main result)

| Measure | Result |
|---|---|
| MCQ exact match, of all usable answers | 78.8% (156/198) |
| MCQ exact match, of unflagged answers | 98.1% |
| MCQ flagged | 19.7% (39) |
| MCQ silent wrong | 3 |
| Short answers within 0.5, of unflagged answers | 37.5% (27/72) |
| Short answers within 0.5, of all with a teacher mark | 27.3% (27/99) |
| Short answers flagged | 27.3% (27/99) |
| Flagged drafts within 0.5 (not counted as marks) | 29.4% (5/17) |
| Short-answer silent wrong | 45 |
| All answers flagged | 33.1% |

## Teacher data caveats

- MCQ cells without a usable letter were left out: 2.
- Short answers with no valid teacher mark (blank, NC, NM, 4.0) were left out: 51. Of these the app found no answer on 44, flagged a written answer on 4, and marked 3 without flagging.
- App MCQ totals vs the teacher's recorded totals, on sheets with no pending MCQ. The totals are unreliable (see the key mismatch), so this is context only: key as written {'sheets': 7, 'exact': 2, 'within_1': 7}; with Q1 as D, a side note, {'sheets': 7, 'exact': 7, 'within_1': 7}.

## Why answers were flagged

| Reason | Count |
|---|---|
| NO_ANSWER_FOUND | 87 |
| CRITIC_DISAGREED | 16 |
| UNMATCHED | 6 |
| BLANK_ANSWER | 3 |
| UNCLEAR_READING | 3 |
| UNCLEAR_OPTION | 1 |

## Silent wrong marks

| Student | Question | App | Teacher |
|---|---|---|---|
| Student_4 | Q23 | 0.0 | 1.0 |
| Student_4 | Q24 | 0.0 | 2.0 |
| Student_4 | Q27 | 1.0 | 2.0 |
| Student_4 | Q28 | 1.0 | 2.0 |
| Student_4 | Q29 | 0.0 | 2.0 |
| Student_6 | Q21 | 0.0 | 2.0 |
| Student_6 | Q24 | 1.0 | 2.0 |
| Student_6 | Q26 | 0.0 | 1.0 |
| Student_6 | Q28 | 2.0 | 1.0 |
| Student_6 | Q31 | 2.0 | 1.0 |
| Student_7 | Q21 | 0.0 | 2.0 |
| Student_7 | Q22 | 0.0 | 1.0 |
| Student_7 | Q23 | 0.0 | 1.0 |
| Student_7 | Q24 | 0.0 | 1.0 |
| Student_7 | Q28 | 0.0 | 2.0 |
| Student_7 | Q29 | 0.0 | 2.0 |
| Student_7 | Q31 | 0.0 | 1.0 |
| Student_7 | Q32 | 0.0 | 1.0 |
| Student_11 | Q22 | 0.0 | 1.0 |
| Student_11 | Q27 | 0.0 | 1.5 |
| Student_11 | Q29 | 0.0 | 2.0 |
| Student_11 | Q30 | 0.0 | 2.0 |
| Student_11 | Q33 | 0.0 | 1.5 |
| Student_11 | Q35 | 0.0 | 1.5 |
| Student_19 | Q29 | 0.0 | 1.0 |
| Student_21 | Q22 | 0.0 | 1.0 |
| Student_21 | Q23 | 0.0 | 1.0 |
| Student_21 | Q24 | 1.0 | 2.0 |
| Student_21 | Q27 | 0.0 | 2.0 |
| Student_21 | Q29 | 0.0 | 2.0 |
| Student_21 | Q34 | 1.0 | 2.0 |
| Student_26 | Q13 | D | B |
| Student_26 | Q26 | 0.0 | 1.0 |
| Student_26 | Q31 | 0.0 | 1.0 |
| Student_36 | Q21 | 0.0 | 2.0 |
| Student_36 | Q23 | 0.0 | 1.0 |
| Student_36 | Q24 | 0.0 | 2.0 |
| Student_36 | Q25 | 1.0 | 2.0 |
| Student_43 | Q22 | 0.0 | 2.0 |
| Student_43 | Q27 | 0.0 | 2.0 |
| Student_43 | Q28 | 0.0 | 2.0 |
| Student_43 | Q29 | 0.0 | 1.0 |
| Student_49 | Q1 | B | A |
| Student_49 | Q19 | B | D |
| Student_49 | Q22 | 0.0 | 2.0 |
| Student_49 | Q27 | 0.0 | 1.0 |
| Student_49 | Q29 | 0.0 | 1.0 |
| Student_49 | Q31 | 0.0 | 1.0 |
