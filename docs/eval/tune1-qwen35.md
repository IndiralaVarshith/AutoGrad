# Grading evaluation: tune1-qwen35

- **Split:** tune (10 of 10 sheets graded)
- **Sheet reader:** qwen3.5:4b
- **Commit:** 0bca73e
- **Criteria:** drafted from answerkey.txt and auto-approved by the script, with no teacher review
- **Answer key:** answerkey.txt as written (Q1 = B)
- **Failed sheets:** none
- **Run time:** 32.8 minutes in this session

## Local models only (main result)

| Measure | Result |
|---|---|
| MCQ exact match, of all usable answers | 86.9% (172/198) |
| MCQ exact match, of unflagged answers | 98.3% |
| MCQ flagged | 11.6% (23) |
| MCQ silent wrong | 3 |
| Short answers within 0.5, of unflagged answers | 32.8% (22/67) |
| Short answers within 0.5, of all with a teacher mark | 22.2% (22/99) |
| Short answers flagged | 32.3% (32/99) |
| Flagged drafts within 0.5 (not counted as marks) | 41.7% (5/12) |
| Short-answer silent wrong | 45 |
| All answers flagged | 29.7% |

## Teacher data caveats

- MCQ cells without a usable letter were left out: 2.
- Short answers with no valid teacher mark (blank, NC, NM, 4.0) were left out: 51. Of these the app found no answer on 44, flagged a written answer on 3, and marked 4 without flagging.
- App MCQ totals vs the teacher's recorded totals, on sheets with no pending MCQ. The totals are unreliable (see the key mismatch), so this is context only: key as written {'sheets': 6, 'exact': 2, 'within_1': 5}; with Q1 as D, a side note, {'sheets': 6, 'exact': 5, 'within_1': 6}.

## Why answers were flagged

| Reason | Count |
|---|---|
| NO_ANSWER_FOUND | 73 |
| CRITIC_DISAGREED | 13 |
| UNMATCHED | 10 |
| UNCLEAR_OPTION | 5 |
| BLANK_ANSWER | 3 |

## Silent wrong marks

| Student | Question | App | Teacher |
|---|---|---|---|
| Student_4 | Q23 | 0.0 | 1.0 |
| Student_4 | Q24 | 0.0 | 2.0 |
| Student_4 | Q27 | 1.0 | 2.0 |
| Student_4 | Q28 | 1.0 | 2.0 |
| Student_4 | Q29 | 0.0 | 2.0 |
| Student_6 | Q21 | 0.0 | 2.0 |
| Student_6 | Q24 | 0.0 | 2.0 |
| Student_6 | Q27 | 0.0 | 1.0 |
| Student_6 | Q31 | 2.0 | 1.0 |
| Student_7 | Q21 | 0.0 | 2.0 |
| Student_7 | Q22 | 0.0 | 1.0 |
| Student_7 | Q24 | 0.0 | 1.0 |
| Student_7 | Q25 | 0.0 | 2.0 |
| Student_7 | Q28 | 0.0 | 2.0 |
| Student_7 | Q29 | 0.0 | 2.0 |
| Student_7 | Q31 | 0.0 | 1.0 |
| Student_11 | Q22 | 0.0 | 1.0 |
| Student_11 | Q27 | 0.0 | 1.5 |
| Student_11 | Q29 | 0.0 | 2.0 |
| Student_11 | Q30 | 0.0 | 2.0 |
| Student_11 | Q33 | 0.0 | 1.5 |
| Student_11 | Q35 | 0.0 | 1.5 |
| Student_19 | Q29 | 0.0 | 1.0 |
| Student_21 | Q22 | 0.0 | 1.0 |
| Student_21 | Q23 | 0.0 | 1.0 |
| Student_21 | Q24 | 0.0 | 2.0 |
| Student_21 | Q27 | 0.0 | 2.0 |
| Student_21 | Q29 | 0.0 | 2.0 |
| Student_21 | Q34 | 1.0 | 2.0 |
| Student_26 | Q13 | D | B |
| Student_26 | Q25 | 0.0 | 2.0 |
| Student_26 | Q26 | 0.0 | 1.0 |
| Student_26 | Q31 | 0.0 | 1.0 |
| Student_36 | Q19 | B | A |
| Student_36 | Q21 | 0.0 | 2.0 |
| Student_36 | Q24 | 0.0 | 2.0 |
| Student_36 | Q26 | 0.0 | 2.0 |
| Student_36 | Q31 | 0.0 | 2.0 |
| Student_43 | Q22 | 0.0 | 2.0 |
| Student_43 | Q27 | 1.0 | 2.0 |
| Student_43 | Q29 | 0.0 | 1.0 |
| Student_43 | Q31 | 0.0 | 2.0 |
| Student_49 | Q19 | B | D |
| Student_49 | Q22 | 0.0 | 2.0 |
| Student_49 | Q24 | 0.0 | 1.0 |
| Student_49 | Q27 | 0.0 | 1.0 |
| Student_49 | Q29 | 0.0 | 1.0 |
| Student_49 | Q31 | 0.0 | 1.0 |
