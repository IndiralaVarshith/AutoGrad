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
- [x] A2 Read an answer sheet: `OLLAMA_SHEET_READER_MODEL` (also shown at `GET /` and on the Upload result), `PaperResult.page_readers`, unmatched numbers saved as `NOT_ON_PAPER`, unreadable pages flag, faint pages skipped, `local_only` scope blocks automatic Gemini escalation.
- [x] A3 Grade each answer: MCQ by code, `route_answer` replaces the stuck `c_ocr` gate, flagged drafts live in `suggested_score` and only count once confirmed, failed grades flag one answer, Gemini only on flagged answers with `GEMINI_MAX_CALLS_PER_RUN` (default 20) and header-free crops. Baseline e2e failures fixed.
- [x] A4 Evaluation script: `backend/scripts/evaluate_grading.py`, metrics in `services/evaluation.py`, split in `backend/evaluation/split.json`. Reports in `docs/eval/`, transcripts in git-ignored `backend/evaluation/runs/`. Report split needs `--confirm-final` and committed code; a rerun needs `--rerun-reason`.
- [ ] A5 Tune on 10 sheets, compare two models
- [ ] Final run on the 40 report sheets, report written

## Notes
- `c_ocr` is fixed at 0, which caps confidence at 0.80 under the 0.85 auto-accept line, so every answer flags today. A3 must replace this.
- MCQs have no deterministic path in `process_paper`. A3 must add one.
- Both models silently fix spelling and copy struck-out text. A2 prompt must address this.
- Real `.env` has all Gemini flags on. `pipeline.process_paper` and `regrade_question_text` run in `local_only()`. A3 must cap `generate_gemini_verification` at 20 per run and send crops only.
- `Student_Pdf` red ink audit (pixel count, no content read): 1 of 200 raw pages has teacher ink, Student_4 page 3, a tuning sheet. No report sheet affected. `Corrected_Pdf`: 173 of 200.
- Struck-out text still partly leaks into transcripts (Student_4 Q21 "have has", Q25 "Self-driving car"). Known reader limit; compare models in A5.
- Student number written with nothing after it comes back as empty text (Student_4 Q30, 33, 35), which is correct.
- Student_4 real run (draft rubric, default models): 222s, MCQ letters all match, Q30/33/35 BLANK_ANSWER match teacher blanks, but all 12 written short answers flagged CRITIC_DISAGREED and grader far harsher than teacher. This is what A5 must tune.
