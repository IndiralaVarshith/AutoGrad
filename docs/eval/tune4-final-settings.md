# Grading evaluation: tune4-final-settings

- **Split:** tune (10 of 10 sheets graded)
- **Sheet reader:** qwen3.5:4b
- **Grader:** gemma4:e4b, second marker is the other local model
- **Commit:** 0c4456c
- **Criteria:** drafted from answerkey.txt and auto-approved by the script, with no teacher review
- **Answer key:** answerkey.txt as written (Q1 = B)
- **Failed sheets:** none
- **Run time:** 36.3 minutes in this session
- **Gemini calls used:** the counter shows 20 attempts, but none returned a usable result. About two minutes into the first sheet Gemini gave a quota-type response, which set a stored block (21:15:53 IST), and later attempts stopped locally. At least one request reached Google; the saved data does not show whether it carried an answer crop. The "With Gemini" section below only repeats the local numbers. Note added by hand after the run.

## Local models only (main result)

| Measure | Result |
|---|---|
| MCQ exact match, of all usable answers | 86.9% (172/198) |
| MCQ exact match, of unflagged answers | 98.3% |
| MCQ flagged | 11.6% (23) |
| MCQ silent wrong | 3 |
| Short answers within 0.5, of unflagged answers | 92.9% (13/14) |
| Short answers within 0.5, of all with a teacher mark | 13.1% (13/99) |
| Baseline: same mark for every answer, never reading it | always 1.5: 91.9%; always 2: 65.7% |
| Short answers flagged | 85.9% (85/99) |
| Flagged drafts within 0.5 (not counted as marks) | 56.1% (37/66) |
| Short-answer silent wrong | 1 |
| All answers flagged | 45.7% |

## With Gemini on flagged answers

| Measure | Result |
|---|---|
| MCQ exact match, of all usable answers | 86.9% (172/198) |
| MCQ exact match, of unflagged answers | 98.3% |
| MCQ flagged | 11.6% (23) |
| MCQ silent wrong | 3 |
| Short answers within 0.5, of unflagged answers | 92.9% (13/14) |
| Short answers within 0.5, of all with a teacher mark | 13.1% (13/99) |
| Baseline: same mark for every answer, never reading it | always 1.5: 91.9%; always 2: 65.7% |
| Short answers flagged | 85.9% (85/99) |
| Flagged drafts within 0.5 (not counted as marks) | 56.1% (37/66) |
| Short-answer silent wrong | 1 |
| All answers flagged | 45.7% |

## Teacher data caveats

- MCQ cells without a usable letter were left out: 2.
- Short answers with no valid teacher mark (blank, NC, NM, 4.0) were left out: 51. Of these the app found no answer on 44, flagged a written answer on 6, and marked 1 without flagging.
- App MCQ totals vs the teacher's recorded totals, on sheets with no pending MCQ. The totals are unreliable (see the key mismatch), so this is context only: key as written {'sheets': 6, 'exact': 2, 'within_1': 5}; with Q1 as D, a side note, {'sheets': 6, 'exact': 5, 'within_1': 6}.

## Why answers were flagged

| Reason | Count |
|---|---|
| NO_ANSWER_FOUND | 72 |
| PARTIAL_MARK | 42 |
| CRITIC_DISAGREED | 20 |
| UNMATCHED | 10 |
| ZERO_ON_WRITTEN_ANSWER | 7 |
| UNCLEAR_OPTION | 5 |
| BLANK_ANSWER | 3 |
| EVIDENCE_NOT_VERIFIED | 1 |

## Silent wrong marks

| Student | Question | App | Teacher |
|---|---|---|---|
| Student_6 | Q31 | 2.0 | 1.0 |
| Student_26 | Q13 | D | B |
| Student_36 | Q19 | B | A |
| Student_49 | Q19 | B | D |
