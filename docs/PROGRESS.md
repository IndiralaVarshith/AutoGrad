# AutoGrade progress

Branch `wip`. Earlier history (local models, the Mendeley evaluation, the two-call setup) is in git.

## How it works now (2026-11-12)

- **One Gemini request per sheet** (two only over 12 pages). No second marking pass and no review
  model: the second pass returned a copy of the first on all 85 real answers checked, so it doubled the
  cost and caught nothing.
- **Marking styles:** Generous (default), Balanced, Strict, plus the teacher's own rules, chosen per exam
  at approval. No negative marking: a mistake lowers only the point it belongs to, once, never below 0.
- **Length check:** about 20 words per mark. Working in symbols (sums, proofs) is not counted in words,
  "show that/verify/derive/determine/construct/plot" questions are exempt, and an answer as long as its
  marks need is never cut for length.
- **Quotes:** a quote Gemini joins with "..." is checked part by part. Before this, most "quote not
  found" flags on real sheets were false (4 per sheet on Udit's and Vedesh's DPT-6 sheets, 0 after).
- **Question papers:** prompt `gemini-paper-v4` keeps every field short and forbids reasoning in the
  reply; a cut-off reply is retried once with more room. The physics paper that was cut off twice now
  reads in 6 s. Scanned papers no longer get false "marks text not found" problems.
- **Calibration from one sheet** (`services/calibration.py`, `routes/calibration.py`): the teacher marks
  one sheet, writes notes on changed marks, and chooses "Use this sheet to calibrate" (approves it if every
  answer is decided). Every written answer on it, with the app's mark, the teacher's mark and note, goes
  into later sheets' one request as `teacher_example`. Applies only to the same approved-marks version,
  never to the calibration sheet itself. "Mark again" re-grades sheets the teacher has not touched (one
  request each); a sheet with any teacher decision is never re-graded. Deleting the calibration sheet stops
  the calibration. Replaced the earlier five-sheet mark mapping.
- **Deleting:** exam (everything), question paper (refused while sheets depend on it), answer sheet
  (marks, review notes, stored pages, queue entry, Storage copy outside the emulator).
- **Sign-in:** sessions belong to one run of the server and the cookie ends with the browser, so every
  start of the app begins at the sign-in page. After sign-in the app opens on Setup.
- **Frontend:** notebook look (ruled paper, margin line, handwritten headings, highlighter titles), light
  and dark.

## Checked

- 301 backend tests on the 8099 test emulator; frontend lint (no errors) and build.
- End to end with real Gemini on the 8099 emulator: social DPT-6 paper read and approved (20 marks), two
  sheets marked in one request each, review in the browser, calibration from Udit's sheet, re-mark of
  Vedesh's sheet with the calibration, a sheet uploaded after calibration marked with it, sheet / paper /
  exam deletes from the UI with nothing left in the stores, sign-in required after a server restart.

## Known gaps

- Calibration accuracy is not measured on real data: the Narayana set has one teacher-marked sheet per
  paper (Udit: Sustainable Development 18/20, Food Security 18.5/20). Two or three corrected sheets of one
  paper are needed to measure it.
- On those two sheets the app is kinder than the teacher on long answers (Food Security Section III: app
  8, teacher 6.5); Strict mode closed the gap on one sheet but not the other.
- Strict mode marked the same answer differently for two students (physics Q3, "3 images": 1 and 0).
- The 25-01-2021 social paper reads as 35 marks against a printed 40 (flagged for the teacher).
- `gemini-3.1-flash-lite` free limits are unconfirmed; 500 requests a day is assumed.
- A cancel request takes effect only before a sheet starts.
- Papers that restart numbering per section (Part B Q1) are flagged as duplicate numbers, not handled.
