# Gemini-only rebuild (current work)

Started 2026-11-06. This section overrides the Milestone A rules below where they conflict.

## Rules
- Gemini does all AI work on the new path. No dataset-specific logic. Free only.
- Use only `GEMINI_API_KEY_2` (owner's choice), named by `GEMINI_KEY_ENV_NAME`. Never print, log or commit it. Other key slots stay unused.
- Firestore emulator only. No deleting files, no pushing. Commit on `wip` when a step's tests pass.
- Never guess marks. Unreadable, unmatched or uncertain = "needs teacher review".
- Stops: after steps 1, 5 and 9. Step 5 lists Ollama files and waits for OK before any deletion.

## Decisions (2026-11-06)
- One request per sheet both reads and grades (two only if over 12 pages). MCQ marks computed in code from the letter read.
- Milestone A decision 2 (no names or headers sent to Gemini) is retired for the Mendeley test sheets only. See "Personal data" under Step 1; real student sheets are an open question for the owner.
- Evaluation rubrics are auto-approved from `answerkey.txt` (Q1 = B) and labelled as such.

## Checklist
- Model changed 2026-11-06: 3.5 Flash-Lite returned 503 on every full request for over an hour, so `GEMINI_MODEL=gemini-3.1-flash-lite` (listed free, $0.25/$1.50 per 1M). Its AI Studio limits are not confirmed; pacing stays at 12 RPM and 200K TPM.
- [x] Step 1 Model setup: `GEMINI_MODEL=gemini-3.5-flash-lite` (`config.py` default, `.env`, `.env.example`). Test call OK (9 tokens). Stored quota block cleared. Tests: 281 passed, 2 skipped. Owner OK given 2026-11-06, billing confirmed off.
- [x] Step 2 Request budget: `services/gemini_client.py`. Limits from owner's AI Studio: 15 RPM, 250K TPM; paced at 12 and 200K. 500/day unconfirmed. Run cap 100, review cap 15 on `gemini-3.8-flash` (20/day unconfirmed). Daily count survives restarts, resets midnight Pacific. Log in git-ignored `backend/gemini_usage/calls.jsonl`. Notional cost at $0.30/$2.50 per 1M and Rs 94.48 per USD (frankfurter.dev, 2026-11-05); actual Rs 0. A bad JSON reply is not re-sent: the sheet goes to the teacher.
- [x] Step 3 Question paper: `services/gemini_paper.py`, `/exam/setup` takes an optional `answer_key` in the same one request. Own totals check handles sub-parts, OR (also across numbers, also with parts) and any K of N; mismatches are listed, never scaled. Marks need a printed quote; on papers with a text layer the quote is checked against it. MCQ suggestion only fills the picker if the teacher uploaded a key. `GRADING_ENGINE` setting: default `gemini`, conftest pins `local` until the old path goes.
- [x] Step 4 Answer sheet: `services/gemini_sheet.py`, one request reads and grades (two only over 12 pages). MCQ marked in code, key never sent. Flags: UNREADABLE, UNMATCHED, MISSING, LOW_CONFIDENCE (<0.8), PARTIAL_HIGH_VALUE (>=5 marks), EVIDENCE_NOT_FOUND, INVALID_MARK, UNCLEAR_OPTION, NO_KEY, DUPLICATE_ANSWER, NUMBER_INFERRED, ATTEMPT_RULE, SHEET_NOT_READ. Flagged marks stay suggestions. Second opinions from the stronger model are off by default and change nothing.
- Steps 3-4 real check: 3.5 Flash-Lite returned 503 "high demand" on every full-paper attempt on 2026-11-05 evening UTC, while tiny calls worked. Same requests on `gemini-3.1-flash-lite` (env override, one script): paper 35 questions, 50 marks, no problems, MCQ answers match key; Student_4 (tuning sheet) 1 request, 9,433 tokens, 20 of 20 MCQ letters match teacher record, 3 MISSING match teacher blanks; written marks disagree with teacher on some with high confidence (Q29 0 vs 2 at 0.95). Found and fixed: Gemini drafts no MCQ criteria, which blocked approval.
- [x] Step 5 default path: `/` no longer contacts Ollama, `/generate-rubric` and `/review/ocr-regrade` return 410 under Gemini, `pix2text`, `rapidocr`, `chromadb` moved to `requirements-local.txt`, app imports without them, `.env.example` local settings commented out. Owner did not choose between 3.5 and 3.1 Flash-Lite, so 3.5 stays configured.
- [x] Step 6 Queue: existing `queue_manager` kept; state and sheets moved to git-ignored `backend/queue_data/`; app start resumes pending sheets; daily limit pauses until midnight Pacific, run cap pauses until Resume, overload pauses 5 minutes and fails with no marks after 3 tries, retry allowed; a saved result with the same file and rubric is reused, so no second payment and no lost teacher edits; "grade one now" (`POST /evaluate`) goes ahead of the queue; Upload page polls progress. Fixed: bulk `/upload` crashed on an undefined variable; position-based student names could overwrite an earlier batch.
- [x] Step 7 Review: `Review.jsx` rewritten: flagged answers first, scanned page beside the read text, edit text and marks, accept suggestion, "blanks get 0", approve sheet, approve all unflagged. Only `POST /review/approve` makes a sheet FINAL; any later change undoes it. Unmatched writing must be ticked before approval. A MANUAL answer with no suggestion cannot be confirmed as 0 by accident. Overrides log Gemini's mark, old and new text, reason. Pages come from a local copy (`services/sheet_store.py`). Export refuses non-final sheets (409) or gives a copy marked NOT FINAL in file name, sheet title and a column. Fixed: export overwrote sub-parts with each other. Emulator mode never touches real Storage.
- [x] Step 8 Basics: 18 MB per file, 500 MB per request, CORS only for localhost, shared `TEACHER_PASSWORD` with a signed HttpOnly cookie (nothing works unset), versions pinned from the working venv, pytest in requirements, README. Frontend no longer uses Firebase auth or browser Firestore; Dashboard polls the API. **Owner must add `TEACHER_PASSWORD` to `backend/.env`.** CORS change means a Firebase-hosted frontend cannot reach this API.

## Step 5 deletions (owner OK 2026-11-06, in git history)
Deleted groups A, B, C and the root `app.js`, `index.html`, `style.css`. `GRADING_ENGINE` removed: Gemini is the only path. `test_question_paper_emulator.py` was deleted in the trim because all of it drove the local route; its general checks (paper conflict, approval cancelling, key safety) moved to `test_gemini_paper.py`. `scripts/evaluate_grading.py` rewritten for Gemini, with its own lock `evaluation/final_run_gemini.json`. Kept: `docs/eval/`, `backend/evaluation/`. Owner to remove old entries from the real `backend/.env`: `AI_PROVIDER`, `LOCAL_FIRST`, `OLLAMA_*`, `OPENROUTER_API_KEY`, old Gemini lists and flags.

## Known gaps so far
- `frontend/src/firebase.js` and the `firebase` npm package are now unused. `/review/ocr-region`, `/review/ocr-page` and the OCR fields in results are unused too. Left in place: not on the approved deletion list.
- `/exam/{id}/rubric/draft-criteria` still reads only a CSV key with fixed headers (no AI). Uploading the key with the paper is the general way.
- Papers that restart numbering per section (Part B Q1) are flagged as duplicate numbers, not handled.
- A misprinted section total is a warning at approval, not a block.
- `test_data/mendeley/clean_question_paper.pdf` is a different 20-mark paper. The Data Science exam is `Question.txt`.

## Step 1 notes
- Price (paid tier, pricing page): $0.30 input, $2.50 output per 1M tokens. Free tier listed as free of charge. Actual cost is Rs 0 only if the key's project has no billing; owner to confirm.
- Free limits: not published in docs; AI Studio's page needs sign-in. Only figure found: 500 requests/day, from a user-quoted 429 (forum, 2026-11-03). RPM and TPM unknown.
- Personal data: Gemini API terms (effective 2026-03-23) say of unpaid services that content may improve Google products, human reviewers may read input and output, and "Do not submit sensitive, confidential, or personal information". Consent does not change this.
- `GEMINI_MODEL_PRIORITY` and per-complexity model lists in `.env` still serve the old path; the new path must read `GEMINI_MODEL` only (step 2).

---

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
