"""
AutoGrade — Math Agent

Pix2Text → Gemini LaTeX cleanup → SymPy symbolic verification.
All SymPy calls are wrapped in try/except. A parse failure
sets c_math=0.4 and falls back to LLM-method evaluation.
"""

from __future__ import annotations

import json
import re

import sympy as sp

from models.schemas import MathResult
from config import get_gemini_client, GEMINI_MODEL

try:
    from pix2text import Pix2Text
except ImportError:
    Pix2Text = None

_pix2text_instance = None


# =========================================================
# PIX2TEXT INITIALISATION
# =========================================================

def _get_pix2text():
    """Lazily initialise Pix2Text."""
    global _pix2text_instance

    if _pix2text_instance is not None:
        return _pix2text_instance

    if Pix2Text is None:
        raise RuntimeError(
            "Pix2Text is not installed. "
            "Run: pip install pix2text"
        )

    print("[Pix2Text] Initialising...")
    _pix2text_instance = Pix2Text()
    return _pix2text_instance


# =========================================================
# SYMPY SYMBOL MAP
# =========================================================

def _sympy_locals() -> dict:
    """Return a safe symbol map for sympify."""
    symbols = {}
    for name in ["x", "y", "z", "a", "b", "c", "m", "n", "t"]:
        symbols[name] = sp.Symbol(name, real=True)

    symbols.update({
        "pi": sp.pi,
        "E": sp.E,
        "sqrt": sp.sqrt,
        "sin": sp.sin,
        "cos": sp.cos,
        "tan": sp.tan,
        "log": sp.log,
        "exp": sp.exp,
    })
    return symbols


def _clean_latex(expression: str) -> str:
    """Basic cleanup for LaTeX → SymPy compatible expression."""
    if not expression:
        return ""
    expr = str(expression).strip()
    expr = expr.replace("^", "**")
    expr = expr.replace("×", "*")
    expr = expr.replace("÷", "/")
    expr = re.sub(r"\s+", " ", expr)
    return expr


# =========================================================
# GEMINI LATEX SYNTAX FIXER
# =========================================================

LATEX_FIX_PROMPT = """
Fix only LaTeX syntax errors in this expression without
changing its mathematical meaning.
Return only the corrected LaTeX. Nothing else.
"""


def _fix_latex_with_gemini(raw_latex: str) -> str:
    """Use Gemini to fix LaTeX syntax errors."""
    client = get_gemini_client()

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[LATEX_FIX_PROMPT, raw_latex],
        config={"temperature": 0.1}
    )

    if response.text:
        return response.text.strip()
    return raw_latex


# =========================================================
# MAIN: VERIFY MATH ANSWER
# =========================================================

def verify_math(
    image_bytes: bytes,
    model_answer_expr: str,
) -> MathResult:
    """
    Full math verification pipeline:
    1. Pix2Text → raw LaTeX from image
    2. Gemini LaTeX syntax fix
    3. SymPy: parse + simplify(student - model) == 0
    4. Fallback if SymPy fails

    Parameters
    ----------
    image_bytes : bytes
        The question image bytes.
    model_answer_expr : str
        The model answer expression (LaTeX or SymPy-compatible).

    Returns
    -------
    MathResult
        Contains c_math, symbolic_correct, latex_extracted.
    """

    # -------------------------------------------------
    # Step 1: Pix2Text extraction
    # -------------------------------------------------
    latex_extracted = ""

    try:
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(
            delete=False, suffix=".png"
        ) as tmp:
            tmp.write(image_bytes)
            tmp_path = tmp.name

        try:
            p2t = _get_pix2text()
            result = p2t.recognize(tmp_path, return_text=True)

            if isinstance(result, list):
                latex_extracted = "\n".join(str(r) for r in result)
            else:
                latex_extracted = str(result)
        finally:
            os.unlink(tmp_path)

    except Exception as error:
        print(f"[MathAgent] Pix2Text error: {error}")
        latex_extracted = ""

    # -------------------------------------------------
    # Step 2: Gemini LaTeX fix
    # -------------------------------------------------
    if latex_extracted:
        try:
            latex_extracted = _fix_latex_with_gemini(latex_extracted)
        except Exception as error:
            print(f"[MathAgent] LaTeX fix error: {error}")

    # -------------------------------------------------
    # Step 3: SymPy verification
    # -------------------------------------------------
    try:
        student_expr_str = _clean_latex(latex_extracted)
        model_expr_str = _clean_latex(model_answer_expr)

        locals_map = _sympy_locals()

        student_expr = sp.sympify(student_expr_str, locals=locals_map)
        model_expr = sp.sympify(model_expr_str, locals=locals_map)

        result = sp.simplify(student_expr - model_expr) == 0

        if result:
            return MathResult(
                c_math=1.0,
                symbolic_correct=True,
                latex_extracted=latex_extracted,
            )
        else:
            return MathResult(
                c_math=0.6,
                symbolic_correct=False,
                latex_extracted=latex_extracted,
            )

    except (sp.SympifyError, SyntaxError, TypeError, ValueError) as error:
        print(f"[MathAgent] SymPy parse failed: {error}")
        # c_math=0.4, fall back to LLM method eval
        return MathResult(
            c_math=0.4,
            symbolic_correct=None,
            latex_extracted=latex_extracted,
        )

    except Exception as error:
        print(f"[MathAgent] Unexpected SymPy error: {error}")
        return MathResult(
            c_math=0.4,
            symbolic_correct=None,
            latex_extracted=latex_extracted,
        )
