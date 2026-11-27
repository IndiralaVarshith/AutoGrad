# AutoGrade vs real teachers

AutoGrade marked real students' answers without seeing the teachers' marks. Then we checked how often it agreed.

- **Same mark:** how often AutoGrade gave exactly the teacher's mark.
- **Close:** right, or only a little off.
- **Agreement (0–1):** does it tell good answers from bad? 0 = guessing, 0.75 = as good as a second teacher.
- **Lazy marker:** gives everyone the most common mark without reading. AutoGrade must beat it.

## 1. English exam (40 sheets)

| Who marked | Same mark | Close (±½) | Agreement (0–1) |
|---|---|---|---|
| AutoGrade | 51.0% | 64.5% | 0.60 |
| Lazy marker | 55.1% | 56.8% | 0.00 |

- Multiple-choice letters read correctly: **97.7%**
- Sheet totals: about **3.9 marks** off out of 50

## 2. Russian maths exam (122 solutions)

| Who marked | Same mark | Close (±1) | Agreement (0–1) |
|---|---|---|---|
| AutoGrade | 44.3% | 74.6% | 0.43 |
| Lazy marker (per task) | 46.7% | 77.0% | 0.32 |
| OpenAI o4-mini (published) | 55.7% | 82.8% | 0.54 |

## 3. Reading answers (349 answers)

| Who marked | Same mark | Close (±1) | Agreement (0–1) |
|---|---|---|---|
| AutoGrade, Generous style | 38.7% | 94.3% | 0.38 |
| AutoGrade, Strict style | 59.9% | 99.1% | 0.54 |
| Lazy marker | 55.9% | 100.0% | 0.00 |
| A second human teacher | 80.2% | 99.9% | 0.75 |

## What it means

- **Strong:** multiple-choice reading and sheet totals.
- **Moderate:** written answers. Rarely far off, but the exact mark is right only about half the time.
- **Strict style is much better** than Generous on written answers.
- **Weak:** the double-check requests miss most wrong marks.

Full numbers for the paper: `results.json`.
