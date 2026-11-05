# Grading evaluation: final

- **Split:** report (40 of 40 sheets graded)
- **Sheet reader:** qwen3.5:4b
- **Grader:** gemma4:e4b, second marker is the other local model
- **Commit:** b3025af
- **Criteria:** drafted from answerkey.txt and auto-approved by the script, with no teacher review
- **Answer key:** answerkey.txt as written (Q1 = B)
- **Failed sheets:** none
- **Run time:** 165.8 minutes in this session

## Local models only (main result)

| Measure | Result |
|---|---|
| MCQ exact match, of all usable answers | 80.0% (631/789) |
| MCQ exact match, of unflagged answers | 98.0% |
| MCQ flagged | 18.4% (145) |
| MCQ silent wrong | 13 |
| Short answers within 0.5, of unflagged answers | 88.7% (55/62) |
| Short answers within 0.5, of all with a teacher mark | 11.8% (55/465) |
| Baseline: same mark for every answer, never reading it | always 1.5: 87.1%; always 2: 56.8% |
| Short answers flagged | 86.7% (403/465) |
| Flagged drafts within 0.5 (not counted as marks) | 58.8% (193/328) |
| Short-answer silent wrong | 7 |
| All answers flagged | 48.9% |

## Teacher data caveats

- MCQ cells without a usable letter were left out: 11.
- Short answers with no valid teacher mark (blank, NC, NM, 4.0) were left out: 135. Of these the app found no answer on 107, flagged a written answer on 25, and marked 3 without flagging.
- App MCQ totals vs the teacher's recorded totals, on sheets with no pending MCQ. The totals are unreliable (see the key mismatch), so this is context only: key as written {'sheets': 21, 'exact': 7, 'within_1': 19}; with Q1 as D, a side note, {'sheets': 21, 'exact': 13, 'within_1': 21}.

## Why answers were flagged

| Reason | Count |
|---|---|
| NO_ANSWER_FOUND | 202 |
| PARTIAL_MARK | 152 |
| CRITIC_DISAGREED | 101 |
| UNCLEAR_OPTION | 95 |
| ZERO_ON_WRITTEN_ANSWER | 68 |
| UNMATCHED | 33 |
| UNCLEAR_READING | 28 |
| BLANK_ANSWER | 3 |
| EVIDENCE_NOT_VERIFIED | 2 |

## Silent wrong marks

| Student | Question | App | Teacher |
|---|---|---|---|
| Student_2 | Q1 | B | A |
| Student_9 | Q28 | 2.0 | 1.0 |
| Student_15 | Q4 | B | A |
| Student_16 | Q6 | D | B |
| Student_18 | Q1 | B | D |
| Student_18 | Q2 | A | D |
| Student_18 | Q4 | A | D |
| Student_22 | Q6 | D | B |
| Student_23 | Q20 | B | D |
| Student_23 | Q21 | 2.0 | 1.0 |
| Student_24 | Q17 | A | B |
| Student_32 | Q26 | 2.0 | 1.0 |
| Student_34 | Q17 | A | B |
| Student_34 | Q18 | C | B |
| Student_38 | Q20 | B | D |
| Student_38 | Q22 | 2.0 | 1.0 |
| Student_40 | Q31 | 2.0 | 1.0 |
| Student_41 | Q31 | 2.0 | 1.0 |
| Student_45 | Q1 | B | D |
| Student_46 | Q30 | 2.0 | 1.0 |
