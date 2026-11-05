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
- [x] A5 Tune on 10 sheets, compare two models. Kept: reader `qwen3.5:4b`, grader `gemma4:e4b`, only full marks auto-accept.
  - Reading only (`read-*`, before later reader rules): gemma MCQ 88.9%, short coverage 93.9%, 41 s/sheet. qwen MCQ 98.0%, coverage 89.9%, 76 s/sheet.
  - `tune1-*` (0bca73e): gemma MCQ 78.8%, short within 0.5 of all 27.3%, flagged 33.1%, silent wrong 48. qwen MCQ 86.9%, short 22.2%, flagged 29.7%, silent wrong 48. Cause: grader zeros on answers the teacher credited (52 of 56 written-answer zeros wrong; teacher gave 0 to 8 of 99).
  - `tune2-*` (53ae81f, generous prompts, `ZERO_ON_WRITTEN_ANSWER`): silent wrong 11-12, but the qwen grader still zeroed answers, short 23.2-26.3%.
  - Grader experiment, 87 answers, text fixed: qwen 33.3%, qwen + examples 39.1%, gemma 58.6%, gemma + examples 59.8%. Examples dropped. Always 1.5 scores 94.3%, so the 0.5 window rewards leniency; reports now show this baseline.
  - `tune3-*` (d9ae1b1, gemma grader): qwen reader 203 right and unflagged, 34.6% flagged, silent wrong 25. gemma reader 193, 36.9%, silent wrong 24. Unflagged full marks 34 right, 4 wrong; partial marks 35 right, 39 wrong.
  - `tune4-final-settings` (0c4456c, adds `PARTIAL_MARK`): MCQ 86.9%, silent wrong 3. Short unflagged 13 of 14 agree, 85.9% flagged, silent wrong 1. All answers flagged 45.7%. 216 s/sheet.
  - Gemini: during `tune4` a quota-type response at 21:15:53 IST set a stored block in `/tmp/autograde_gemini_provider_state.json`; no call returned a result. At least one request reached Google; whether it carried an answer crop is not recorded. Exact cause not known (one possibility: the verification path prefers `gemini-3.1-pro-preview`, which may have no free quota). Clearing it (`scripts/clear_gemini_quota_block.py`) needs the account owner to confirm the quota is back. Not cleared. `--gemini` now refuses to start when blocked.
- [x] Final run on the 40 report sheets, report in `docs/eval/final.md`. One run at b3025af, local only, no failed sheets, 246 s/sheet. MCQ exact 80.0% of all, 98.0% of unflagged, 13 silent wrong. Short within 0.5: 11.8% of all, 88.7% of unflagged, 86.7% flagged, 7 silent wrong. All answers flagged 48.9%. Always 1.5 scores 87.1%. All 40 sheets need review.

## Open
- Gemini-assisted numbers do not exist: the stored quota block is still in place and needs the account owner's decision. A new run on the report sheets would need `--rerun-reason`.

## Notes
- `c_ocr` is fixed at 0, which caps confidence at 0.80 under the 0.85 auto-accept line, so every answer flags today. A3 must replace this.
- MCQs have no deterministic path in `process_paper`. A3 must add one.
- Both models silently fix spelling and copy struck-out text. A2 prompt must address this.
- Real `.env` has all Gemini flags on. `pipeline.process_paper` and `regrade_question_text` run in `local_only()`. A3 must cap `generate_gemini_verification` at 20 per run and send crops only.
- `Student_Pdf` red ink audit (pixel count, no content read): 1 of 200 raw pages has teacher ink, Student_4 page 3, a tuning sheet. No report sheet affected. `Corrected_Pdf`: 173 of 200.
- Struck-out text still partly leaks into transcripts (Student_4 Q21 "have has", Q25 "Self-driving car"). Known reader limit; compare models in A5.
- Student number written with nothing after it comes back as empty text (Student_4 Q30, 33, 35), which is correct.
- Student_4 real run (draft rubric, default models): 222s, MCQ letters all match, Q30/33/35 BLANK_ANSWER match teacher blanks, but all 12 written short answers flagged CRITIC_DISAGREED and grader far harsher than teacher. This is what A5 must tune.
