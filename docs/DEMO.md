# Live demo: 5 answer sheets

About 10 minutes. You show a teacher's whole path: paper to rubric, sheets to marks, review, final
spreadsheet. The sheets are Student_4, 6, 7, 11 and 19 from `test_data/mendeley/Student_Pdf`. They are
tuning sheets, so the accuracy numbers come from other sheets.

## What you need

Set these in `backend/.env` before the day:

| Setting | Value |
|---|---|
| `TEACHER_PASSWORD` | Any password. Nothing works until it is set. |
| `GEMINI_KEY_ENV_NAME` | The entry that holds your key: `GEMINI_API_KEY_2` |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` |

The live run makes 6 Gemini requests: one for the paper and one per sheet.

## Pre-run, the day before

Use a terminal for each server, from the repo root.

```bash
# Terminal 1: the emulator. Restarting it clears old test exams from the screens.
java -jar ~/.cache/firebase/emulators/cloud-firestore-emulator-v1.19.8.jar --host 127.0.0.1 --port 8080

# Terminal 2: backend
cd backend && FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 ../.venv/bin/uvicorn main:app --port 8000

# Terminal 3: frontend
cd frontend && npm run dev
```

Open <http://localhost:5173>, using `localhost` and not `127.0.0.1`, or the sign-in will not stick. If
something already holds port 8000 or 5173, stop it first.

1. Run the whole demo below once with the exam ID `demo-5`, stopping before you review anything.
2. Save the results as the backup. This replaces the one already saved:
   ```bash
   cd backend && ../.venv/bin/python scripts/demo_backup.py save --exam demo-5
   ```
3. Have these open or ready: `Question.txt`, `answerkey.txt` and the 5 sheet PDFs in a folder.
4. Open [AI Studio's rate limit page](https://aistudio.google.com/rate-limit) and check the model has quota.

**Do not run the tests once the demo is set up.** They clear the emulator, which removes your exams. If
that happens, restore the backup.

The backup is at `backend/evaluation/runs/demo-backup/demo-5.json`. It is git-ignored, so it stays on
this machine. Copy it if you present from another one.

## On the day

Restart the emulator so the screens are clean, start all three servers, and run the fallback restore now
(see below), so the backup is ready behind the live exam. Then:

| Step | Do | Say | Time |
|---|---|---|---|
| 1. Sign in | Enter the password | One shared teacher password | 10 s |
| 2. Paper | Setup: exam ID `demo-live`, add `Question.txt` and `answerkey.txt`, press Analyze the paper | One request reads the paper and key. Marks come only from what is printed. | 15 s |
| 3. Rubric | Show "Not printed" for the total, the marking points, then tick the box and press Approve | This paper prints no total, so 50 is calculated and the teacher confirms it. Nothing is marked until a teacher approves this. | 1 min |
| 4. Sheets | Upload: choose the 5 PDFs, press Submit to Queue | One at a time, about 10 s each, with live progress | 1 min |
| 5. Restart (optional) | Press Ctrl+C on the backend mid-queue and start it again | The queue carries on by itself. | 30 s |
| 6. Review | Review: open Student_4 | Flagged answers first, page beside the text. Marks not decided yet do not count toward the total. | 2 min |
| 7. Correct | Fix a mark and some text, press Save my marks. The next flagged answer opens at once. Press Accept on another. | The teacher decides. Every override is logged. | 1 min |
| 8. Blanks | Press "I checked: N blanks get 0" | A blank is never silently marked 0. | 15 s |
| 9. Student_6 | Open it | About 30 flags: the student's answers are out of order, and Gemini says it worked out numbers it could not read. Flags like this are the design working. | 30 s |
| 10. Export | Results: press Export | Refused until sheets are approved. The copy you can still get says NOT FINAL in its name, sheet title and a column. | 30 s |
| 11. Final | Decide the remaining flags on one sheet, approve it | Only this makes a sheet final. Finish the rest to show the final spreadsheet if time allows. | 1 min |

The answers change a little between runs, so check what is on screen rather than quoting exact marks from
this page. The live marks may differ from the backup's.

## If network or quota fails

Nothing needs to reach Google. The backup restores the paper, the approved rubric, all 5 graded sheets
and their page images, with no Gemini call:

```bash
cd backend && FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 ../.venv/bin/python scripts/demo_backup.py restore --exam demo-5
```

Then, in the app: choose `demo-5` from the **Exam ID** menu at the top of the page, then Review. Carry on from step 6, and say plainly
that these are saved results from an earlier run.

| What you see | What it means | What to do |
|---|---|---|
| Queue says "Gemini is busy" | Overload. The queue retries by itself in 5 minutes. | Switch to the backup. |
| Queue says the daily allowance is used up | It waits until midnight Pacific. | Switch to the backup. |
| Queue asks to Resume | The call cap for the batch was reached. | Press Resume. |
| Setup shows 429 or 503 | Gemini refused the paper, and nothing was saved. | Try once, then use the backup. |
| "Please sign in" after signing in | Opened with `127.0.0.1`, or the password changed. | Use `localhost:5173`, and sign in again. |
| "No approved marks" | Exam ID is wrong. | Check the Exam ID menu at the top of the page. |

## Be straight about accuracy

From the 40 sheets kept out of tuning (`docs/eval/gemini-final.md`). Please don't describe this as
automatic grading:

- **Multiple choice reading is strong:** 94.0% exact, 3.4% flagged, and still 20 wrong letters with no flag.
- **Written marking is a draft:** 56.1% of proposed marks match the teacher exactly. Always giving 2 scores
  55.1%. Its error is smaller, 0.449 against 0.563, so it helps, but a teacher has to check it.
- **Flags do not catch everything:** 28 short answers got full marks with no flag where the teacher gave less.
  Gemini's confidence did not reliably predict accuracy.
- **About a third of answers go to the teacher** (31.9%, or 22.7% leaving out blanks).
- **Free tier:** ₹0 here, about ₹0.62 per sheet at paid rates, and not suitable for real students' sheets,
  because Google's terms say not to submit personal information on unpaid services.
