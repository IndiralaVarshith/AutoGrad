"""SymPy verification for answers with an explicit approved answer expression."""

from __future__ import annotations

import re

import sympy as sp

from models.schemas import MathResult


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
    """Normalize common OCR math delimiters and assignment syntax."""
    if not expression:
        return ""
    expr = str(expression).strip()
    expr = re.sub(r"\\(?:\(|\)|\[|\])", "", expr)
    expr = expr.replace("$", "")
    expr = re.sub(r"\\boxed\{([^{}]*)\}", r"\1", expr)
    expr = re.sub(r"\\(?:text|mathrm)\{([^{}]*)\}", r"\1", expr)
    expr = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"((\1)/(\2))", expr)
    expr = re.sub(r"\\sqrt\{([^{}]+)\}", r"sqrt(\1)", expr)
    expr = expr.replace(r"\cdot", "*").replace(r"\times", "*")
    expr = expr.replace(r"\pi", "pi")
    expr = expr.replace("^", "**")
    expr = expr.replace("×", "*")
    expr = expr.replace("÷", "/")
    expr = re.sub(r"\s+", " ", expr)
    return expr


def expected_expression_from_rubric(rubric: list) -> str | None:
    """Use only an explicitly stated answer expression; never infer a key."""
    explicit_answer = re.compile(
        r"(?:expected\s+answer|correct\s+answer|answer|result)\s*[:=]\s*([^;\n]+)",
        re.IGNORECASE,
    )
    for criterion in rubric:
        description = getattr(criterion, "description", "")
        match = explicit_answer.search(description)
        if match:
            expression = _clean_latex(match.group(1).strip())
            if expression:
                return expression
    return None


# =========================================================
# MAIN: VERIFY MATH ANSWER
# =========================================================

def verify_math(
    student_expression: str,
    model_answer_expr: str,
) -> MathResult:
    """
    Compare OCR text with an explicit approved answer using SymPy.

    Parameters
    ----------
    student_expression : str
        OCR text for the student's mapped answer.
    model_answer_expr : str
        An answer expression explicitly included in the approved rubric.

    Returns
    -------
    MathResult
        Contains c_math, symbolic_correct, latex_extracted.
    """

    latex_extracted = _clean_latex(student_expression)
    expected = _clean_latex(model_answer_expr)
    if not latex_extracted or not expected:
        return MathResult(
            c_math=0.0,
            symbolic_correct=None,
            latex_extracted=latex_extracted,
        )

    # -------------------------------------------------
    # Step 3: SymPy verification
    # -------------------------------------------------
    try:
        locals_map = _sympy_locals()
        student_expr = sp.sympify(_final_expression(latex_extracted), locals=locals_map)
        model_expr = sp.sympify(_final_expression(expected), locals=locals_map)

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


def _final_expression(text: str) -> str:
    """Prefer the right-hand side of the final explicit equality."""
    expression_lines = [
        line.strip()
        for line in text.splitlines()
        if any(operator in line for operator in ("=", "+", "-", "*", "/", "^"))
    ]
    candidate = expression_lines[-1] if expression_lines else text
    if "=" in candidate:
        candidate = candidate.rsplit("=", 1)[-1]
    return candidate.strip().rstrip(".")
