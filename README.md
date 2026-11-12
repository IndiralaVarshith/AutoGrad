# AutoGrade

AutoGrade reads handwritten exam answer sheets and drafts marks for a teacher to check.
Gemini reads the question paper, drafts the marking points, then reads and marks each answer sheet
in **one request per sheet**. Code checks that work and sends anything uncertain to the teacher.
**No mark is final until a teacher approves the sheet.**

Papers can be multiple choice, short or long answer, with sub-parts, OR choices and "answer any K of N".

## Before you use it on real exams

This setup uses Gemini's free tier. Google's terms for unpaid services say people may read what you
send and that it may be used to improve Google's products, and they say not to submit personal
information. **Real students' sheets should not go through the free tier.** Real exams need a paid
key, and the school's agreement.

## Setup

You need Python 3.14, Node.js and Java (for the Firestore emulator).

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
cd frontend && npm ci && cd ..
cp backend/.env.example backend/.env
```

Then, in `backend/.env`:

| Setting | What to put |
|---|---|
| `GEMINI_API_KEY` | Your key from [Google AI Studio](https://aistudio.google.com/). Keep billing off for free use. |
| `GEMINI_KEY_ENV_NAME` | The name of the entry holding the key, if it is not `GEMINI_API_KEY` |
| `GEMINI_MODEL` | The one model that does the work |
| `TEACHER_PASSWORD` | The password everyone signs in with. Nothing works until it is set. |

## Run it on the emulator

The emulator keeps all data on your machine. With `FIRESTORE_EMULATOR_HOST` set, the app never loads
the service account key or touches real Firestore or Storage.

```bash
# Terminal 1
java -jar ~/.cache/firebase/emulators/cloud-firestore-emulator-v1.19.8.jar --host 127.0.0.1 --port 8080

# Terminal 2
cd backend && FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 ../.venv/bin/uvicorn main:app --port 8000

# Terminal 3
cd frontend && npm run dev
```

Open <http://localhost:5173>. Use `localhost`, not `127.0.0.1`, so the sign-in cookie reaches the server.
Every start of the server begins at the sign-in page, and closing the browser signs you out.

## How a teacher uses it

1. **Setup:** choose or type an exam ID, upload the question paper, and the answer key if you have one.
   Check every question and mark, pick how strictly written answers are marked (Generous, Balanced or
   Strict), add your own marking rules if you like, then approve.
2. **Upload:** add many sheets for the background queue, or mark one now. The queue carries on after a
   restart. Each sheet is named after its file unless you name it.
3. **Review:** flagged answers come first, beside the scanned page. Correct the text and marks, write a
   note when you change a mark, then approve the sheet.
4. **Results and export:** the spreadsheet is refused until every sheet is approved. You can still
   download a copy clearly marked NOT FINAL.

### Calibrate with one sheet (optional)

Mark one sheet first ("Mark this one now"), decide its flagged answers in Review, change any mark you
disagree with and say why in the note. Then press **Use this sheet to calibrate**. Every written answer
on that sheet, with your mark and note, is sent with later sheets of the same exam, and Gemini marks to
your standard. Sheets marked before you calibrated can be marked again with one click (one Gemini
request each); sheets you have already worked on are never touched. Multiple-choice answers are always
marked by code from the key. Approving new marks for the exam makes an old calibration stop applying.

### Deleting

Every exam, question paper and answer sheet has a delete button:

- **Exam** (exam menu): deletes its paper, approved marks, calibration and every sheet.
- **Question paper** (Setup): deletes the paper, its approved marks and calibration; the exam name stays.
  Refused while sheets are marked against it.
- **Answer sheet** (Upload queue, Review, Results): deletes its marks, review notes, stored pages and
  queue entry. Deleting the calibration sheet also stops the calibration.

Deletes remove the data from Firestore, the local sheet copies and, outside the emulator, Firebase Storage.

### Why an answer gets flagged

Unreadable, no matching question, Gemini unsure (under `GEMINI_MIN_CONFIDENCE`), quoted evidence not in
the answer, an impossible mark, an unclear option, no number written, more answers than the paper
allows, or the sheet could not be read. Optional: partial marks on a question worth
`GEMINI_HIGH_VALUE_MARKS` or more, and zeros on written answers (`GEMINI_FLAG_ZERO_ON_WRITTEN`).
A flagged mark counts toward the total as a suggestion until the teacher decides.

An answer with no flag can still be wrong, so spot-check before approving in bulk.

## Request budget

- One request per question paper, and one per sheet (two only over 12 pages).
- Requests are paced under `GEMINI_RPM_LIMIT` and `GEMINI_TPM_LIMIT`, with backoff on 429 and overload.
- `GEMINI_RUN_CALL_CAP` limits calls in a batch. When reached, the queue waits for Resume.
- When the daily allowance runs out, the queue waits until midnight Pacific and carries on by itself.
- Calls, tokens and notional cost are logged per sheet in `backend/gemini_usage/calls.jsonl`.

## Tests

Tests use fake Gemini replies, and real calls are blocked. They clear their emulator, so they run on a
separate one on port 8099 and refuse to use 8080.

```bash
java -jar ~/.cache/firebase/emulators/cloud-firestore-emulator-v1.19.8.jar --host 127.0.0.1 --port 8099
```

```bash
cd backend && ../.venv/bin/python -m pytest tests -q
```

## Project layout

| Path | What it holds |
|---|---|
| `backend/services/gemini_client.py` | The only route to Gemini: pacing, retries, caps, cost log |
| `backend/services/gemini_paper.py` | Question paper to structured questions, totals checks |
| `backend/services/gemini_sheet.py` | One sheet in one request: read, mark, flag |
| `backend/services/calibration.py` | One teacher-marked sheet as the example for later sheets |
| `backend/services/queue_manager.py` | Background queue |
| `backend/routes/` | API routes |
| `frontend/src/pages/` | Login, Setup, Upload, Review, Results |
| `docs/PROGRESS.md` | Decisions, status and known gaps |
