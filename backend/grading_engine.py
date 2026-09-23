import json
import random
import re
import tempfile
import time
from pathlib import Path

import sympy as sp
from rapidfuzz.distance import Levenshtein

from google import genai

try:
    from pix2text import Pix2Text
except ImportError:
    Pix2Text = None


# =========================================================
# CONFIGURATION
# =========================================================

MODEL_NAME = "gemini-3.5-flash"
MAX_RETRIES = 4
EVIDENCE_THRESHOLD = 0.85


# =========================================================
# JSON SCHEMAS
# =========================================================

OCR_SCHEMA = {
    "type": "object",
    "properties": {
        "student_answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question_number": {"type": "string"},
                    "question_type": {
                        "type": "string",
                        "enum": ["Theory", "Diagram", "Numerical", "Mixed"],
                    },
                    "answer_text": {"type": "string"},
                    "contains_diagram": {"type": "boolean"},
                    "illegible_regions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "question_number",
                    "question_type",
                    "answer_text",
                    "contains_diagram",
                    "illegible_regions",
                ],
            },
        }
    },
    "required": ["student_answers"],
}


GRADING_SCHEMA = {
    "type": "object",
    "properties": {
        "criterion_results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "criterion": {"type": "string"},
                    "marks_awarded": {"type": "integer"},
                    "verbatim_quote": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": [
                    "criterion",
                    "marks_awarded",
                    "verbatim_quote",
                    "reason",
                ],
            },
        },
        "total_marks": {"type": "integer"},
        "overall_feedback": {"type": "string"},
    },
    "required": [
        "criterion_results",
        "total_marks",
        "overall_feedback",
    ],
}


CRITIC_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["ACCEPT", "REJECT"],
        },
        "challenged_criteria": {
            "type": "array",
            "items": {"type": "string"},
        },
        "reason": {"type": "string"},
    },
    "required": [
        "decision",
        "challenged_criteria",
        "reason",
    ],
}


MATH_SCHEMA = {
    "type": "object",
    "properties": {
        "problem_expression": {"type": "string"},
        "variable": {"type": "string"},
        "student_final_answer": {"type": "string"},
        "working_expression": {"type": "string"},
    },
    "required": [
        "problem_expression",
        "variable",
        "student_final_answer",
        "working_expression",
    ],
}


# =========================================================
# GRADING ENGINE
# =========================================================

class GradingEngine:

    def __init__(self, api_key):
        self.client = genai.Client(api_key=api_key)
        self.pix2text = None

    # =====================================================
    # GEMINI RETRY SUPPORT
    # =====================================================

    def _is_retryable(self, error):
        code = getattr(error, "code", None)

        try:
            code = int(code)
        except (TypeError, ValueError):
            return False

        return code in {408, 429, 500, 502, 503, 504}

    def _call_gemini(self, function, operation_name):
        last_error = None

        for attempt in range(MAX_RETRIES):
            try:
                print(
                    f"[Gemini] {operation_name} "
                    f"- attempt {attempt + 1}/{MAX_RETRIES}"
                )
                return function()

            except Exception as error:
                last_error = error

                if not self._is_retryable(error):
                    raise

                if attempt == MAX_RETRIES - 1:
                    raise

                delay = 2 ** attempt
                jitter = random.uniform(0, 1)
                wait_time = delay + jitter

                print(
                    f"[Gemini] Temporary error. "
                    f"Retrying in {wait_time:.1f}s"
                )
                time.sleep(wait_time)

        raise last_error

    # =====================================================
    # FILE UPLOAD
    # =====================================================

    def upload_answer_sheet(self, file_path, mime_type):
        return self._call_gemini(
            lambda: self.client.files.upload(
                file=file_path,
                config={"mime_type": mime_type},
            ),
            "Uploading answer sheet",
        )

    # =====================================================
    # OCR
    # =====================================================

    def extract_answers(self, uploaded_file, rubric):
        prompt = f"""
You are the OCR and answer-extraction module of AutoGrade.

Read the student's handwritten answer sheet.

The approved rubric is supplied below:

APPROVED RUBRIC:
{json.dumps(rubric, ensure_ascii=False, indent=2)}

TASK:

1. Identify every answer written by the student.
2. Separate answers using question numbers.
3. Preserve the student's wording as closely as possible.
4. DO NOT grade the answers.
5. DO NOT assign marks.
6. DO NOT summarize the student's answer.
7. If a word or region cannot be confidently read, write [ILLEGIBLE].
8. List the illegible regions separately.
9. Detect whether a diagram is present.
10. Use the question type from the approved rubric.
11. Preserve mathematical expressions as accurately as possible.
12. Return only the required JSON.

The output will be passed to the grading agents.
"""

        response = self._call_gemini(
            lambda: self.client.models.generate_content(
                model=MODEL_NAME,
                contents=[prompt, uploaded_file],
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": OCR_SCHEMA,
                    "temperature": 0.0,
                },
            ),
            "OCR / answer extraction",
        )

        if not response.text:
            raise RuntimeError("Gemini returned empty OCR output.")

        data = json.loads(response.text)
        answers = data.get("student_answers", [])

        all_text = " ".join(
            answer.get("answer_text", "") for answer in answers
        ).strip()

        illegible_count = sum(
            len(answer.get("illegible_regions", [])) for answer in answers
        )

        # Temporary OCR confidence fallback.
        # Replace with token-level logprob calculation when that capability
        # is wired into the production model/API setup.
        if not all_text:
            c_ocr = 0.30
        elif illegible_count == 0:
            c_ocr = 1.00
        elif illegible_count == 1:
            c_ocr = 0.85
        elif illegible_count == 2:
            c_ocr = 0.70
        else:
            c_ocr = 0.55

        return answers, c_ocr

    # =====================================================
    # GRADING AGENT
    # =====================================================

    def grade_answer(
        self,
        question,
        answer,
        rubric_question,
        uploaded_file,
    ):
        criteria = rubric_question.get("criteria", [])

        prompt = f"""
You are the AutoGrade Grading Agent.

Evaluate ONE student answer against the teacher-approved rubric.

QUESTION:
{question}

QUESTION TYPE:
{rubric_question.get("question_type", "Theory")}

TOTAL MARKS:
{rubric_question.get("total_marks", 0)}

STUDENT OCR ANSWER:
{answer}

RUBRIC:
{json.dumps(criteria, ensure_ascii=False, indent=2)}

INSTRUCTIONS:

1. Evaluate every rubric criterion.
2. Use only the student's answer and the rubric.
3. Award partial credit when the rubric supports it.
4. Never award more than the maximum marks assigned to a criterion.
5. For every criterion that receives marks, provide a VERBATIM QUOTE
   from the student's answer as evidence.
6. The quote must be copied from the student's text. Do not rewrite it.
7. If the criterion is not satisfied, marks may be zero and the evidence
   quote may be empty.
8. For diagram questions, inspect the uploaded answer-sheet image as well
   as the OCR text.
9. Do NOT invent information that is not present.
10. Return only the required JSON.
"""

        response = self._call_gemini(
            lambda: self.client.models.generate_content(
                model=MODEL_NAME,
                contents=[prompt, uploaded_file],
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": GRADING_SCHEMA,
                    "temperature": 0.1,
                },
            ),
            "Grading Agent",
        )

        if not response.text:
            raise RuntimeError("Grading Agent returned no output.")

        result = json.loads(response.text)
        return self._normalize_grade(result, criteria)

    # =====================================================
    # GRADE NORMALIZATION
    # =====================================================

    def _normalize_grade(self, grade, rubric_criteria):
        rubric_map = {}

        for criterion in rubric_criteria:
            name = criterion.get("criterion", "")
            rubric_map[name] = int(criterion.get("marks", 0))

        normalized_results = []

        for item in grade.get("criterion_results", []):
            name = item.get("criterion", "")
            max_marks = rubric_map.get(name, 0)

            try:
                marks = int(item.get("marks_awarded", 0))
            except (TypeError, ValueError):
                marks = 0

            marks = max(0, min(marks, max_marks))

            normalized_results.append(
                {
                    "criterion": name,
                    "max_marks": max_marks,
                    "marks_awarded": marks,
                    "verbatim_quote": item.get("verbatim_quote", ""),
                    "reason": item.get("reason", ""),
                }
            )

        total = sum(
            item["marks_awarded"] for item in normalized_results
        )

        max_total = sum(rubric_map.values())
        total = min(total, max_total)

        return {
            "criterion_results": normalized_results,
            "total_marks": total,
            "max_marks": max_total,
            "overall_feedback": grade.get("overall_feedback", ""),
        }

    # =====================================================
    # CRITIC AGENT
    # =====================================================

    def critique_grade(
        self,
        question,
        answer,
        rubric_question,
        grade,
        uploaded_file,
    ):
        prompt = f"""
You are the AutoGrade Critic Agent.

Your job is to independently review the Grading Agent's result.

QUESTION:
{question}

STUDENT ANSWER:
{answer}

APPROVED RUBRIC:
{json.dumps(rubric_question.get("criteria", []), ensure_ascii=False, indent=2)}

GRADING AGENT OUTPUT:
{json.dumps(grade, ensure_ascii=False, indent=2)}

Review the following:

1. Did the grader follow the rubric?
2. Are the awarded marks justified?
3. Are any criteria over-scored?
4. Are any important criteria incorrectly scored?
5. Are the evidence quotes actually representative of the student's answer?
6. For diagram questions, inspect the uploaded answer-sheet image.

Return:

ACCEPT if the grading is sufficiently justified.
REJECT if there is a meaningful grading error.

If rejecting, list the challenged criteria.

Do not change the grade yourself.
Do not assign a replacement score.

Return only the required JSON.
"""

        response = self._call_gemini(
            lambda: self.client.models.generate_content(
                model=MODEL_NAME,
                contents=[prompt, uploaded_file],
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": CRITIC_SCHEMA,
                    "temperature": 0.3,
                },
            ),
            "Critic Agent",
        )

        if not response.text:
            raise RuntimeError("Critic Agent returned no output.")

        return json.loads(response.text)

    # =====================================================
    # RESOLVER
    # =====================================================

    def resolve_disagreement(
        self,
        question,
        answer,
        rubric_question,
        original_grade,
        uploaded_file,
    ):
        print("[Resolver] Grader/Critic disagreement.")

        grade_1 = self.grade_answer(
            question,
            answer,
            rubric_question,
            uploaded_file,
        )

        grade_2 = self.grade_answer(
            question,
            answer,
            rubric_question,
            uploaded_file,
        )

        candidates = [original_grade, grade_1, grade_2]

        signatures = [
            self._grade_signature(grade) for grade in candidates
        ]

        counts = {}

        for index, signature in enumerate(signatures):
            counts.setdefault(signature, []).append(index)

        winner_signature = None
        winner_votes = 0

        for signature, indexes in counts.items():
            if len(indexes) > winner_votes:
                winner_signature = signature
                winner_votes = len(indexes)

        if winner_votes >= 2:
            winning_index = next(
                index
                for index, signature in enumerate(signatures)
                if signature == winner_signature
            )

            return {
                "status": "MAJORITY_REACHED",
                "grade": candidates[winning_index],
                "agreement": 0.75,
            }

        return {
            "status": "STILL_SPLIT",
            "grade": original_grade,
            "agreement": 0.40,
        }

    def _grade_signature(self, grade):
        return json.dumps(
            [
                (
                    item.get("criterion", ""),
                    item.get("marks_awarded", 0),
                )
                for item in grade.get("criterion_results", [])
            ],
            sort_keys=True,
        )

    # =====================================================
    # PIX2TEXT
    # =====================================================

    def get_pix2text(self):
        if self.pix2text is not None:
            return self.pix2text

        if Pix2Text is None:
            raise RuntimeError(
                "Pix2Text is not installed. Run: "
                "python3.10 -m pip install pix2text"
            )

        print("[Pix2Text] Initializing...")
        self.pix2text = Pix2Text()
        return self.pix2text

    def extract_math_text(self, answer_sheet_path):
        p2t = self.get_pix2text()

        print("[Pix2Text] Extracting text/formulas...")

        result = p2t.recognize(
            answer_sheet_path,
            return_text=True,
        )

        if isinstance(result, list):
            return "\n".join(str(item) for item in result)

        return str(result)

    # =====================================================
    # MATH AGENT
    # =====================================================

    def analyze_math_answer(
        self,
        question,
        answer,
        pix2text_output,
        uploaded_file,
    ):
        prompt = f"""
You are the AutoGrade Math Agent.

This is a NUMERICAL / MATHEMATICAL question.

QUESTION:
{question}

STUDENT OCR ANSWER:
{answer}

PIX2TEXT OUTPUT:
{pix2text_output}

Your task is NOT to blindly calculate the answer.

Your task is to convert the mathematical problem and student result
into a clean representation that can be checked deterministically by SymPy.

Return:

1. problem_expression
   The main equation or mathematical expression from the question.

2. variable
   The variable being solved for.
   Use an empty string if the problem does not solve for a variable.

3. student_final_answer
   The student's final answer or value.

4. working_expression
   A clean mathematical representation of the student's final working
   where possible.

RULES:

- Do not invent missing values.
- Preserve mathematical meaning.
- Use ordinary SymPy-compatible notation where possible.
- Example:
  2x + 4 = 10
  becomes:
  2*x + 4 = 10

- Return ONLY the required JSON.
"""

        response = self._call_gemini(
            lambda: self.client.models.generate_content(
                model=MODEL_NAME,
                contents=[prompt, uploaded_file],
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": MATH_SCHEMA,
                    "temperature": 0.1,
                },
            ),
            "Math Agent",
        )

        if not response.text:
            raise RuntimeError("Math Agent returned no output.")

        return json.loads(response.text)

    # =====================================================
    # SYMPY HELPERS
    # =====================================================

    def _sympy_locals(self):
        symbols = {}

        for name in [
            "x",
            "y",
            "z",
            "a",
            "b",
            "c",
            "m",
            "n",
            "t",
        ]:
            symbols[name] = sp.Symbol(name, real=True)

        symbols.update(
            {
                "pi": sp.pi,
                "E": sp.E,
                "sqrt": sp.sqrt,
                "sin": sp.sin,
                "cos": sp.cos,
                "tan": sp.tan,
                "log": sp.log,
                "exp": sp.exp,
            }
        )

        return symbols

    def _clean_math_expression(self, expression):
        if expression is None:
            return ""

        expression = str(expression).strip()
        expression = expression.replace("^", "**")
        expression = expression.replace("×", "*")
        expression = expression.replace("÷", "/")
        expression = re.sub(r"\s+", " ", expression)

        return expression

    def _is_safe_expression(self, expression):
        if not expression:
            return False

        # Permit basic ASCII mathematical notation.
        if not re.fullmatch(
            r"[0-9A-Za-z_+\-*/().=,\s*]+",
            expression,
        ):
            return False

        identifiers = set(
            re.findall(
                r"[A-Za-z_][A-Za-z_0-9]*",
                expression,
            )
        )

        allowed = {
            "x",
            "y",
            "z",
            "a",
            "b",
            "c",
            "m",
            "n",
            "t",
            "pi",
            "E",
            "sqrt",
            "sin",
            "cos",
            "tan",
            "log",
            "exp",
        }

        return identifiers.issubset(allowed)

    # =====================================================
    # SYMPY VERIFICATION
    # =====================================================

    def verify_with_sympy(self, math_data):
        problem = self._clean_math_expression(
            math_data.get("problem_expression", "")
        )

        variable_name = str(
            math_data.get("variable", "") or ""
        ).strip()

        student_answer = str(
            math_data.get("student_final_answer", "") or ""
        ).strip()

        if not self._is_safe_expression(problem):
            return {
                "status": "UNSUPPORTED",
                "c_math": 0.40,
                "message": "Problem expression is not supported.",
            }

        locals_map = self._sympy_locals()

        try:
            # ------------------------------------------------
            # CASE 1: EQUATION
            # ------------------------------------------------
            if "=" in problem:
                parts = problem.split("=")

                if len(parts) != 2:
                    return {
                        "status": "UNSUPPORTED",
                        "c_math": 0.40,
                        "message": "Multiple equality signs.",
                    }

                lhs = sp.sympify(
                    parts[0].strip(),
                    locals=locals_map,
                )

                rhs = sp.sympify(
                    parts[1].strip(),
                    locals=locals_map,
                )

                if variable_name not in locals_map:
                    return {
                        "status": "UNSUPPORTED",
                        "c_math": 0.40,
                        "message": "Unknown variable.",
                    }

                variable = locals_map[variable_name]

                solutions = sp.solve(
                    sp.Eq(lhs, rhs),
                    variable,
                )

                student_clean = student_answer

                if "=" in student_clean:
                    student_clean = student_clean.split("=", 1)[1].strip()

                if not self._is_safe_expression(student_clean):
                    return {
                        "status": "UNSUPPORTED",
                        "c_math": 0.40,
                        "message": "Student answer is not supported.",
                    }

                student_value = sp.sympify(
                    self._clean_math_expression(student_clean),
                    locals=locals_map,
                )

                for solution in solutions:
                    try:
                        difference = sp.simplify(solution - student_value)

                        if difference == 0:
                            return {
                                "status": "PASS",
                                "c_math": 1.00,
                                "message": "Student solution matches SymPy solution.",
                                "sympy_solution": str(solution),
                            }
                    except Exception:
                        continue

                return {
                    "status": "FAIL",
                    "c_math": 0.40,
                    "message": "Student result does not match the SymPy solution.",
                    "sympy_solution": str(solutions),
                }

            # ------------------------------------------------
            # CASE 2: DIRECT NUMERICAL EXPRESSION
            # ------------------------------------------------
            expression = sp.sympify(
                problem,
                locals=locals_map,
            )

            if expression.free_symbols:
                return {
                    "status": "UNSUPPORTED",
                    "c_math": 0.40,
                    "message": "Expression contains unresolved variables.",
                }

            expected = sp.N(expression)

            student_clean = student_answer

            if "=" in student_clean:
                student_clean = student_clean.split("=", 1)[1].strip()

            if not self._is_safe_expression(student_clean):
                return {
                    "status": "UNSUPPORTED",
                    "c_math": 0.40,
                    "message": "Student answer is not supported.",
                }

            student_value = sp.N(
                sp.sympify(
                    self._clean_math_expression(student_clean),
                    locals=locals_map,
                )
            )

            difference = abs(float(expected - student_value))

            if difference < 1e-8:
                return {
                    "status": "PASS",
                    "c_math": 1.00,
                    "message": "Final numerical answer matches SymPy evaluation.",
                    "expected": str(expected),
                }

            return {
                "status": "FAIL",
                "c_math": 0.40,
                "message": "Student result does not match SymPy evaluation.",
                "expected": str(expected),
                "student": str(student_value),
            }

        except Exception as error:
            return {
                "status": "UNSUPPORTED",
                "c_math": 0.40,
                "message": f"SymPy could not safely verify this expression: {error}",
            }

    # =====================================================
    # EVIDENCE VERIFIER
    # =====================================================

    def _best_quote_similarity(self, quote, answer_text):
        quote = quote.strip()
        answer_text = answer_text.strip()

        if not quote or not answer_text:
            return 0.0

        # Exact normalized substring match.
        if quote.casefold() in answer_text.casefold():
            return 1.0

        candidates = []

        # Compare against lines first.
        lines = [line.strip() for line in answer_text.splitlines() if line.strip()]
        candidates.extend(lines)

        # Also compare against nearby word windows so a short quote
        # can be checked against part of a longer OCR answer.
        words = answer_text.split()
        quote_word_count = max(1, len(quote.split()))

        window_sizes = {
            max(1, quote_word_count - 2),
            quote_word_count,
            quote_word_count + 2,
        }

        for window_size in window_sizes:
            if window_size <= 0:
                continue

            for start in range(
                0,
                max(0, len(words) - window_size + 1),
            ):
                candidates.append(
                    " ".join(words[start:start + window_size])
                )

        if not candidates:
            return 0.0

        return max(
            Levenshtein.normalized_similarity(
                quote,
                candidate,
            )
            for candidate in candidates
        )

    def verify_evidence(self, answer_text, grade):
        criteria = grade.get("criterion_results", [])

        if not criteria:
            return {
                "c_evid": 0.0,
                "valid": False,
                "criteria": [],
            }

        evidence_results = []

        for criterion in criteria:
            quote = str(
                criterion.get("verbatim_quote", "") or ""
            ).strip()

            similarity = self._best_quote_similarity(
                quote,
                answer_text,
            )

            verified = similarity >= EVIDENCE_THRESHOLD

            evidence_results.append(
                {
                    "criterion": criterion.get("criterion", ""),
                    "similarity": round(similarity, 4),
                    "verified": verified,
                }
            )

        c_evid = sum(
            item["similarity"] for item in evidence_results
        ) / len(evidence_results)

        valid = all(
            item["verified"] for item in evidence_results
        )

        return {
            "c_evid": round(c_evid, 4),
            "valid": valid,
            "criteria": evidence_results,
        }

    # =====================================================
    # CONFIDENCE ENGINE
    # =====================================================

    def calculate_confidence(
        self,
        c_ocr,
        c_agree,
        c_evid,
        c_math,
    ):
        final_confidence = (
            0.20 * c_ocr
            + 0.35 * c_agree
            + 0.25 * c_evid
            + 0.20 * c_math
        )

        return round(final_confidence, 4)

    # =====================================================
    # ROUTING
    # =====================================================

    def route_result(self, confidence, force_flag=False):
        if force_flag:
            return "FLAGGED"

        if confidence >= 0.85:
            return "AUTO ACCEPT"

        if confidence >= 0.65:
            return "FLAGGED"

        return "MANUAL REGRADING"

    # =====================================================
    # COMPLETE ANSWER-SHEET PIPELINE
    # =====================================================

    def process_answer_sheet(
        self,
        answer_sheet_path,
        answer_mime_type,
        rubric,
    ):
        # --------------------------------------------------
        # Upload answer sheet once and reuse the returned file.
        # --------------------------------------------------
        uploaded_file = self.upload_answer_sheet(
            answer_sheet_path,
            answer_mime_type,
        )

        # --------------------------------------------------
        # OCR
        # --------------------------------------------------
        answers, c_ocr = self.extract_answers(
            uploaded_file,
            rubric,
        )

        answer_map = {
            str(answer.get("question_number", "")).strip(): answer
            for answer in answers
        }

        # --------------------------------------------------
        # Pix2Text is only needed when at least one question is numerical.
        # --------------------------------------------------
        pix2text_output = ""

        has_numerical = any(
            question.get("question_type") in {"Numerical", "Mixed"}
            for question in rubric.get("questions", [])
        )

        if has_numerical:
            try:
                pix2text_output = self.extract_math_text(
                    answer_sheet_path
                )
            except Exception as error:
                print("[Pix2Text] Error:", repr(error))
                pix2text_output = ""

        # --------------------------------------------------
        # Process questions
        # --------------------------------------------------
        question_results = []

        for rubric_question in rubric.get("questions", []):
            q_number = str(
                rubric_question.get("question_number", "")
            ).strip()

            question_text = rubric_question.get(
                "question_text",
                "",
            )

            question_type = rubric_question.get(
                "question_type",
                "Theory",
            )

            answer_data = answer_map.get(q_number, {})
            answer_text = answer_data.get(
                "answer_text",
                "",
            )

            # =================================================
            # NUMERICAL PATH
            # =================================================
            if question_type == "Numerical":
                math_data = self.analyze_math_answer(
                    question_text,
                    answer_text,
                    pix2text_output,
                    uploaded_file,
                )

                math_result = self.verify_with_sympy(
                    math_data
                )

                # Math questions do not use Grader/Critic in this path.
                c_agree = 1.00
                c_evid = 1.00 if answer_text else 0.0
                c_math = math_result.get("c_math", 0.40)

                final_confidence = self.calculate_confidence(
                    c_ocr,
                    c_agree,
                    c_evid,
                    c_math,
                )

                route = self.route_result(
                    final_confidence
                )

                # We do not assign a score automatically here unless
                # the current math implementation can verify it.
                score = (
                    rubric_question.get("total_marks", 0)
                    if math_result.get("status") == "PASS"
                    else None
                )

                question_results.append(
                    {
                        "question_number": q_number,
                        "question_text": question_text,
                        "question_type": question_type,
                        "answer_text": answer_text,
                        "score": score,
                        "max_marks": rubric_question.get("total_marks", 0),
                        "grading": None,
                        "critic": None,
                        "resolver": None,
                        "math": {
                            "pix2text": pix2text_output,
                            "analysis": math_data,
                            "verification": math_result,
                        },
                        "evidence": None,
                        "confidence": {
                            "c_ocr": c_ocr,
                            "c_agree": c_agree,
                            "c_evid": c_evid,
                            "c_math": c_math,
                            "c_final": final_confidence,
                        },
                        "decision": route,
                    }
                )

                continue

            # =================================================
            # THEORY / DIAGRAM PATH
            # =================================================
            grade = self.grade_answer(
                question_text,
                answer_text,
                rubric_question,
                uploaded_file,
            )

            critic = self.critique_grade(
                question_text,
                answer_text,
                rubric_question,
                grade,
                uploaded_file,
            )

            resolver = None
            c_agree = 1.00
            force_flag = False

            # -------------------------------------------------
            # Resolver only when Critic rejects.
            # -------------------------------------------------
            if critic.get("decision") == "REJECT":
                resolver = self.resolve_disagreement(
                    question_text,
                    answer_text,
                    rubric_question,
                    grade,
                    uploaded_file,
                )

                grade = resolver["grade"]
                c_agree = resolver["agreement"]

                if resolver["status"] == "STILL_SPLIT":
                    force_flag = True

            # -------------------------------------------------
            # Evidence
            # -------------------------------------------------
            evidence = self.verify_evidence(
                answer_text,
                grade,
            )

            c_evid = evidence.get("c_evid", 0.0)

            # No math verification is used for theory/diagram questions.
            # A neutral value of 1.0 prevents the math component from
            # penalizing non-mathematical questions.
            c_math = 1.00

            final_confidence = self.calculate_confidence(
                c_ocr,
                c_agree,
                c_evid,
                c_math,
            )

            decision = self.route_result(
                final_confidence,
                force_flag,
            )

            question_results.append(
                {
                    "question_number": q_number,
                    "question_text": question_text,
                    "question_type": question_type,
                    "answer_text": answer_text,
                    "score": grade.get("total_marks", 0),
                    "max_marks": rubric_question.get("total_marks", 0),
                    "grading": grade,
                    "critic": critic,
                    "resolver": resolver,
                    "math": None,
                    "evidence": evidence,
                    "confidence": {
                        "c_ocr": c_ocr,
                        "c_agree": c_agree,
                        "c_evid": c_evid,
                        "c_math": c_math,
                        "c_final": final_confidence,
                    },
                    "decision": decision,
                }
            )

        # --------------------------------------------------
        # Overall score
        # --------------------------------------------------
        total_awarded = sum(
            result["score"]
            for result in question_results
            if result["score"] is not None
        )

        total_possible = sum(
            result["max_marks"]
            for result in question_results
        )

        return {
            "success": True,
            "model": MODEL_NAME,
            "total_score": total_awarded,
            "total_marks": total_possible,
            "questions": question_results,
        }
