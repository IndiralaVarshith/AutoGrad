"""
AutoGrade — Intelligent Model Routing Layer

Provides dual local vision-language model integration through Ollama with
intelligent, deterministic routing between:
  1. Fast / routine model:            qwen3.5:4b
  2. Complex visual / document model: gemma4:e4b

Features:
- Thread-safe serialization (single inference concurrency for 16GB unified memory)
- Strict memory management: automatically unloads inactive model before loading
  the other model so both models NEVER coexist in memory simultaneously
- Bounded keep-alive for consecutive requests on the same model
- Preserves full backward-compatibility with Gemini API (switchable via AI_PROVIDER=gemini)
- Detailed routing attribution: records model_used and routing_reason for every call
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
from PIL import Image

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None


# =========================================================
# CONFIGURATION
# =========================================================

class AIConfiguration:
    """Central configuration for local AI and cloud providers."""

    def __init__(self):
        self.provider = os.getenv("AI_PROVIDER", "ollama").strip().lower()
        self.ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        self.simple_model = os.getenv("OLLAMA_SIMPLE_MODEL", "qwen3.5:4b").strip()
        self.complex_model = os.getenv("OLLAMA_COMPLEX_MODEL", "gemma4:e4b").strip()
        self.max_concurrent_requests = int(os.getenv("OLLAMA_MAX_CONCURRENT_REQUESTS", "1"))
        self.gemini_enabled = os.getenv("GEMINI_ENABLED", "false").strip().lower() == "true"
        self.gemini_fallback_enabled = os.getenv("GEMINI_FALLBACK_ENABLED", "false").strip().lower() == "true"
        self.context_window = int(os.getenv("OLLAMA_CONTEXT_WINDOW", "8192"))
        self.request_timeout = int(os.getenv("OLLAMA_REQUEST_TIMEOUT", "240"))
        self.keep_alive = os.getenv("OLLAMA_KEEP_ALIVE", "5m")

    def is_local(self) -> bool:
        return self.provider == "ollama"

    def is_gemini(self) -> bool:
        return self.provider == "gemini"


ai_config = AIConfiguration()


# =========================================================
# DATA CLASSES
# =========================================================

@dataclass
class RouterResponse:
    """Standardized response from the model router."""
    text: str
    parsed_json: dict | list | None = None
    model_used: str = ""
    routing_reason: str = ""
    duration_seconds: float = 0.0
    tokens_evaluated: int = 0


# =========================================================
# OLLAMA CLIENT
# =========================================================

class OllamaClient:
    """
    Robust local HTTP client for Ollama API (/api/chat).
    
    Enforces concurrency lock and strict single-model memory residency.
    """

    def __init__(self, config: AIConfiguration | None = None):
        self.config = config or ai_config
        self._lock = threading.Semaphore(self.config.max_concurrent_requests)
        self._session = requests.Session()
        self._active_model: str | None = None

    def check_health(self) -> dict[str, Any]:
        """Verify Ollama is reachable and return installed models."""
        url = f"{self.config.ollama_base_url}/api/tags"
        try:
            res = self._session.get(url, timeout=5)
            if res.status_code == 200:
                data = res.json()
                models = [m.get("name") for m in data.get("models", [])]
                return {"online": True, "models": models}
            return {"online": False, "error": f"HTTP {res.status_code}"}
        except Exception as e:
            return {"online": False, "error": str(e)}

    def unload_model(self, model_name: str) -> bool:
        """Explicitly unload a model from unified memory using keep_alive: 0."""
        if not model_name:
            return True
        url = f"{self.config.ollama_base_url}/api/chat"
        try:
            payload = {
                "model": model_name,
                "messages": [],
                "keep_alive": 0,
            }
            res = self._session.post(url, json=payload, timeout=10)
            if res.status_code == 200:
                print(f"[OllamaClient] Unloaded '{model_name}' from VRAM.")
                if self._active_model == model_name:
                    self._active_model = None
                return True
        except Exception as e:
            print(f"[OllamaClient] Failed to unload '{model_name}': {e}")
        return False

    def prepare_image_payload(self, image_input: Any) -> str:
        """
        Convert file path, bytes, PIL Image, or PyMuPDF Pixmap to a base64 JPEG string.
        Downscales images if max dimension exceeds 1440px to bound memory and token load.
        """
        if isinstance(image_input, str):
            # Check if it's already a base64 string
            if image_input.startswith("data:image") or len(image_input) > 256 and not os.path.exists(image_input):
                if "," in image_input:
                    return image_input.split(",", 1)[1]
                return image_input

            # It's a file path
            path = Path(image_input)
            if not path.exists():
                raise FileNotFoundError(f"Image file not found: {image_input}")
            img_bytes = path.read_bytes()
        elif isinstance(image_input, bytes):
            img_bytes = image_input
        elif isinstance(image_input, Image.Image):
            buf = io.BytesIO()
            image_input.save(buf, format="JPEG", quality=85)
            img_bytes = buf.getvalue()
        elif hasattr(image_input, "tobytes"):  # PyMuPDF Pixmap
            img_bytes = image_input.tobytes("jpeg")
        else:
            raise ValueError(f"Unsupported image input type: {type(image_input)}")

        # Validate and scale image with PIL
        try:
            pil_img = Image.open(io.BytesIO(img_bytes))
            if pil_img.mode != "RGB":
                pil_img = pil_img.convert("RGB")

            # Downscale if larger than 1440px
            max_dim = max(pil_img.size)
            if max_dim > 1440:
                ratio = 1440.0 / max_dim
                new_size = (int(pil_img.size[0] * ratio), int(pil_img.size[1] * ratio))
                pil_img = pil_img.resize(new_size, Image.Resampling.LANCZOS)

            buf = io.BytesIO()
            pil_img.save(buf, format="JPEG", quality=85)
            return base64.b64encode(buf.getvalue()).decode("utf-8")
        except Exception as e:
            # Fallback to direct raw base64
            print(f"[OllamaClient] Image resize warning: {e}")
            return base64.b64encode(img_bytes).decode("utf-8")

    def render_pdf_to_images(
        self,
        pdf_input: str | bytes,
        max_pages: int | None = 6,
        dpi: int = 120,
    ) -> list[str]:
        """Render pages of a PDF document into base64 JPEG strings."""
        if fitz is None:
            raise RuntimeError("PyMuPDF (fitz) is required to render PDF pages.")

        if isinstance(pdf_input, (str, Path)):
            doc = fitz.open(str(pdf_input))
        else:
            doc = fitz.open(stream=pdf_input, filetype="pdf")

        images: list[str] = []
        page_count = len(doc)
        limit = min(page_count, max_pages) if max_pages else page_count

        for i in range(limit):
            page = doc[i]
            # dpi 120 gives a great balance between handwriting sharpness and token size
            pix = page.get_pixmap(dpi=dpi)
            b64_str = self.prepare_image_payload(pix)
            images.append(b64_str)

        doc.close()
        return images

    def _clean_json_text(self, text: str) -> str:
        """Strip markdown fences, thinking traces, and surrounding non-JSON text."""
        # Strip thinking tags if present
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        text = text.strip()

        # Strip markdown ```json ... ``` blocks
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
            text = text.strip()

        # If wrapped inside some other text, extract outermost {...} or [...]
        first_brace = text.find("{")
        first_bracket = text.find("[")
        last_brace = text.rfind("}")
        last_bracket = text.rfind("]")

        if first_brace != -1 and (first_bracket == -1 or first_brace < first_bracket):
            if last_brace != -1 and last_brace > first_brace:
                return text[first_brace : last_brace + 1]
        elif first_bracket != -1:
            if last_bracket != -1 and last_bracket > first_bracket:
                return text[first_bracket : last_bracket + 1]

        return text

    def chat(
        self,
        model: str,
        prompt: str,
        images: list[Any] | None = None,
        system_instruction: str | None = None,
        format_json: bool = True,
        temperature: float = 0.1,
        max_tokens: int | None = None,
        schema: dict | None = None,
        timeout: int | None = None,
    ) -> tuple[str, dict | list | None, int]:
        """
        Execute inference via Ollama /api/chat.

        Acquires concurrency lock, ensures single-model memory residency,
        unloads prior model on switch, and parses structured output.
        """
        timeout = timeout or self.config.request_timeout

        with self._lock:
            # Memory Management: if active model is different, unload it first!
            if self._active_model and self._active_model != model:
                print(
                    f"[OllamaClient] Model switch detected: "
                    f"unloading '{self._active_model}' before loading '{model}'..."
                )
                self.unload_model(self._active_model)

            self._active_model = model

            # Process images
            b64_images: list[str] = []
            if images:
                for img in images:
                    if img is None:
                        continue
                    b64_images.append(self.prepare_image_payload(img))

            # Assemble messages
            messages: list[dict[str, Any]] = []
            if system_instruction:
                messages.append({"role": "system", "content": system_instruction})

            user_msg: dict[str, Any] = {"role": "user", "content": prompt}
            if b64_images:
                user_msg["images"] = b64_images
            messages.append(user_msg)

            # Build payload
            payload: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "stream": False,
                "think": False,
                "keep_alive": self.config.keep_alive,
                "options": {
                    "temperature": temperature,
                    "num_ctx": self.config.context_window,
                },
            }

            if max_tokens:
                payload["options"]["num_predict"] = max_tokens

            if format_json:
                payload["format"] = "json"

            url = f"{self.config.ollama_base_url}/api/chat"

            # Execute with retry on transient network/local glitches
            max_retries = 2
            last_error: Exception | None = None

            for attempt in range(max_retries + 1):
                try:
                    res = self._session.post(url, json=payload, timeout=timeout)
                    if res.status_code == 200:
                        data = res.json()
                        raw_content = data.get("message", {}).get("content", "").strip()
                        if not raw_content and data.get("message", {}).get("thinking"):
                            raw_content = data["message"]["thinking"].strip()
                        eval_count = data.get("eval_count", 0)

                        if not raw_content and not data.get("done"):
                            raise RuntimeError(f"Ollama returned empty response for model {model}")

                        parsed_json = None
                        if format_json and raw_content:
                            cleaned = self._clean_json_text(raw_content)
                            try:
                                parsed_json = json.loads(cleaned)
                            except json.JSONDecodeError as je:
                                print(f"[OllamaClient] JSON decode warning: {je}. Raw content: {raw_content[:200]}")
                                # Retry extraction with relaxed pattern
                                match = re.search(r"(\{.*\}|\[.*\])", raw_content, re.DOTALL)
                                if match:
                                    try:
                                        parsed_json = json.loads(match.group(1))
                                    except Exception:
                                        pass

                        return raw_content, parsed_json, eval_count

                    elif res.status_code == 404:
                        raise RuntimeError(
                            f"Model '{model}' not found in Ollama. "
                            f"Ensure model is pulled with 'ollama pull {model}'."
                        )
                    else:
                        raise RuntimeError(f"Ollama API returned HTTP {res.status_code}: {res.text}")

                except (requests.Timeout, requests.ConnectionError) as e:
                    last_error = e
                    print(f"[OllamaClient] Attempt {attempt + 1}/{max_retries + 1} failed: {e}")
                    if attempt < max_retries:
                        time.sleep(2.0 * (attempt + 1))
                    else:
                        raise RuntimeError(
                            f"Ollama connection/timeout error for model '{model}' after {max_retries + 1} attempts: {e}"
                        ) from e
                except Exception as e:
                    raise e

            if last_error:
                raise last_error
            raise RuntimeError(f"Ollama request failed unexpectedly for model {model}")


# =========================================================
# ROUTING POLICY
# =========================================================

class RoutingPolicy:
    """
    Intelligent routing policy:
    Selects between Qwen3.5 4B (routine/fast) and Gemma 4 E4B (complex/visual)
    based on task type, input modality, question characteristics, and uncertainty.
    """

    def __init__(self, config: AIConfiguration | None = None):
        self.config = config or ai_config

    def select_model(
        self,
        task_type: str,
        has_images: bool = False,
        question_type: str = "THEORY",
        illegible_count: int = 0,
        is_disputed: bool = False,
        is_rubric_from_scanned_paper: bool = False,
        force_model: str | None = None,
    ) -> tuple[str, str]:
        """
        Determine the appropriate local model and human-readable routing reason.

        Returns: (model_name, routing_reason)
        """
        if force_model:
            return force_model, f"Explicitly forced model override: {force_model}"

        q_type_upper = (question_type or "THEORY").upper()

        # 1. OCR Extraction from handwritten student answer sheets (Image-based)
        if task_type == "ocr_extraction":
            if has_images:
                return (
                    self.config.complex_model,
                    "Handwritten answer-sheet visual transcription and illegible-region detection",
                )
            return (
                self.config.simple_model,
                "Text-based OCR transcript cleaning and normalization",
            )

        # 2. Student Metadata Extraction (Hall Ticket / Name)
        if task_type == "student_meta":
            return (
                self.config.simple_model,
                "Fast tabular student metadata (hall ticket & name) extraction",
            )

        # 3. Rubric Generation from Question Paper
        if task_type == "rubric_generation":
            if is_rubric_from_scanned_paper or has_images:
                return (
                    self.config.complex_model,
                    "Visual question paper interpretation and comprehensive criteria breakdown",
                )
            return (
                self.config.simple_model,
                "Routine text-based rubric generation and criteria structuring",
            )

        # 4. Question Classification (THEORY, NUMERICAL, DIAGRAM)
        if task_type == "classification":
            return (
                self.config.simple_model,
                "Routine question classification (THEORY, NUMERICAL, DIAGRAM)",
            )

        # 5. Math notation syntax & LaTeX fix
        if task_type == "math_syntax":
            return (
                self.config.simple_model,
                "Symbolic LaTeX syntax cleaning and normalization",
            )

        # 6. Grading Agent
        if task_type == "grading":
            # Complex visual, diagram, numerical, or ambiguous cases -> Gemma
            if q_type_upper in {"DIAGRAM", "MIXED"} or has_images:
                return (
                    self.config.complex_model,
                    f"Visual assessment for {q_type_upper} question with diagram/handwriting verification",
                )
            if q_type_upper == "NUMERICAL":
                return (
                    self.config.complex_model,
                    "Multi-step mathematical reasoning and numerical derivation evaluation",
                )
            if illegible_count > 0:
                return (
                    self.config.complex_model,
                    f"Ambiguous answer evaluation ({illegible_count} illegible regions detected)",
                )

            # Straightforward theory answer with clean OCR -> Qwen
            return (
                self.config.simple_model,
                "Routine text theory grading against approved rubric criteria",
            )

        # 7. Critic Agent
        if task_type == "critique":
            # Cross-model verification: for disputes, diagrams, or numericals, use Gemma
            if is_disputed or q_type_upper in {"DIAGRAM", "MIXED", "NUMERICAL"} or has_images:
                return (
                    self.config.complex_model,
                    f"Independent cross-model critique for {q_type_upper} evaluation",
                )
            return (
                self.config.simple_model,
                "Independent rubric compliance and evidence citation verification",
            )

        # 8. Resolver
        if task_type == "resolution":
            return (
                self.config.complex_model,
                "High-capacity arbitration and resolution of Grader/Critic disagreement",
            )

        # 9. Fallback default
        if has_images:
            return (
                self.config.complex_model,
                "Visual modality requires high-capacity visual model",
            )
        return (
            self.config.simple_model,
            "Standard text processing task",
        )


# =========================================================
# GEMINI PROVIDER (PRESERVED CLOUD INTEGRATION)
# =========================================================

class GeminiProvider:
    """
    Preserved Gemini Cloud integration.
    Only initialized when AI_PROVIDER=gemini or fallback is explicitly triggered.
    """

    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        api_key = os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is missing from environment. "
                "Cannot use GeminiProvider."
            )
        from google import genai
        self._client = genai.Client(api_key=api_key)
        return self._client

    def generate(
        self,
        prompt: str,
        images: list[Any] | None = None,
        system_instruction: str | None = None,
        format_json: bool = True,
        temperature: float = 0.1,
    ) -> tuple[str, dict | list | None]:
        client = self._get_client()
        model_name = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

        contents: list[Any] = [prompt]
        if images:
            for img in images:
                if isinstance(img, (str, Path)) and os.path.exists(str(img)):
                    uploaded = client.files.upload(file=str(img))
                    contents.append(uploaded)
                elif hasattr(img, "name"):  # Gemini uploaded File ref
                    contents.append(img)

        config_dict: dict[str, Any] = {"temperature": temperature}
        if system_instruction:
            config_dict["system_instruction"] = system_instruction
        if format_json:
            config_dict["response_mime_type"] = "application/json"

        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=config_dict,
        )

        text = response.text or ""
        parsed = None
        if format_json and text:
            try:
                parsed = json.loads(text)
            except Exception:
                pass

        return text, parsed


# =========================================================
# CENTRAL MODEL ROUTER
# =========================================================

class ModelRouter:
    """
    Central Model Router coordinating local Ollama models and cloud fallback.
    """

    def __init__(self):
        self.config = ai_config
        self.ollama = OllamaClient(self.config)
        self.policy = RoutingPolicy(self.config)
        self.gemini = GeminiProvider()

    def generate(
        self,
        task_type: str,
        prompt: str,
        images: list[Any] | None = None,
        system_instruction: str | None = None,
        format_json: bool = True,
        temperature: float = 0.1,
        question_type: str = "THEORY",
        illegible_count: int = 0,
        is_disputed: bool = False,
        is_rubric_from_scanned_paper: bool = False,
        force_model: str | None = None,
        max_tokens: int | None = None,
        timeout: int | None = None,
    ) -> RouterResponse:
        """
        Unified routing entry point.

        Selects the optimal model, runs inference, handles errors and memory,
        and records the exact model_used and routing_reason.
        """
        start_time = time.time()
        has_images = bool(images and len(images) > 0)

        # -----------------------------------------------------
        # Route 1: Local Ollama (Default)
        # -----------------------------------------------------
        if self.config.is_local():
            model_name, reason = self.policy.select_model(
                task_type=task_type,
                has_images=has_images,
                question_type=question_type,
                illegible_count=illegible_count,
                is_disputed=is_disputed,
                is_rubric_from_scanned_paper=is_rubric_from_scanned_paper,
                force_model=force_model,
            )

            print(f"[ModelRouter] Task='{task_type}' → Routed to '{model_name}' | Reason: {reason}")

            try:
                text, parsed, eval_count = self.ollama.chat(
                    model=model_name,
                    prompt=prompt,
                    images=images,
                    system_instruction=system_instruction,
                    format_json=format_json,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                )

                duration = time.time() - start_time
                return RouterResponse(
                    text=text,
                    parsed_json=parsed,
                    model_used=model_name,
                    routing_reason=reason,
                    duration_seconds=round(duration, 2),
                    tokens_evaluated=eval_count,
                )

            except Exception as exc:
                print(f"[ModelRouter] Local inference error with {model_name}: {exc}")

                # Optional cloud fallback if explicitly permitted
                if self.config.gemini_fallback_enabled:
                    print("[ModelRouter] Triggering emergency Gemini fallback...")
                    try:
                        g_text, g_parsed = self.gemini.generate(
                            prompt=prompt,
                            images=images,
                            system_instruction=system_instruction,
                            format_json=format_json,
                            temperature=temperature,
                        )
                        duration = time.time() - start_time
                        return RouterResponse(
                            text=g_text,
                            parsed_json=g_parsed,
                            model_used="gemini-fallback",
                            routing_reason=f"Local failure fallback ({exc})",
                            duration_seconds=round(duration, 2),
                        )
                    except Exception as g_exc:
                        print(f"[ModelRouter] Fallback failed: {g_exc}")

                raise exc

        # -----------------------------------------------------
        # Route 2: Gemini Cloud Provider (Switch via AI_PROVIDER=gemini)
        # -----------------------------------------------------
        elif self.config.is_gemini():
            print(f"[ModelRouter] Task='{task_type}' → Routed to Gemini Cloud API")
            text, parsed = self.gemini.generate(
                prompt=prompt,
                images=images,
                system_instruction=system_instruction,
                format_json=format_json,
                temperature=temperature,
            )
            duration = time.time() - start_time
            return RouterResponse(
                text=text,
                parsed_json=parsed,
                model_used=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
                routing_reason="Cloud Gemini provider active (AI_PROVIDER=gemini)",
                duration_seconds=round(duration, 2),
            )

        else:
            raise ValueError(f"Unknown AI_PROVIDER: {self.config.provider}")


# Global singleton router instance
model_router = ModelRouter()
