# AutoGrade evaluation results

## Mendeley, 40 held-out sheets, default (Generous)

Sheets scored: 40

| Variant | n | Exact | Within 0.5 | MAE | QWK |
|---|---|---|---|---|---|
| full | 465 | 51.0% | 64.5% | 0.465 | 0.596 |
| no_length_cap | 465 | 56.1% | 62.6% | 0.448 | 0.604 |
| raw_llm | 465 | 56.1% | 62.6% | 0.448 | 0.604 |
| constant | 465 | 55.1% | 56.8% | 0.563 | 0.000 |

MCQ letters read correctly: 97.7% of 789
Sheet totals: MAE 3.862, Pearson 0.865
Flags: 2.6% of written answers flagged; 2.4% of wrong marks (off by more than 0.5) were flagged; precision 33.3%; unflagged answers within 0.5: 64.5% (n=453)

## Mendeley, 40 held-out sheets, calibrated from Student_21

Sheets scored: 40

| Variant | n | Exact | Within 0.5 | MAE | QWK |
|---|---|---|---|---|---|
| full | 465 | 52.0% | 64.3% | 0.480 | 0.564 |
| no_length_cap | 465 | 55.9% | 63.2% | 0.466 | 0.572 |
| raw_llm | 465 | 55.9% | 63.2% | 0.466 | 0.572 |
| constant | 465 | 55.1% | 56.8% | 0.563 | 0.000 |

MCQ letters read correctly: 95.8% of 789
Sheet totals: MAE 4.475, Pearson 0.761
Flags: 3.9% of written answers flagged; 5.4% of wrong marks (off by more than 0.5) were flagged; precision 50.0%; unflagged answers within 0.5: 64.9% (n=447)

## CHECK-MAT (expert marks 0–4)

Solutions scored: 122

| System | n | Exact | Within 1 mark | MAE | QWK |
|---|---|---|---|---|---|
| AutoGrade full | 122 | 44.3% | 74.6% | 0.852 | 0.429 |
| AutoGrade full (rounded to whole marks) | 122 | 44.3% | 74.6% | 0.852 | 0.429 |
| AutoGrade raw_llm | 122 | 44.3% | 74.6% | 0.852 | 0.429 |
| AutoGrade raw_llm (rounded to whole marks) | 122 | 44.3% | 74.6% | 0.852 | 0.429 |
| Constant (most common mark per task) | 122 | 46.7% | 77.0% | 0.828 | 0.323 |
| openai/o4-mini (published, without answer) | 122 | 55.7% | 82.8% | 0.664 | 0.539 |

Per task (AutoGrade full):

| Task | n | Exact | MAE | QWK |
|---|---|---|---|---|
| 13 | 21 | 42.9% | 0.857 | -0.094 |
| 14 | 18 | 27.8% | 1.389 | 0.092 |
| 15 | 19 | 52.6% | 0.737 | 0.216 |
| 16 | 17 | 58.8% | 0.471 | 0.509 |
| 17 | 15 | 53.3% | 0.733 | 0.492 |
| 18 | 16 | 31.2% | 1.188 | 0.408 |
| 19 | 16 | 43.8% | 0.562 | 0.757 |

Half marks given by AutoGrade: 0 of 122. Median time 5.6 s; mean tokens 1789 in, 787 out.

Flags: 45.1% flagged; 51.5% of wrong marks flagged; unflagged exact 50.7% (n=67)

## Handwritten ASAP short answers (prompts 3 and 4, marks 0–2), default (Generous)

Answers scored: 349 of 349. AutoGrade's half marks are rounded up to whole marks, as ASAP marks are whole numbers.

| System | n | Exact | Within 1 mark | MAE | QWK |
|---|---|---|---|---|---|
| AutoGrade full | 349 | 38.7% | 94.3% | 0.670 | 0.376 |
| AutoGrade no_length_cap | 349 | 38.7% | 94.3% | 0.670 | 0.376 |
| AutoGrade raw_llm | 349 | 38.7% | 94.3% | 0.670 | 0.376 |
| Constant (most common mark per prompt) | 349 | 55.9% | 100.0% | 0.441 | 0.000 |
| Human marker 1 vs marker 2 (ASAP training answers) | 2415 | 80.2% | 99.9% | 0.199 | 0.752 |

Per prompt (exact / QWK):

| Prompt | n | AutoGrade full | no length cap | Humans |
|---|---|---|---|---|
| 3 | 185 | 45.4% / 0.430 | 45.4% / 0.430 | 79.0% / 0.744 |
| 4 | 164 | 31.1% / 0.309 | 31.1% / 0.309 | 81.6% / 0.740 |

AutoGrade (full) against the human mark, all answers scored:

| Human mark | AutoGrade 0 | AutoGrade 1 | AutoGrade 2 |
|---|---|---|---|
| 0 | 26 | 53 | 20 |
| 1 | 0 | 56 | 139 |
| 2 | 0 | 2 | 53 |

Flags (not counting 'number inferred'): 1.1% flagged; 1.9% of wrong marks flagged; precision 100.0%; unflagged exact 39.1% (n=345)

## Handwritten ASAP short answers (prompts 3 and 4, marks 0–2), Strict marking style

Answers scored: 349 of 349. AutoGrade's half marks are rounded up to whole marks, as ASAP marks are whole numbers.

| System | n | Exact | Within 1 mark | MAE | QWK |
|---|---|---|---|---|---|
| AutoGrade full | 349 | 59.9% | 99.1% | 0.410 | 0.544 |
| AutoGrade no_length_cap | 349 | 44.1% | 96.8% | 0.590 | 0.452 |
| AutoGrade raw_llm | 349 | 44.1% | 96.8% | 0.590 | 0.452 |
| Constant (most common mark per prompt) | 349 | 55.9% | 100.0% | 0.441 | 0.000 |
| Human marker 1 vs marker 2 (ASAP training answers) | 2415 | 80.2% | 99.9% | 0.199 | 0.752 |

Per prompt (exact / QWK):

| Prompt | n | AutoGrade full | no length cap | Humans |
|---|---|---|---|---|
| 3 | 185 | 62.7% / 0.587 | 50.8% / 0.500 | 79.0% / 0.744 |
| 4 | 164 | 56.7% / 0.449 | 36.6% / 0.390 | 81.6% / 0.740 |

AutoGrade (full) against the human mark, all answers scored:

| Human mark | AutoGrade 0 | AutoGrade 1 | AutoGrade 2 |
|---|---|---|---|
| 0 | 32 | 64 | 3 |
| 1 | 0 | 128 | 67 |
| 2 | 0 | 6 | 49 |

Flags (not counting 'number inferred'): 2.0% flagged; 2.9% of wrong marks flagged; precision 57.1%; unflagged exact 60.2% (n=342)
