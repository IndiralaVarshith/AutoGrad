# How well AutoGrade marks, compared with real teachers

Real students' handwritten answers, already marked by real teachers or examiners, were given to AutoGrade. AutoGrade never saw the teachers' marks. Then each of AutoGrade's marks was compared with the teacher's mark.

## How to read the tables

| Column | What it means | Better is |
|---|---|---|
| Answers | How many answers were compared | |
| Same mark as the teacher | How often AutoGrade gave exactly the teacher's mark | Higher |
| At most ½ mark off / At most 1 mark off | How often AutoGrade was right or only a little off | Higher |
| Average difference (marks) | How far AutoGrade's mark was from the teacher's, on average | Lower |
| Agreement score (0 to 1) | Does AutoGrade tell good answers from bad ones? 0 = no better than guessing, about 0.5 = moderate, about 0.75 = as good as a second teacher, 1 = perfect | Higher |

Two rows in some tables are there only for comparison:

- **Lazy marker:** never reads the answer and gives everyone the most common mark. It can look good on "Same mark as the teacher" when most students got the same mark, but its agreement score is 0. A real marking system has to beat it.
- **A second human teacher:** how often two real teachers agree with each other. This is about the best anyone can expect, because teachers do not always agree either.

## Summary

| Test | What students wrote | AutoGrade: same mark as the teacher | Lazy marker | A second human teacher |
|---|---|---|---|---|
| 1. English exam (Mendeley) | Written answers, 0–2 marks | 51.0% | 55.1% | not available |
| 2. Russian maths exam (CHECK-MAT) | Full maths solutions, 0–4 marks | 44.3% | 46.7% | not available |
| 3. Reading answers (handwritten ASAP), Strict style | Short answers, 0–2 marks | 59.9% | 55.9% | 80.2% |

- **Multiple-choice answers:** AutoGrade read the letter the student chose correctly 97.7% of the time (789 answers).
- **Whole-sheet totals:** on average 3.9 marks away from the teacher's total out of 50, and they rise and fall with the teacher's totals (score 0.86, where 1 = perfectly together).
- **Marking style matters a lot:** on the reading answers, Strict style matched the teacher 59.9% of the time, against 38.7% for the default Generous style.
- **Weak spot:** AutoGrade's double-check requests catch only a small share of its wrong marks.

## 1. English exam (Mendeley dataset)

A Data Science exam: 20 multiple-choice questions and 15 written answers worth 2 marks each, on 40 student sheets that were never used while building AutoGrade. Half marks are allowed.

| Who marked | Answers | Same mark as the teacher | At most ½ mark off | Average difference (marks) | Agreement score (0 to 1) |
|---|---|---|---|---|---|
| AutoGrade, as used in the app | 465 | 51.0% | 64.5% | 0.46 | 0.60 |
| AutoGrade after learning from one teacher-marked sheet | 465 | 52.0% | 64.3% | 0.48 | 0.56 |
| AutoGrade without the length rule | 465 | 56.1% | 62.6% | 0.45 | 0.60 |
| The AI's own mark, without AutoGrade's checks | 465 | 56.1% | 62.6% | 0.45 | 0.60 |
| Lazy marker (everyone gets the most common mark) | 465 | 55.1% | 56.8% | 0.56 | 0.00 |

**Multiple-choice:** 97.7% of the letters students chose were read correctly (789 answers). The marks then come straight from the answer key.

**Whole-sheet totals:** on average 3.9 marks away from the teacher's total out of 50; they move together with the teacher's totals at 0.86 (1 = perfectly).

**Double-check requests:** AutoGrade asked the teacher to double-check 2.6% of the answers. Of the answers it marked wrongly (more than ½ mark off), it asked about 2.4%.

**In short:** multiple-choice and totals are reliable. For single written answers AutoGrade gets the exact mark a little less often than the lazy marker, but it is closer on average and does tell good answers from bad ones (agreement score about 0.6, against 0 for the lazy marker). Without the length rule it matches the teacher a little more often. Learning from one marked sheet did not help.

## 2. Russian maths exam (CHECK-MAT dataset)

122 handwritten solutions to the Russian state exam (tasks 13–19), marked by official examiners from 0 to 4 using the official marking rules. Only whole marks.

| Who marked | Answers | Same mark as the teacher | At most 1 mark off | Average difference (marks) | Agreement score (0 to 1) |
|---|---|---|---|---|---|
| AutoGrade, as used in the app | 122 | 44.3% | 74.6% | 0.85 | 0.43 |
| The AI's own mark, without AutoGrade's checks | 122 | 44.3% | 74.6% | 0.85 | 0.43 |
| Lazy marker (most common mark for each task) | 122 | 46.7% | 77.0% | 0.83 | 0.32 |
| OpenAI o4-mini (score published by the dataset's authors) | 122 | 55.7% | 82.8% | 0.66 | 0.54 |

By task:

| Task | Topic | Solutions | Same mark as the examiner | Agreement score |
|---|---|---|---|---|
| 13 | Equations (trig / exponential) | 21 | 42.9% | -0.09 |
| 14 | 3D geometry | 18 | 27.8% | 0.09 |
| 15 | Inequality | 19 | 52.6% | 0.22 |
| 16 | Money word problem | 17 | 58.8% | 0.51 |
| 17 | 2D geometry | 15 | 53.3% | 0.49 |
| 18 | Equation with a parameter | 16 | 31.2% | 0.41 |
| 19 | Number theory | 16 | 43.8% | 0.76 |

**Speed:** about 6 seconds per solution.

**Double-check requests:** AutoGrade asked the teacher to double-check 45.1% of the answers. Of the answers it marked wrongly (any mark different from the examiner's), it asked about 51.5%.

**In short:** long maths proofs are the hardest case. AutoGrade's marks follow the solution quality better than the lazy marker (agreement 0.43 against 0.32), but it gets the exact mark right less often than a stronger published model. Its double-check requests are useful here: they catch about half of the wrong marks.

## 3. Reading answers (handwritten ASAP dataset)

349 handwritten answers to two reading questions about one newspaper article, each marked 0, 1 or 2 by trained markers. Only whole marks.

- Question 3: how are pandas and koalas alike, and how are they different from pythons?
- Question 4: why does the word "invasive" matter to the article?

| Who marked | Answers | Same mark as the teacher | At most 1 mark off | Average difference (marks) | Agreement score (0 to 1) |
|---|---|---|---|---|---|
| AutoGrade, Generous style (the app's default) | 349 | 38.7% | 94.3% | 0.67 | 0.38 |
| AutoGrade, Strict style | 349 | 59.9% | 99.1% | 0.41 | 0.54 |
| AutoGrade, Strict style, without the length rule | 349 | 44.1% | 96.8% | 0.59 | 0.45 |
| The AI's own mark (Strict), without AutoGrade's checks | 349 | 44.1% | 96.8% | 0.59 | 0.45 |
| Lazy marker (most common mark for each question) | 349 | 55.9% | 100.0% | 0.44 | 0.00 |
| A second human teacher (2,415 answers marked twice) | 2415 | 80.2% | 99.9% | 0.20 | 0.75 |

By question (same mark as the teacher):

| Question | Answers | Generous | Strict | Two human teachers |
|---|---|---|---|---|
| 3 | 185 | 45.4% | 62.7% | 79.0% |
| 4 | 164 | 31.1% | 56.7% | 81.6% |

Where AutoGrade's marks went, Generous style (rows: the teacher's mark; columns: AutoGrade's mark):

| Teacher gave | AutoGrade gave 0 | AutoGrade gave 1 | AutoGrade gave 2 |
|---|---|---|---|
| 0 | 26 | 53 | 20 |
| 1 | 0 | 56 | 139 |
| 2 | 0 | 2 | 53 |

Where AutoGrade's marks went, Strict style (rows: the teacher's mark; columns: AutoGrade's mark):

| Teacher gave | AutoGrade gave 0 | AutoGrade gave 1 | AutoGrade gave 2 |
|---|---|---|---|
| 0 | 32 | 64 | 3 |
| 1 | 0 | 128 | 67 |
| 2 | 0 | 6 | 49 |

**Double-check requests:** AutoGrade asked the teacher to double-check 2.0% of the answers. Of the answers it marked wrongly (any mark different from the teacher's), it asked about 2.9%. (Requests caused only by the page having no question number written on it are not counted.)

**In short:** the default Generous style gives too many marks (it often gave 2 where the teacher gave 1). Strict style fixes much of this and beats the lazy marker, but two human teachers still agree with each other more often than AutoGrade agrees with them. Here the length rule helps.

## Technical names (for the paper)

| Plain name here | Name in research papers |
|---|---|
| Same mark as the teacher | Exact agreement (accuracy) |
| At most ½ / 1 mark off | Adjacent agreement (within one step) |
| Average difference (marks) | Mean absolute error (MAE) |
| Agreement score (0 to 1) | Quadratic weighted kappa (QWK) |
| Totals move together with the teacher's | Pearson correlation of sheet totals |
| Double-check requests that catch wrong marks | Flag recall (and precision) |
| AutoGrade without the length rule / AI's own mark without checks | Ablations |
| Lazy marker | Majority-class baseline |
| A second human teacher | Inter-rater agreement (human ceiling) |

Exact numbers for every result are in `results.json`.
