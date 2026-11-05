# Milestone A: make grading work and measure it

Branch `wip`. Update after every step so a new session can resume.

## Rules
- Free tools only. Local Ollama models first. Gemini free key only as fallback for
  flagged answers, max 20 calls per run, off in tests.
- Firestore emulator only. Never real Firestore or the service account key.
- Do not delete files. Do not push. Commit on `wip` after each step passes its tests.
- Never guess marks. Unreadable, unmatched or low-confidence goes to the teacher as
  "needs review".
- Never tune on the 40 report sheets.

## Decisions (agreed 2026-11-05)
1. Q1 key stays B as in `answerkey.txt`. The teacher appears to have marked with D.
   The report shows MCQ marks under both keys; the D result is a side note, not a
   tuned score. Recorded letters, not MCQ totals, are the MCQ truth.
2. Gemini sees only the question, criteria and a crop of the answer. Never page
   headers or names. Local-only numbers are the main result, Gemini-assisted beside them.
3. Evaluation criteria are generated and auto-approved by the script, labelled as such
   in the report. Changes may come only from the 10 tuning sheets.

## Split (seed 20261105, one sheet per band of 5 by teacher total)
- Tuning: Students 4, 6, 7, 11, 19, 21, 26, 36, 43, 49
- Report: the other 40. Do not open or run them until the final run.

## Environment
- Python: `.venv/bin/python` (repo root).
- Emulator: `java -jar ~/.cache/firebase/emulators/cloud-firestore-emulator-v1.19.8.jar --host 127.0.0.1 --port 8080`

## Checklist
- [x] Baseline: 195 passed, 2 failed (test_e2e D, E), 2 skipped. Cause: resolver excludes both models, so one critic REJECT crashes the sheet. Fix in A3.
- [x] A1 Marking criteria: `services/criteria_draft.py`, `POST /exam/{id}/rubric/draft-criteria`, Setup page key upload and correct-option picker; approval blocks empty or blank criteria and MCQs without a valid option.
- [ ] A2 Read an answer sheet
- [ ] A3 Grade each answer
- [ ] A4 Evaluation script
- [ ] A5 Tune on 10 sheets, compare two models
- [ ] Final run on the 40 report sheets, report written

## Notes
- `c_ocr` is fixed at 0, which caps confidence at 0.80 under the 0.85 auto-accept line, so every answer flags today. A3 must replace this.
- MCQs have no deterministic path in `process_paper`. A3 must add one.
- Both models silently fix spelling and copy struck-out text. A2 prompt must address this.
