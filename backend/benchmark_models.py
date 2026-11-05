"""
AutoGrad — Synthetic Local Model Smoke Suite

Runs four synthetic text prompts against `qwen3.5:4b` and `gemma4:e4b`,
recording response parsing, expected-key presence, latency, token counts, and
model unload behavior. It does not verify semantic correctness of the outputs.

This does not measure handwriting transcription accuracy or grading agreement
against teacher decisions. It makes no production routing recommendation.
Outputs a smoke-test report saved as `benchmark_report.md`.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Add backend directory to path
backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.router import OllamaClient, ai_config


BENCHMARK_TASKS = [
    {
        "id": "T1_classification",
        "name": "Question Type Classification",
        "prompt": "Classify this question: 'Derive the Euler-Lagrange equation for a classical harmonic oscillator and calculate the frequency.' Choose one: THEORY, NUMERICAL, DIAGRAM. Return JSON with key 'question_type'.",
        "expected_key": "question_type",
        "expected_val": "NUMERICAL",
    },
    {
        "id": "T2_json_syntax",
        "name": "Complex Structured JSON Schema",
        "prompt": "Create an evaluation rubric with 2 criteria for an 8-mark question on TCP 3-way handshake. Return JSON format: {\"criteria\": [{\"description\": str, \"max_marks\": float}]}. Total max_marks sum must be 8.0.",
        "expected_key": "criteria",
    },
    {
        "id": "T3_math_notation",
        "name": "Mathematical LaTeX Syntax Fixing",
        "prompt": "Fix LaTeX syntax errors in this equation: \\int_{0}^{\\infty} x^2 e^{-x} dx = 2!. Return JSON format: {\"fixed_latex\": str}.",
        "expected_key": "fixed_latex",
    },
    {
        "id": "T4_theory_grading",
        "name": "Theory Grading & Evidence Citation",
        "prompt": """
QUESTION: Explain the role of virtual memory in modern operating systems. (Max 5 marks)
RUBRIC:
- Memory isolation & security (2.5 marks)
- Address space abstraction via paging (2.5 marks)
STUDENT ANSWER:
Virtual memory allows the operating system to provide each process with its own private contiguous address space using paging. It prevents processes from accessing each other's memory directly, providing memory protection and security.
Evaluate this answer. Return JSON:
{
  "marks_awarded": float,
  "verbatim_quote": str,
  "rationale": str
}
""",
        "expected_key": "marks_awarded",
    },
]


def run_benchmark():
    print("=" * 70)
    print("AutoGrade — Dual Local AI Model Benchmarking")
    print(f"Ollama URL: {ai_config.ollama_base_url}")
    print(f"Models: {ai_config.simple_model} vs {ai_config.complex_model}")
    print("=" * 70)

    client = OllamaClient(ai_config)
    health = client.check_health()
    if not health.get("online"):
        print(f"ERROR: Ollama is not reachable at {ai_config.ollama_base_url}: {health.get('error')}")
        return

    models = [ai_config.simple_model, ai_config.complex_model]
    results: dict[str, dict] = {m: {} for m in models}

    for model in models:
        print(f"\nEvaluating Model: {model}...")
        client.unload_all() if hasattr(client, "unload_all") else None

        for task in BENCHMARK_TASKS:
            t_id = task["id"]
            print(f"  Running task {t_id}: {task['name']}...")
            start_t = time.time()
            try:
                raw_text, parsed_json, eval_count = client.chat(
                    model=model,
                    prompt=task["prompt"],
                    format_json=True,
                    temperature=0.1,
                    timeout=90,
                )
                dur = round(time.time() - start_t, 2)
                has_valid_json = parsed_json is not None
                key_found = has_valid_json and isinstance(parsed_json, dict) and task.get("expected_key") in parsed_json

                results[model][t_id] = {
                    "latency_sec": dur,
                    "valid_json": has_valid_json,
                    "key_found": key_found,
                    "output_preview": (raw_text or "")[:120].replace("\n", " "),
                    "eval_tokens": eval_count,
                    "error": None,
                }
                print(f"    ✓ Done in {dur}s | JSON: {has_valid_json} | Target key: {key_found}")
            except Exception as e:
                dur = round(time.time() - start_t, 2)
                results[model][t_id] = {
                    "latency_sec": dur,
                    "valid_json": False,
                    "key_found": False,
                    "output_preview": "",
                    "eval_tokens": 0,
                    "error": str(e),
                }
                print(f"    ✗ Failed in {dur}s: {e}")

        # Unload after testing this model
        client.unload_model(model)

    # ---------------------------------------------------------
    # Generate Markdown Report
    # ---------------------------------------------------------
    report_lines = [
        "# AutoGrad — Synthetic Local Model Smoke Report",
        f"\n**Date:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Hardware:** Apple Silicon (16 GB unified memory; operator reported)",
        f"**Runtime:** Ollama (Inference on 127.0.0.1:11434)",
        "\n> These synthetic prompts test response shape and task-specific outputs only. They do not measure handwriting OCR accuracy, question mapping accuracy, or grading agreement against teacher decisions.\n",
        "\n## Benchmark Summary Table\n",
        "| Task ID | Task Description | Model | Latency (s) | Valid JSON | Target Key Found | Tokens Evaluated |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    for task in BENCHMARK_TASKS:
        t_id = task["id"]
        t_name = task["name"]
        for model in models:
            res = results[model].get(t_id, {})
            lat = res.get("latency_sec", "N/A")
            v_json = "✅" if res.get("valid_json") else "❌"
            k_found = "✅" if res.get("key_found") else "❌"
            tok = res.get("eval_tokens", 0)
            report_lines.append(f"| {t_id} | {t_name} | `{model}` | {lat}s | {v_json} | {k_found} | {tok} |")

    report_lines.extend([
        "\n## Limits of This Smoke Test\n",
        f"- `{ai_config.simple_model}` and `{ai_config.complex_model}` are tested only on the synthetic text prompts above.",
        "- No answer-sheet image is supplied here, so this script cannot support claims about visual or handwriting quality.",
        "- Use the separate human-reviewed handwriting benchmark for CER/WER; no score is valid until its references are teacher-verified.",
        "\n## Final Configuration Decision",
        "- This smoke test makes no production routing recommendation.",
        "- Confirm image and structured-output requirements using the live Ollama capability registry before routing those tasks.",
    ])

    report_content = "\n".join(report_lines)
    report_path = backend_dir / "benchmark_report.md"
    report_path.write_text(report_content, encoding="utf-8")
    print(f"\n{'='*70}")
    print(f"Benchmark Report generated at: {report_path}")
    print("=" * 70)


if __name__ == "__main__":
    run_benchmark()

