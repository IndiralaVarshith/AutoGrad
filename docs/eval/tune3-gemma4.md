# Grading evaluation: tune3-gemma4

- **Split:** tune (10 of 10 sheets graded)
- **Sheet reader:** gemma4:e4b
- **Grader:** gemma4:e4b, second marker is the other local model
- **Commit:** d9ae1b1
- **Criteria:** drafted from answerkey.txt and auto-approved by the script, with no teacher review
- **Answer key:** answerkey.txt as written (Q1 = B)
- **Failed sheets:** none
- **Run time:** 32.7 minutes in this session

## Local models only (main result)

| Measure | Result |
|---|---|
| MCQ exact match, of all usable answers | 78.8% (156/198) |
| MCQ exact match, of unflagged answers | 98.1% |
| MCQ flagged | 19.7% (39) |
| MCQ silent wrong | 3 |
| Short answers within 0.5, of unflagged answers | 63.8% (37/58) |
| Short answers within 0.5, of all with a teacher mark | 37.4% (37/99) |
| Baseline: same mark for every answer, never reading it | always 1.5: 91.9%; always 2: 65.7% |
| Short answers flagged | 41.4% (41/99) |
| Flagged drafts within 0.5 (not counted as marks) | 56.2% (18/32) |
| Short-answer silent wrong | 21 |
| All answers flagged | 36.9% |

## Teacher data caveats

- MCQ cells without a usable letter were left out: 2.
- Short answers with no valid teacher mark (blank, NC, NM, 4.0) were left out: 51. Of these the app found no answer on 44, flagged a written answer on 3, and marked 4 without flagging.
- App MCQ totals vs the teacher's recorded totals, on sheets with no pending MCQ. The totals are unreliable (see the key mismatch), so this is context only: key as written {'sheets': 7, 'exact': 2, 'within_1': 7}; with Q1 as D, a side note, {'sheets': 7, 'exact': 7, 'within_1': 7}.

## Why answers were flagged

| Reason | Count |
|---|---|
| NO_ANSWER_FOUND | 87 |
| CRITIC_DISAGREED | 23 |
| ZERO_ON_WRITTEN_ANSWER | 6 |
| UNMATCHED | 5 |
| BLANK_ANSWER | 3 |
| UNCLEAR_READING | 3 |
| EVIDENCE_NOT_VERIFIED | 1 |
| UNCLEAR_OPTION | 1 |

## Silent wrong marks

| Student | Question | App | Teacher |
|---|---|---|---|
| Student_4 | Q24 | 1.0 | 2.0 |
| Student_4 | Q27 | 0.5 | 2.0 |
| Student_4 | Q29 | 1.0 | 2.0 |
| Student_4 | Q32 | 2.0 | 0.0 |
| Student_6 | Q28 | 2.0 | 1.0 |
| Student_6 | Q31 | 2.0 | 1.0 |
| Student_7 | Q28 | 1.0 | 2.0 |
| Student_11 | Q27 | 0.5 | 1.5 |
| Student_11 | Q32 | 1.0 | 2.0 |
| Student_11 | Q35 | 0.5 | 1.5 |
| Student_19 | Q25 | 0.5 | 2.0 |
| Student_21 | Q24 | 1.0 | 2.0 |
| Student_21 | Q25 | 1.0 | 2.0 |
| Student_21 | Q27 | 0.5 | 2.0 |
| Student_21 | Q34 | 1.0 | 2.0 |
| Student_26 | Q13 | D | B |
| Student_26 | Q25 | 0.5 | 2.0 |
| Student_36 | Q25 | 1.0 | 2.0 |
| Student_36 | Q26 | 1.0 | 2.0 |
| Student_43 | Q27 | 0.5 | 2.0 |
| Student_43 | Q28 | 0.5 | 2.0 |
| Student_49 | Q1 | B | A |
| Student_49 | Q19 | B | D |
| Student_49 | Q25 | 1.0 | 2.0 |
