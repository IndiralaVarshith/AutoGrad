# Handwriting reference review

The reference text in `handwriting_ground_truth.json` is an unverified candidate. Do not use its CER/WER as an accuracy claim until a teacher has checked every segment against the source scan.

## Review procedure

1. Open the private original PDF and inspect each listed page and normalized bounding box at the recorded render DPI. Keep the source PDF in its existing access-controlled location.
2. Transcribe what is actually written, including misspellings and grammar. Do not correct the student's wording. Record crossed-out text separately; do not silently include or discard it. Mark genuinely unreadable spans as unreadable rather than guessing.
3. Confirm the question number and page continuation from the image. For mathematical content, check every symbol, sign, exponent, fraction, and unit against the image.
4. Edit `reference_text` only after checking the image. Do not copy an OCR or model response as the reference.
5. For each segment, set `verification.status` to `TEACHER_VERIFIED`, enter a reviewer pseudonym in `verified_by`, and an ISO 8601 UTC timestamp in `verified_at`. Only after all segments are reviewed, set the matching top-level `annotation_status`, `verified_by`, and `verified_at` fields.
6. Leave an uncertain segment unverified. If a second teacher is available, have them independently check ambiguous handwriting, cross-outs, question mapping, and mathematical notation before sign-off.
7. Run the benchmark locally. The script reports CER/WER as `TEACHER_VERIFIED` only when the top-level and every segment sign-off is present. Otherwise all metrics are marked diagnostic-only. The script emits counts and metrics, not answer text.

Use pseudonymous reviewer identifiers. Do not add student names, hall tickets, or other student identifiers to this fixture. Keep consent and access-control requirements for the source PDF separate from benchmark annotation; benchmark sign-off is not student consent to retain or train on the scan.
