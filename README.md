# AutoGrade

AutoGrade helps a teacher mark handwritten answer sheets faster.

You upload the question paper and the students' answer sheets. Gemini reads them and suggests marks.
Anything it is unsure about is flagged for you to check. **No mark is final until you approve the sheet.**

It works with multiple choice, short answer and long answer questions, including sub-parts, OR choices
and "answer any 3 of 5".

---

## What it does, in short

- **Reads the question paper** and lists every question with its marks. A progress bar with a
  percentage and time left shows while it reads.
- **Marks each answer sheet with one Gemini request.**
- **Marking style:** choose Generous, Balanced or Strict for each exam, and add your own rules if you like.
- **No negative marking.** A mistake only lowers the mark for that point, and a mark never goes below 0.
- **Flags anything uncertain**, such as unreadable writing, a missing question number or a strange mark,
  so you check it beside the scanned page.
- **Calibration from one sheet (optional):** mark one sheet yourself and the app marks the rest the same way.
- **Delete buttons** for every exam, question paper and answer sheet.
- **Sign-in every time:** the app always opens on the password page when it starts.
- **Notebook look**, in light and dark mode.

---

## Important: real student data

The free Gemini tier lets Google read what you send and use it to improve its products. **Do not send
real students' sheets on the free tier.** Use a paid key, and get the school's permission first.

---

## Install (one time)

You need **Python 3.14**, **Node.js** and **Java**. Java runs the local database (the Firestore emulator).

```bash
python3 -m venv .venv
```

```bash
.venv/bin/pip install -r backend/requirements.txt
```

```bash
cd frontend && npm ci && cd ..
```

```bash
cp backend/.env.example backend/.env
```

Then open `backend/.env` and fill in:

| Setting | What to put |
|---|---|
| `GEMINI_API_KEY` | Your key from [Google AI Studio](https://aistudio.google.com/) |
| `GEMINI_MODEL` | The Gemini model to use |
| `TEACHER_PASSWORD` | The password you will sign in with |

---

## Start the app

Open three terminals and run one command in each.

**1. Database**

```bash
java -jar ~/.cache/firebase/emulators/cloud-firestore-emulator-v1.19.8.jar --host 127.0.0.1 --port 8080
```

**2. Backend**

```bash
cd backend && FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 ../.venv/bin/uvicorn main:app --port 8000
```

**3. Frontend**

```bash
cd frontend && npm run dev
```

Then open **<http://localhost:5173>**. Use `localhost`, not `127.0.0.1`, or sign-in will not work.

All data stays on your computer while you use the emulator.

---

## How to use it

### 1. Setup: the question paper
1. Choose an exam from the menu, or type a new exam ID.
2. Upload the question paper, and the answer key if you have one.
3. Check each question and its marks.
4. Pick a marking style (Generous, Balanced or Strict) and add your own rules if you want.
5. Press **Approve**.

### 2. Upload: the answer sheets
- Upload many sheets at once and they are marked one by one in the background.
- Or use **Mark this one now** for a single sheet.
- The queue carries on after a restart.

### 3. Review: check the marks
- Flagged answers are shown first, beside the scanned page.
- Fix the text or marks if needed, and write a short note when you change a mark.
- Press **Approve** when the sheet is right.

### 4. Results: download
- Download the marks spreadsheet once every sheet is approved.
- Before that, you can download a copy that is clearly marked **NOT FINAL**.

---

## Calibration (optional, for better accuracy)

Use this when you want the app to mark the way you do.

1. Mark one sheet with **Mark this one now**.
2. In Review, decide its flagged answers. Change any mark you disagree with and write why.
3. Press **Use this sheet to calibrate**.

From then on, every new sheet of that exam is marked using your marks and notes as the example.

- Sheets marked before calibration can be marked again with one click.
- Sheets you have already checked or changed are never re-marked.
- Multiple-choice answers are always marked straight from the answer key.
- If you approve new marks for the question paper, the old calibration stops applying.
- Deleting the calibration sheet stops the calibration.

---

## Deleting

| What | Where | What gets deleted |
|---|---|---|
| Exam | Exam menu | Everything: paper, marks, calibration and all sheets |
| Question paper | Setup | The paper, its approved marks and calibration. Not allowed while sheets use it. |
| Answer sheet | Upload, Review or Results | Its marks, notes, scanned pages and queue entry |

Deleted data is removed from the database and from every other place it was stored.

---

## Why an answer gets flagged

- The writing could not be read
- No matching question, or no question number written
- Gemini was not confident
- The quoted answer text is not on the page
- The mark is not possible for that question
- An unclear multiple-choice option
- More answers than the paper allows

A flagged mark counts as a suggestion until you decide. An answer with no flag can still be wrong, so
spot-check before approving many sheets at once.

---

## Gemini usage

- One request to read a question paper.
- One request per answer sheet (two if the sheet is over 12 pages).
- Requests are paced to stay within the free limits.
- If the daily limit runs out, the queue waits and carries on the next day by itself.
- Every request is logged in `backend/gemini_usage/calls.jsonl`.

---

## Put it online (Render)

Both parts go on [Render](https://render.com/), set up together by `render.yaml`:

- **Backend:** one Docker server, on Render's free plan.
- **Frontend:** a free static site. It forwards every `/api/...` request to the backend, so the site
  has one address and sign-in works in every browser.

Before you start: online, the data is stored in your real Firebase project, not on your computer.
Use a paid Gemini key for real students' sheets (see "Important: real student data" above).

### 1. Firebase
In the [Firebase console](https://console.firebase.google.com/) for your project, make sure
**Firestore** and **Storage** are turned on. You need the service account JSON file (Project settings →
Service accounts → Generate new private key).

### 2. Create both services
1. Push this repository to GitHub.
2. In Render, choose **New → Blueprint** and pick this repository. It creates
   `autograde-backend` and `autograde-frontend`.
3. Fill in the secret values when asked:

   | Setting | What to put |
   |---|---|
   | `GEMINI_API_KEY` | Your Gemini key |
   | `TEACHER_PASSWORD` | The sign-in password |
   | `FIREBASE_SERVICE_ACCOUNT_JSON` | The whole contents of the service account JSON file |

4. When the backend is live, open `https://autograde-backend-26z3.onrender.com/health`. It should say `healthy`.

### 3. Check the backend address
The frontend forwards `/api` to `https://autograde-backend-26z3.onrender.com`. If Render gave the backend a
different address (shown at the top of its page), put that address in `render.yaml` under
`autograde-frontend` → `routes` → `destination`, keeping the `/*` at the end, then commit and push.

Then open the frontend's address (`https://autograde-frontend.onrender.com` or similar) and sign in.

### Good to know
- **Free plan limits:** the backend sleeps after about 15 minutes with no visits, and the first visit
  after that takes about a minute to wake it. It has no disk, so when it sleeps or redeploys, sheets
  still waiting in the queue are lost and everyone is signed out. Keep the page open while sheets are
  being marked: the page checks the queue, which keeps the server awake.
- **For real use:** in `render.yaml` change the backend's `plan` to `starter` and add a disk
  (`name: autograde-data`, `mountPath: /data`, `sizeGB: 1`). It then stays awake and keeps queued
  sheets. This costs about $7 a month and needs a card. Keep it on one instance.
- Each request through the static site takes a little longer (a few hundred milliseconds). Marking
  happens in the background, so this hardly shows.
- If a big batch of sheets fails to upload, upload fewer sheets at a time.
- Restarting or redeploying the backend signs everyone out.

---

## Tests

Tests use fake Gemini replies, so they cost nothing. They wipe their database, so they use a
**separate emulator on port 8099**, never 8080.

```bash
java -jar ~/.cache/firebase/emulators/cloud-firestore-emulator-v1.19.8.jar --host 127.0.0.1 --port 8099
```

```bash
cd backend && ../.venv/bin/python -m pytest tests -q
```

---

## Project layout

| Folder or file | What it is |
|---|---|
| `backend/services/gemini_client.py` | Talks to Gemini: pacing, retries and usage log |
| `backend/services/gemini_paper.py` | Reads the question paper |
| `backend/services/gemini_sheet.py` | Reads and marks one answer sheet |
| `backend/services/calibration.py` | Calibration from one marked sheet |
| `backend/services/queue_manager.py` | The background marking queue |
| `backend/routes/` | The backend API |
| `frontend/src/pages/` | The pages: Login, Setup, Upload, Review, Results |
| `docs/PROGRESS.md` | Progress notes and known gaps |
