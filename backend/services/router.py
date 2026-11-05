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

from contextlib import ContextDecorator, contextmanager
from contextvars import ContextVar

import base64
import hashlib
import io
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
from PIL import Image
from services.timing import log_timing
from services.provider_registry import (
    GeminiCredentialError,
    GeminiCredentialManager,
    GeminiRequestFailure,
    GeminiRateLimitError,
    GeminiModelCapability,
    OllamaModelCapability,
    TaskCategory,
    TaskProfile,
    classify_task,
    gemini_credentials,
    _http_status,
    _retry_after_seconds,
)

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

_logger = logging.getLogger(__name__)
_PDF_RENDER_VERSION = "pdf-render-v2"
_PDF_IMAGE_CACHE_DIR = Path(tempfile.gettempdir()) / "autograde_page_cache"
_PDF_CACHE_TTL_SECONDS = int(os.getenv("AUTOGRADE_CACHE_TTL_SECONDS", "86400"))
_PDF_CACHE_LOCK = threading.Lock()


def _csv_env(name: str, default: str) -> list[str]:
    values = str(os.getenv(name, default)).split(",")
    return [value.strip() for value in values if value.strip()]


def _first_int(*values: Any) -> int | None:
    for value in values:
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _ollama_supports_structured_json(version: str | None) -> bool | None:
    """The Ollama API accepts constrained JSON output from v0.5 onward."""
    if not version:
        return None
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        return None
    return tuple(int(part) for part in match.groups()) >= (0, 5, 0)


def _family_from_ollama_name(name: str) -> str:
    """Infer a missing family label from the model's non-version name prefix."""
    root = str(name or "").split(":", 1)[0]
    match = re.match(r"([A-Za-z]+)", root)
    return match.group(1).lower() if match else ""


# =========================================================
# CONFIGURATION
# =========================================================

class AIConfiguration:
    """Central configuration for local AI and cloud providers."""

    def __init__(self):
        self.provider = os.getenv("AI_PROVIDER", "ollama").strip().lower()
        self.local_first = os.getenv("LOCAL_FIRST", "true").strip().lower() == "true"
        self.ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        self.simple_model = os.getenv("OLLAMA_SIMPLE_MODEL", "qwen3.5:4b").strip()
        self.complex_model = os.getenv("OLLAMA_COMPLEX_MODEL", "gemma4:e4b").strip()
        self.max_concurrent_requests = max(1, int(os.getenv("OLLAMA_MAX_CONCURRENT_REQUESTS", "1")))
        self.gemini_enabled = (
            self.provider == "gemini"
            or os.getenv("GEMINI_ENABLED", "false").strip().lower() == "true"
        )
        self.gemini_fallback_enabled = os.getenv("GEMINI_FALLBACK_ENABLED", "false").strip().lower() == "true"
        self.context_window = int(os.getenv("OLLAMA_CONTEXT_WINDOW", "8192"))
        self.enable_thinking = os.getenv("OLLAMA_ENABLE_THINKING", "false").strip().lower() == "true"
        self.request_timeout = max(1, int(os.getenv("OLLAMA_REQUEST_TIMEOUT", "180")))
        self.max_retries = max(0, min(2, int(os.getenv("OLLAMA_MAX_RETRIES", "1"))))
        self.keep_alive = os.getenv("OLLAMA_KEEP_ALIVE", "5m")
        self.discovery_cache_seconds = max(5, int(os.getenv("OLLAMA_DISCOVERY_CACHE_SECONDS", "30")))
        self.model_priorities = {
            TaskCategory.LOW.value: _csv_env("OLLAMA_LOW_COMPLEXITY_MODELS", self.simple_model),
            TaskCategory.MEDIUM.value: _csv_env("OLLAMA_MEDIUM_COMPLEXITY_MODELS", self.simple_model),
            TaskCategory.HIGH.value: _csv_env("OLLAMA_HIGH_COMPLEXITY_MODELS", self.complex_model),
            TaskCategory.CRITICAL.value: _csv_env("OLLAMA_CRITICAL_VALIDATION_MODELS", self.complex_model),
        }
        self.reasoning_models = set(_csv_env("OLLAMA_REASONING_MODELS", ""))
        self.vision_model_overrides = set(_csv_env("OLLAMA_VISION_MODELS", ""))

    def is_local(self) -> bool:
        return self.provider == "ollama"

    def is_gemini(self) -> bool:
        return self.provider == "gemini"


class NoSuitableModelError(RuntimeError):
    """Raised when no configured provider satisfies task capabilities."""

    requires_teacher_validation = True
    teacher_validation_required = True


ai_config = AIConfiguration()


# =========================================================
# DATA CLASSES
# =========================================================

_LOCAL_ONLY: ContextVar[bool] = ContextVar("autograd_local_only", default=False)


@contextmanager
def _local_only_scope():
    token = _LOCAL_ONLY.set(True)
    try:
        yield
    finally:
        _LOCAL_ONLY.reset(token)


class _LocalOnly(ContextDecorator):
    """Inside this scope, generate() never escalates to Gemini by itself.

    Student work may only reach Gemini through generate_gemini_verification,
    which sends crops of flagged answers and is capped per run.
    """

    def __enter__(self):
        self._scope = _local_only_scope()
        self._scope.__enter__()
        return self

    def __exit__(self, *exc):
        return self._scope.__exit__(*exc)


local_only = _LocalOnly


@dataclass
class RouterResponse:
    """Standardized response from the model router."""
    text: str
    parsed_json: dict | list | None = None
    model_used: str = ""
    routing_reason: str = ""
    duration_seconds: float = 0.0
    tokens_evaluated: int = 0
    provider: str = ""
    credential_id: str = ""
    task_id: str = ""
    task_category: str = ""
    retry_count: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: float | None = None
    escalation_reason: str = ""
    teacher_validation_required: bool = False


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
        self._discovery_lock = threading.RLock()
        self._discovery_cache: dict[str, Any] | None = None
        self._discovery_cache_at = 0.0
        self._last_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
        self._last_retry_count = 0

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

    def discover_models(self, *, refresh: bool = False) -> dict[str, Any]:
        """Read installed, detailed, and loaded model metadata without loading models."""
        now = time.monotonic()
        with self._discovery_lock:
            if (
                not refresh
                and self._discovery_cache is not None
                and now - self._discovery_cache_at < self.config.discovery_cache_seconds
            ):
                return self._discovery_cache

            try:
                tags_response = self._session.get(
                    f"{self.config.ollama_base_url}/api/tags", timeout=5
                )
                if tags_response.status_code != 200:
                    result = {
                        "online": False,
                        "server_version": None,
                        "models": [],
                        "error": f"tags_http_{tags_response.status_code}",
                    }
                    self._cache_discovery(result)
                    return result
                tag_data = tags_response.json()
            except Exception as exc:
                result = {
                    "online": False,
                    "server_version": None,
                    "models": [],
                    "error": type(exc).__name__,
                }
                self._cache_discovery(result)
                return result

            try:
                ps_response = self._session.get(
                    f"{self.config.ollama_base_url}/api/ps", timeout=5
                )
                loaded_items = ps_response.json().get("models", []) if ps_response.status_code == 200 else []
            except Exception:
                loaded_items = []
            loaded_by_name = {
                str(item.get("model") or item.get("name") or ""): item
                for item in loaded_items
                if isinstance(item, dict)
            }

            server_version = None
            try:
                version_response = self._session.get(
                    f"{self.config.ollama_base_url}/api/version", timeout=3
                )
                if version_response.status_code == 200:
                    server_version = str(version_response.json().get("version", "")) or None
            except Exception:
                pass
            json_supported = _ollama_supports_structured_json(server_version)

            discovered: list[OllamaModelCapability] = []
            for tag in tag_data.get("models", []):
                if not isinstance(tag, dict):
                    continue
                name = str(tag.get("name") or tag.get("model") or "").strip()
                if not name:
                    continue
                try:
                    show_response = self._session.post(
                        f"{self.config.ollama_base_url}/api/show",
                        json={"model": name},
                        timeout=5,
                    )
                    show_data = show_response.json() if show_response.status_code == 200 else {}
                except Exception:
                    show_data = {}

                detail = tag.get("details") or {}
                show_capabilities = show_data.get("capabilities") or []
                capabilities = tuple(
                    str(value).strip().lower()
                    for value in show_capabilities
                    if str(value).strip()
                )
                if capabilities:
                    image_support: bool | None = "vision" in capabilities or "image" in capabilities
                elif name in self.config.vision_model_overrides:
                    image_support = True
                else:
                    image_support = None
                if name in self.config.reasoning_models:
                    reasoning_support: bool | None = True
                elif any(value in capabilities for value in ("reasoning", "thinking")):
                    reasoning_support = True
                else:
                    reasoning_support = None

                loaded_item = loaded_by_name.get(name) or loaded_by_name.get(name.split(":", 1)[0])
                model_info = show_data.get("model_info") or {}
                context_length = _first_int(
                    loaded_item.get("context_length") if loaded_item else None,
                    model_info.get("context_length"),
                )
                families_value = detail.get("families") or []
                family = str(detail.get("family") or "").strip() or _family_from_ollama_name(name)
                if not families_value and family:
                    families_value = [family]
                discovered.append(OllamaModelCapability(
                    name=name,
                    family=family,
                    families=tuple(str(value) for value in families_value),
                    size_bytes=_first_int(tag.get("size")),
                    parameter_size=str(detail.get("parameter_size") or ""),
                    quantization_level=str(detail.get("quantization_level") or ""),
                    supports_images=image_support,
                    supports_structured_json=json_supported,
                    reasoning_suitable=reasoning_support,
                    available=True,
                    loaded=loaded_item is not None,
                    size_vram_bytes=_first_int(loaded_item.get("size_vram")) if loaded_item else None,
                    context_length=context_length,
                    capabilities=capabilities,
                ))

            result = {
                "online": True,
                "server_version": server_version,
                "models": discovered,
                "error": "",
            }
            self._cache_discovery(result)
            return result

    def _cache_discovery(self, result: dict[str, Any]) -> None:
        self._discovery_cache = result
        self._discovery_cache_at = time.monotonic()

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
        max_pages: int | None = None,
        dpi: int = 120,
        *,
        source_hash: str = "",
        job_id: str = "",
        on_stage=None,
    ) -> list[str]:
        """Render every page at a bounded resolution and cache reusable page images."""
        if fitz is None:
            raise RuntimeError("PyMuPDF (fitz) is required to render PDF pages.")

        pdf_bytes = (
            Path(pdf_input).read_bytes()
            if isinstance(pdf_input, (str, Path))
            else pdf_input
        )
        if not source_hash:
            source_hash = hashlib.sha256(pdf_bytes).hexdigest()
        cache_identity = json.dumps(
            {
                "source": source_hash,
                "version": _PDF_RENDER_VERSION,
                "dpi": dpi,
                "max_pages": max_pages,
                "pymupdf": getattr(fitz, "VersionBind", ""),
                "pillow": getattr(Image, "__version__", ""),
            },
            sort_keys=True,
        )
        cache_path = _PDF_IMAGE_CACHE_DIR / hashlib.sha256(
            cache_identity.encode()
        ).hexdigest()

        if on_stage is not None:
            on_stage("extracting_pages", "Extracting every PDF page")
        started_at = time.perf_counter()
        with _PDF_CACHE_LOCK:
            cached_pages = self._read_cached_pdf_pages(cache_path)
        if cached_pages is not None:
            _logger.info(
                "evaluation_cache_hit %s",
                json.dumps({"kind": "pdf_pages", "job_id": job_id}, sort_keys=True),
            )
            log_timing(
                "pdf_page_extraction_and_conversion",
                started_at,
                job_id=job_id,
                page_count=len(cached_pages),
                cache_hit=True,
            )
            return cached_pages

        if on_stage is not None:
            on_stage("preprocessing_images", f"Rendering pages at {dpi} DPI")
        if isinstance(pdf_input, (str, Path)):
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        else:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")

        images: list[str] = []
        page_extraction_ms = 0.0
        image_preprocessing_ms = 0.0
        try:
            page_count = len(doc)
            limit = min(page_count, max_pages) if max_pages else page_count
            for i in range(limit):
                extraction_started = time.perf_counter()
                pix = doc[i].get_pixmap(dpi=dpi, alpha=False)
                page_extraction_ms += (time.perf_counter() - extraction_started) * 1000

                preprocessing_started = time.perf_counter()
                images.append(self.prepare_image_payload(pix))
                image_preprocessing_ms += (time.perf_counter() - preprocessing_started) * 1000
        finally:
            doc.close()

        log_timing(
            "pdf_page_extraction",
            started_at,
            job_id=job_id,
            page_count=len(images),
            cache_hit=False,
            page_render_duration_ms=round(page_extraction_ms, 2),
        )
        _logger.info(
            "evaluation_stage_timing %s",
            json.dumps(
                {
                    "event": "evaluation_stage_timing",
                    "stage": "image_preprocessing",
                    "duration_ms": round(image_preprocessing_ms, 2),
                    "job_id": job_id,
                    "page_count": len(images),
                    "dpi": dpi,
                },
                sort_keys=True,
            ),
        )
        with _PDF_CACHE_LOCK:
            self._write_cached_pdf_pages(
                cache_path,
                images,
                page_count=page_count,
                max_pages=max_pages,
            )
        return images

    @staticmethod
    def _read_cached_pdf_pages(cache_path: Path) -> list[str] | None:
        manifest_path = cache_path / "manifest.json"
        if not manifest_path.is_file():
            return None
        try:
            if time.time() - manifest_path.stat().st_mtime > _PDF_CACHE_TTL_SECONDS:
                shutil.rmtree(cache_path, ignore_errors=True)
                return None
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            page_files = manifest.get("pages", [])
            if not page_files or any(not (cache_path / name).is_file() for name in page_files):
                return None
            return [
                base64.b64encode((cache_path / name).read_bytes()).decode("ascii")
                for name in page_files
            ]
        except (OSError, ValueError, TypeError):
            _logger.warning("Ignoring incomplete rendered-page cache %s", cache_path.name)
            return None

    @staticmethod
    def _write_cached_pdf_pages(
        cache_path: Path,
        images: list[str],
        *,
        page_count: int,
        max_pages: int | None,
    ) -> None:
        if not images:
            return
        _PDF_IMAGE_CACHE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary_dir = Path(tempfile.mkdtemp(dir=_PDF_IMAGE_CACHE_DIR, prefix="render-"))
        try:
            page_names = []
            for index, image in enumerate(images):
                name = f"page-{index + 1:04d}.jpg"
                page_path = temporary_dir / name
                page_path.write_bytes(base64.b64decode(image))
                os.chmod(page_path, 0o600)
                page_names.append(name)
            manifest_path = temporary_dir / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "pages": page_names,
                        "page_count": page_count,
                        "rendered_count": len(images),
                        "max_pages": max_pages,
                    }
                ),
                encoding="utf-8",
            )
            os.chmod(manifest_path, 0o600)
            if cache_path.exists():
                shutil.rmtree(cache_path)
            temporary_dir.replace(cache_path)

            cache_entries = [
                entry for entry in _PDF_IMAGE_CACHE_DIR.iterdir()
                if entry.is_dir() and entry.name != cache_path.name
            ]
            cache_entries.sort(key=lambda entry: entry.stat().st_mtime, reverse=True)
            for entry in cache_entries[127:]:
                shutil.rmtree(entry)
        except OSError:
            _logger.exception("Could not persist rendered-page cache")
            if temporary_dir.exists():
                shutil.rmtree(temporary_dir, ignore_errors=True)

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
        think: bool = False,
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
                "think": bool(think),
                "keep_alive": self.config.keep_alive,
                "options": {
                    "temperature": temperature,
                    "num_ctx": self.config.context_window,
                },
            }

            if max_tokens:
                payload["options"]["num_predict"] = max_tokens

            if schema:
                payload["format"] = schema
            elif format_json:
                payload["format"] = "json"

            url = f"{self.config.ollama_base_url}/api/chat"

            # Execute with retry on transient network/local glitches
            max_retries = self.config.max_retries
            last_error: Exception | None = None

            for attempt in range(max_retries + 1):
                try:
                    res = self._session.post(url, json=payload, timeout=timeout)
                    if res.status_code == 200:
                        self._last_retry_count = attempt
                        data = res.json()
                        message = data.get("message", {})
                        raw_content = str(message.get("content", "") or "").strip()
                        eval_count = data.get("eval_count", 0)
                        self._last_usage = {
                            "input_tokens": _first_int(data.get("prompt_eval_count")) or 0,
                            "output_tokens": _first_int(eval_count) or 0,
                        }

                        if not raw_content:
                            # Never promote a model's private reasoning trace to
                            # an answer when the user-facing content is absent.
                            last_error = RuntimeError(
                                f"Ollama returned an empty response for model '{model}'."
                            )
                            if attempt < max_retries:
                                _logger.warning(
                                    "Ollama returned an empty response for model %s; retrying "
                                    "attempt %d/%d",
                                    model,
                                    attempt + 2,
                                    max_retries + 1,
                                )
                                time.sleep(2.0 * (attempt + 1))
                                continue
                            raise RuntimeError(
                                f"Ollama returned an empty response for model '{model}' "
                                f"after {max_retries + 1} attempt(s)."
                            ) from last_error

                        parsed_json = None
                        if format_json and raw_content:
                            cleaned = self._clean_json_text(raw_content)
                            try:
                                parsed_json = json.loads(cleaned)
                            except json.JSONDecodeError:
                                # Retry extraction with relaxed pattern
                                match = re.search(r"(\{.*\}|\[.*\])", raw_content, re.DOTALL)
                                if match:
                                    try:
                                        parsed_json = json.loads(match.group(1))
                                    except Exception:
                                        pass
                            if parsed_json is None:
                                raise RuntimeError("Ollama returned malformed structured output.")

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

    def last_usage(self) -> dict[str, int]:
        return dict(self._last_usage)

    def last_retry_count(self) -> int:
        return self._last_retry_count


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

    def select_capable_model(
        self,
        profile: TaskProfile,
        models: list[OllamaModelCapability],
        *,
        exclude_model: str | None = None,
        exclude_models: set[str] | None = None,
        force_model: str | None = None,
    ) -> tuple[str, str]:
        """Select only an installed model whose discovered capabilities fit."""
        usable = [model for model in models if model.available]
        if profile.requires_vision:
            usable = [model for model in usable if model.supports_images is True]
        if profile.requires_structured_json:
            usable = [model for model in usable if model.supports_structured_json is True]
        if profile.requires_reasoning:
            usable = [model for model in usable if model.reasoning_suitable is True]
        excluded = set(exclude_models or set())
        if exclude_model:
            excluded.add(exclude_model)
        if excluded:
            usable = [model for model in usable if model.name not in excluded]
        if force_model:
            selected = next((model for model in usable if model.name == force_model), None)
            if selected is None:
                raise NoSuitableModelError(
                    f"The forced Ollama model is unavailable or lacks a required capability "
                    f"for {profile.category.value}; teacher validation is required."
                )
            return selected.name, "Explicit model override passed installed capability checks."
        if not usable:
            missing = []
            if profile.requires_vision:
                missing.append("vision")
            if profile.requires_structured_json:
                missing.append("structured JSON")
            if profile.requires_reasoning:
                missing.append("reasoning")
            details = ", ".join(missing) if missing else "completion"
            raise NoSuitableModelError(
                f"No installed Ollama model satisfies required {details} capability; "
                "teacher validation is required."
            )

        preferred = self.config.model_priorities.get(profile.category.value, [])
        rank = {name: index for index, name in enumerate(preferred)}
        if profile.category in {TaskCategory.LOW, TaskCategory.MEDIUM}:
            usable.sort(key=lambda model: (
                rank.get(model.name, len(rank) + 1),
                model.size_bytes if model.size_bytes is not None else 2**63,
                model.name,
            ))
        else:
            usable.sort(key=lambda model: (
                rank.get(model.name, len(rank) + 1),
                -(model.size_bytes or 0),
                model.name,
            ))
        selected = usable[0]
        if profile.independent_verification:
            reason = (
                f"Independent verification selected eligible model {selected.name}; "
                f"excluded the first-pass model {exclude_model or 'when provided'}."
            )
        elif selected.name in rank:
            reason = f"Selected configured priority model for {profile.category.value}."
        else:
            reason = (
                f"Selected installed capability match for {profile.category.value}; "
                "no configured priority candidate was installed."
            )
        if profile.requires_vision:
            reason += " Vision capability was reported by Ollama /api/show."
        if profile.requires_reasoning:
            reason += " Reasoning capability was explicitly configured or reported."
        return selected.name, reason


# =========================================================
# GEMINI PROVIDER (PRESERVED CLOUD INTEGRATION)
# =========================================================

class GeminiProvider:
    """
    Preserved Gemini Cloud integration.
    Only initialized when AI_PROVIDER=gemini or fallback is explicitly triggered.
    """

    def __init__(self, credentials: GeminiCredentialManager | None = None):
        self.credentials = credentials or gemini_credentials
        try:
            concurrency = max(1, min(8, int(os.getenv("GEMINI_MAX_CONCURRENT_REQUESTS", "1"))))
        except (TypeError, ValueError):
            concurrency = 1
        self._semaphore = threading.BoundedSemaphore(concurrency)

    def _get_client(self, credential_id: str | None = None):
        return self.credentials.client_for(credential_id or self.credentials.select_credential())

    def embed_texts(
        self,
        texts: list[str],
        *,
        requested_model: str | None = None,
        output_dimensions: int = 768,
    ) -> tuple[list[list[float]], dict[str, Any]]:
        """Embed text through a key that exposes the embedding API method."""
        credential_id, model = self.credentials.select_embedding_credential_and_model(
            requested_model=requested_model,
        )
        client = self._get_client(credential_id)
        from google.genai import types

        vectors: list[list[float]] = []
        for index, text in enumerate(texts):
            self.credentials.record_request(credential_id)
            try:
                with self._semaphore:
                    response = client.models.embed_content(
                        model=model.name,
                        contents=text,
                        config=types.EmbedContentConfig(output_dimensionality=output_dimensions),
                    )
                embeddings = getattr(response, "embeddings", None) or []
                values = getattr(embeddings[0], "values", None) if embeddings else None
                if not values or len(values) != output_dimensions:
                    raise RuntimeError("Gemini embedding response had an unexpected vector shape.")
                vectors.append([float(value) for value in values])
                self.credentials.record_success(credential_id)
            except Exception as exc:
                retry_after = _retry_after_seconds(exc)
                status_code, _, quota_exhausted = self.credentials.record_failure(
                    credential_id,
                    exc,
                    retry_after_seconds=retry_after,
                )
                message = (
                    "Gemini embedding request was rate or quota limited; no alternate key was tried."
                    if status_code == 429 or quota_exhausted
                    else f"Gemini embedding request failed ({type(exc).__name__}, HTTP {status_code or 'unknown'})."
                )
                raise GeminiRequestFailure(
                    message,
                    credential_id=credential_id,
                    model_name=model.name,
                    retry_count=0,
                    status_code=status_code,
                    rate_limited=status_code == 429 or quota_exhausted,
                    request_count=index + 1,
                ) from None
        return vectors, {
            "provider": "gemini",
            "model": model.name,
            "credential_id": credential_id,
            "request_count": len(texts),
            "input_tokens": None,
            "output_tokens": None,
            "estimated_cost_usd": None,
        }

    def generate(
        self,
        prompt: str,
        images: list[Any] | None = None,
        system_instruction: str | None = None,
        format_json: bool = True,
        temperature: float = 0.1,
        response_json_schema: dict | None = None,
        task_profile: TaskProfile | None = None,
        credential_id: str | None = None,
        requested_model: str | None = None,
        exclude_models: set[str] | None = None,
    ) -> tuple[str, dict | list | None, dict[str, Any]]:
        profile = task_profile or classify_task(
            "standard_interpretation",
            has_images=bool(images),
            requires_structured_json=format_json,
        )
        if credential_id:
            selected_credential = credential_id
            model_capability = self.credentials.select_model(
                selected_credential,
                requires_vision=profile.requires_vision,
                requires_structured_json=profile.requires_structured_json,
                requires_reasoning=profile.requires_reasoning,
                requested_model=requested_model,
                exclude_models=exclude_models,
                task_category=profile.category.value,
            )
        else:
            selected_credential, model_capability = self.credentials.select_credential_and_model(
                requires_vision=profile.requires_vision,
                requires_structured_json=profile.requires_structured_json,
                requires_reasoning=profile.requires_reasoning,
                requested_model=requested_model,
                exclude_models=exclude_models,
                task_category=profile.category.value,
            )
        client = self._get_client(selected_credential)
        contents: list[Any] = [prompt, *_to_gemini_image_parts(images or [])]

        config_dict: dict[str, Any] = {"temperature": temperature}
        if system_instruction:
            config_dict["system_instruction"] = system_instruction
        if format_json:
            config_dict["response_mime_type"] = "application/json"
        if response_json_schema:
            config_dict["response_json_schema"] = response_json_schema

        try:
            from google.genai import types
            max_retries = max(0, min(2, int(os.getenv("GEMINI_MAX_RETRIES", "1"))))
            max_retry_wait = max(0, int(os.getenv("GEMINI_MAX_RETRY_WAIT_SECONDS", "30")))
            for attempt in range(max_retries + 1):
                self.credentials.record_request(selected_credential)
                try:
                    with self._semaphore:
                        response = client.models.generate_content(
                            model=model_capability.name,
                            contents=contents,
                            config=types.GenerateContentConfig(**config_dict),
                        )
                    text = response.text or ""
                    parsed = None
                    if format_json and text:
                        try:
                            parsed = json.loads(text)
                        except json.JSONDecodeError:
                            parsed = None
                    if format_json and (not text or parsed is None):
                        raise RuntimeError("Gemini returned malformed structured output.")
                    usage = getattr(response, "usage_metadata", None)
                    prompt_tokens = _first_int(
                        getattr(usage, "prompt_token_count", None),
                        getattr(usage, "input_token_count", None),
                    )
                    output_tokens = _first_int(
                        getattr(usage, "candidates_token_count", None),
                        getattr(usage, "output_token_count", None),
                    )
                    self.credentials.record_success(selected_credential)
                    return text, parsed, {
                        "provider": "gemini",
                        "model": model_capability.name,
                        "credential_id": selected_credential,
                        "retry_count": attempt,
                        "request_count": attempt + 1,
                        "input_tokens": prompt_tokens,
                        "output_tokens": output_tokens,
                        "estimated_cost_usd": None,
                    }
                except Exception as exc:
                    retry_after = _retry_after_seconds(exc)
                    status_code, retryable, quota_exhausted = self.credentials.record_failure(
                        selected_credential,
                        exc,
                        retry_after_seconds=retry_after,
                    )
                    is_rate_limited = status_code == 429 or quota_exhausted or "rate limit" in str(exc).lower()
                    if quota_exhausted or not retryable or attempt >= max_retries:
                        message = (
                            "Gemini reported a rate or quota limit; no alternate key was tried."
                            if is_rate_limited
                            else f"Gemini request failed ({type(exc).__name__}, HTTP {status_code or 'unknown'})."
                        )
                        raise GeminiRequestFailure(
                            message,
                            credential_id=selected_credential,
                            model_name=model_capability.name,
                            retry_count=attempt,
                            status_code=status_code,
                            rate_limited=is_rate_limited,
                            request_count=attempt + 1,
                        ) from None

                    requested_wait = retry_after if retry_after is not None else min(2 ** attempt, 8)
                    if requested_wait > max_retry_wait:
                        message = (
                            "Gemini retry delay exceeds the configured bound; no alternate key was tried."
                            if is_rate_limited
                            else f"Gemini temporary failure ({type(exc).__name__}); retry delay exceeds the configured bound."
                        )
                        raise GeminiRequestFailure(
                            message,
                            credential_id=selected_credential,
                            model_name=model_capability.name,
                            retry_count=attempt,
                            status_code=status_code,
                            rate_limited=is_rate_limited,
                            request_count=attempt + 1,
                        ) from None
                    time.sleep(max(0.0, requested_wait))
            raise RuntimeError("Gemini request ended without a response.")
        except GeminiRequestFailure:
            raise
        except GeminiCredentialError:
            raise
        except Exception:
            raise


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
        self._usage_lock = threading.Lock()
        self._usage: dict[str, dict[str, int]] = {
            "ollama": {"requests": 0, "successes": 0, "errors": 0, "tokens": 0},
            "gemini": {"requests": 0, "successes": 0, "errors": 0, "tokens": 0},
        }

    def provider_diagnostics(self, *, validate_gemini: bool = False) -> dict[str, Any]:
        """Return model/key capability state without exposing credential values."""
        ollama = self.ollama.discover_models(refresh=True)
        gemini_status = (
            self.gemini.credentials.validate_all_credentials()
            if validate_gemini
            else self.gemini.credentials.configured_status()
        )
        return {
            "ollama": {
                "online": ollama["online"],
                "server_version": ollama["server_version"],
                "error": ollama["error"],
                "models": [_ollama_model_dict(model) for model in ollama["models"]],
            },
            "gemini_credentials": gemini_status,
            "usage": self.usage_summary(),
        }

    def validate_gemini_credentials(self) -> list[dict[str, Any]]:
        """Run independent, non-generation model-list validation for each key."""
        return self.gemini.credentials.validate_all_credentials()

    def generate_gemini_verification(
        self,
        *,
        task_id: str,
        prompt: str,
        images: list[Any] | None = None,
        response_json_schema: dict | None = None,
        temperature: float = 0.0,
    ) -> RouterResponse:
        """Run a single explicit Gemini verification call, separate from local routing.

        This path is reserved for selective independent checks. It never falls
        back across credentials after quota exhaustion, and its output is an
        annotation for the existing Resolver/teacher workflow, not a score
        override.
        """
        if os.getenv("GEMINI_VERIFICATION_ENABLED", "false").strip().lower() != "true":
            raise NoSuitableModelError("Independent Gemini verification is disabled.")
        if not self._gemini_allowed_for_fallback():
            raise NoSuitableModelError("Gemini is not enabled for fallback or verification.")
        profile = TaskProfile(
            category=TaskCategory.CRITICAL,
            requires_vision=bool(images),
            requires_reasoning=True,
            requires_structured_json=True,
            independent_verification=True,
            reason="Explicit independent verification of a selected local grading result.",
        )
        return self._generate_gemini(
            task_id=task_id,
            start_time=time.perf_counter(),
            task_type="independent_grading_verification",
            prompt=prompt,
            images=images,
            system_instruction=(
                "Act as an independent examiner. Inspect supplied evidence yourself; "
                "do not assume the local model's transcription or score is correct. "
                "Return only the requested JSON. Never invent unreadable content."
            ),
            format_json=True,
            response_json_schema=response_json_schema,
            temperature=temperature,
            profile=profile,
            reason="Selective independent Gemini verification; result cannot change marks automatically.",
            requested_model=None,
            exclude_models=None,
        )

    def usage_summary(self) -> dict[str, Any]:
        """Summarize local versus cloud inference without answer content."""
        with self._usage_lock:
            local = dict(self._usage["ollama"])
            gemini = dict(self._usage["gemini"])
        gemini["credential_slots"] = self.gemini.credentials.usage_summary()["credential_slots"]
        return {
            "local": local,
            "external": gemini,
            "estimated_external_cost_usd": None,
        }

    def routing_policy_fingerprint(self) -> str:
        """Stable non-secret policy signature for rubric cache invalidation."""
        payload = {
            "provider": self.config.provider,
            "local_priorities": self.config.model_priorities,
            "vision_models": sorted(self.config.vision_model_overrides),
            "reasoning_models": sorted(self.config.reasoning_models),
            "gemini_enabled": self.config.gemini_enabled,
            "gemini_fallback_enabled": self.config.gemini_fallback_enabled,
            "gemini_model": os.getenv("GEMINI_MODEL", ""),
            "gemini_model_priority": os.getenv("GEMINI_MODEL_PRIORITY", ""),
            "gemini_low_models": os.getenv("GEMINI_LOW_COMPLEXITY_MODELS", ""),
            "gemini_medium_models": os.getenv("GEMINI_MEDIUM_COMPLEXITY_MODELS", ""),
            "gemini_high_models": os.getenv("GEMINI_HIGH_COMPLEXITY_MODELS", ""),
            "gemini_critical_models": os.getenv("GEMINI_CRITICAL_VALIDATION_MODELS", ""),
            "gemini_vision_models": os.getenv("GEMINI_VISION_MODELS", ""),
            "gemini_reasoning_models": os.getenv("GEMINI_REASONING_MODELS", ""),
            "gemini_json_models": os.getenv("GEMINI_STRUCTURED_JSON_MODELS", ""),
            "gemini_embedding_model": os.getenv("GEMINI_EMBEDDING_MODEL", ""),
            "gemini_embedding_dimensions": os.getenv("GEMINI_EMBEDDING_DIMENSIONS", "768"),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def _record_usage(
        self,
        provider: str,
        *,
        success: bool,
        token_count: int = 0,
        request_count: int = 1,
    ) -> None:
        with self._usage_lock:
            target = self._usage[provider]
            target["requests"] += max(1, request_count)
            target["successes" if success else "errors"] += 1
            target["tokens"] += max(0, token_count)

    def embed_texts(
        self,
        texts: list[str],
        *,
        task_id: str | None = None,
        requested_model: str | None = None,
        output_dimensions: int | None = None,
    ) -> list[list[float]] | None:
        """Route embeddings through authorized Gemini when explicitly enabled."""
        if not texts:
            return []
        if not (
            self.config.is_gemini()
            or self.config.gemini_enabled
            or self.config.gemini_fallback_enabled
        ):
            return None
        task_id = task_id or uuid.uuid4().hex
        started_at = time.perf_counter()
        try:
            vectors, metadata = self.gemini.embed_texts(
                texts,
                requested_model=requested_model or os.getenv(
                    "GEMINI_EMBEDDING_MODEL", "gemini-embedding-2"
                ),
                output_dimensions=(
                    output_dimensions
                    or max(128, min(3072, int(os.getenv("GEMINI_EMBEDDING_DIMENSIONS", "768"))))
                ),
            )
        except Exception as exc:
            self._record_usage(
                "gemini",
                success=False,
                request_count=getattr(exc, "request_count", 1),
            )
            log_timing(
                "model_inference",
                started_at,
                task_id=task_id,
                task_type="embedding",
                task_category=TaskCategory.LOW.value,
                provider="gemini",
                model=getattr(exc, "model_name", ""),
                credential_id=getattr(exc, "credential_id", ""),
                success=False,
                error_type=type(exc).__name__,
                http_status=_http_status(exc),
                retry_count=getattr(exc, "retry_count", 0),
                teacher_validation_required=False,
            )
            raise NoSuitableModelError(
                f"Gemini embedding could not complete ({type(exc).__name__})."
            ) from None

        self._record_usage(
            "gemini",
            success=True,
            request_count=metadata["request_count"],
        )
        log_timing(
            "model_inference",
            started_at,
            task_id=task_id,
            task_type="embedding",
            task_category=TaskCategory.LOW.value,
            provider="gemini",
            model=metadata["model"],
            credential_id=metadata["credential_id"],
            success=True,
            retry_count=0,
            estimated_cost_usd=None,
            teacher_validation_required=False,
        )
        return vectors

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
        task_category: str | TaskCategory | None = None,
        requires_vision: bool | None = None,
        requires_reasoning: bool | None = None,
        avoid_model: str | None = None,
        avoid_models: list[str] | set[str] | None = None,
        task_id: str | None = None,
        response_json_schema: dict | None = None,
    ) -> RouterResponse:
        """
        Unified routing entry point.

        Selects the optimal model, runs inference, handles errors and memory,
        and records the exact model_used and routing_reason.
        """
        start_time = time.perf_counter()
        task_id = task_id or uuid.uuid4().hex
        has_images = bool(images)
        profile = classify_task(
            task_type,
            prompt=prompt,
            has_images=has_images,
            question_type=question_type,
            illegible_count=illegible_count,
            is_disputed=is_disputed,
            task_category=task_category,
            requires_vision=requires_vision,
            requires_reasoning=requires_reasoning,
            requires_structured_json=format_json,
        )
        excluded_models = {str(value) for value in (avoid_models or []) if value}
        if avoid_model:
            excluded_models.add(avoid_model)
        _logger.info(
            "model_task_classified %s",
            json.dumps({
                "task_id": task_id,
                "task_type": task_type,
                "task_category": profile.category.value,
                "requires_vision": profile.requires_vision,
                "requires_reasoning": profile.requires_reasoning,
                "requires_structured_json": profile.requires_structured_json,
                "reason": profile.reason,
            }, sort_keys=True),
        )

        # Gemini enablement grants fallback/verification capacity; it does not
        # make Gemini the primary provider. Cloud-first behavior remains an
        # explicit opt-out for legacy deployments via LOCAL_FIRST=false.
        prefer_gemini = self.config.is_gemini() and not self.config.local_first
        gemini_attempted = False
        gemini_fallback_reason = ""
        if prefer_gemini:
            reason = (
                "AI_PROVIDER selects Gemini and LOCAL_FIRST is disabled."
            )
            try:
                return self._generate_gemini(
                    task_id=task_id,
                    start_time=start_time,
                    task_type=task_type,
                    prompt=prompt,
                    images=images,
                    system_instruction=system_instruction,
                    format_json=format_json,
                    response_json_schema=response_json_schema,
                    temperature=temperature,
                    profile=profile,
                    reason=reason,
                    requested_model=force_model,
                    exclude_models=excluded_models,
                )
            except NoSuitableModelError as exc:
                gemini_attempted = True
                local_fallback_allowed = (
                    self.config.is_local() or self.config.gemini_fallback_enabled
                )
                if not local_fallback_allowed:
                    raise
                gemini_fallback_reason = type(exc).__name__
                _logger.warning(
                    "Gemini unavailable for task %s (%s); checking only local models "
                    "that satisfy the same capabilities.",
                    task_id,
                    type(exc).__name__,
                )

        registry = self.ollama.discover_models()
        if not registry["online"]:
            local_error: Exception = NoSuitableModelError(
                "Ollama is unavailable; no local model can serve this task. Teacher validation is required."
            )
            if self._gemini_auto_fallback_allowed() and not gemini_attempted:
                return self._generate_gemini(
                    task_id=task_id,
                    start_time=start_time,
                    task_type=task_type,
                    prompt=prompt,
                    images=images,
                    system_instruction=system_instruction,
                    format_json=format_json,
                    response_json_schema=response_json_schema,
                    temperature=temperature,
                    profile=profile,
                    reason="Ollama unavailable; explicit Gemini fallback is enabled.",
                    requested_model=None,
                    exclude_models=excluded_models,
                )
            self._record_usage("ollama", success=False)
            log_timing(
                "model_inference",
                start_time,
                task_id=task_id,
                task_type=task_type,
                task_category=profile.category.value,
                provider="ollama",
                success=False,
                error_type="ProviderUnavailable",
                teacher_validation_required=True,
            )
            raise local_error

        try:
            model_name, reason = self.policy.select_capable_model(
                profile,
                registry["models"],
                exclude_model=avoid_model,
                exclude_models=excluded_models,
                force_model=force_model,
            )
            if gemini_fallback_reason:
                reason += (
                    f" Gemini escalation was unavailable ({gemini_fallback_reason}); "
                    "selected a local model only after capability checks."
                )
        except NoSuitableModelError:
            if self._gemini_auto_fallback_allowed() and not gemini_attempted:
                return self._generate_gemini(
                    task_id=task_id,
                    start_time=start_time,
                    task_type=task_type,
                    prompt=prompt,
                    images=images,
                    system_instruction=system_instruction,
                    format_json=format_json,
                    response_json_schema=response_json_schema,
                    temperature=temperature,
                    profile=profile,
                    reason="No installed Ollama model satisfies required capabilities; explicit Gemini escalation is enabled.",
                    requested_model=None,
                    exclude_models=excluded_models,
                )
            self._record_usage("ollama", success=False)
            log_timing(
                "model_inference",
                start_time,
                task_id=task_id,
                task_type=task_type,
                task_category=profile.category.value,
                provider="ollama",
                success=False,
                error_type="CapabilityUnavailable",
                teacher_validation_required=True,
            )
            raise

        try:
            _unload_other_loaded_models(self.ollama, registry["models"], model_name)
            text, parsed, output_tokens = self.ollama.chat(
                model=model_name,
                prompt=prompt,
                images=images,
                system_instruction=system_instruction,
                format_json=format_json,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                schema=response_json_schema,
                think=profile.requires_reasoning and self.config.enable_thinking and "thinking" in next(
                    (model.capabilities for model in registry["models"] if model.name == model_name),
                    (),
                ),
            )
            usage = self.ollama.last_usage()
            retry_count = self.ollama.last_retry_count()
            self._record_usage("ollama", success=True, token_count=(usage.get("input_tokens", 0) + output_tokens))
            duration = time.perf_counter() - start_time
            log_timing(
                "model_inference",
                start_time,
                task_id=task_id,
                task_type=task_type,
                task_category=profile.category.value,
                provider="ollama",
                model=model_name,
                success=True,
                retry_count=retry_count,
                input_tokens=usage.get("input_tokens"),
                output_tokens=output_tokens,
                estimated_cost_usd=0.0,
                escalation_reason="",
                teacher_validation_required=profile.category == TaskCategory.CRITICAL,
            )
            return RouterResponse(
                text=text,
                parsed_json=parsed,
                model_used=model_name,
                routing_reason=reason,
                duration_seconds=round(duration, 2),
                tokens_evaluated=output_tokens,
                provider="ollama",
                task_id=task_id,
                task_category=profile.category.value,
                retry_count=retry_count,
                input_tokens=usage.get("input_tokens"),
                output_tokens=output_tokens,
                estimated_cost_usd=0.0,
                teacher_validation_required=profile.category == TaskCategory.CRITICAL,
            )
        except Exception as exc:
            self._record_usage("ollama", success=False)
            log_timing(
                "model_inference",
                start_time,
                task_id=task_id,
                task_type=task_type,
                task_category=profile.category.value,
                provider="ollama",
                model=model_name,
                success=False,
                error_type=type(exc).__name__,
                teacher_validation_required=True,
            )
            failure = exc

            # Try at most one different installed local model, and only when
            # it satisfies the same vision/JSON/reasoning requirements.
            try:
                fallback_model, fallback_reason = self.policy.select_capable_model(
                    profile,
                    registry["models"],
                    exclude_models=excluded_models | {model_name},
                )
            except NoSuitableModelError:
                fallback_model = ""
                fallback_reason = ""
            if fallback_model:
                try:
                    _unload_other_loaded_models(self.ollama, registry["models"], fallback_model)
                    text, parsed, output_tokens = self.ollama.chat(
                        model=fallback_model,
                        prompt=prompt,
                        images=images,
                        system_instruction=system_instruction,
                        format_json=format_json,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        timeout=timeout,
                        schema=response_json_schema,
                        think=profile.requires_reasoning and self.config.enable_thinking and "thinking" in next(
                            (model.capabilities for model in registry["models"] if model.name == fallback_model),
                            (),
                        ),
                    )
                    usage = self.ollama.last_usage()
                    retry_count = self.ollama.last_retry_count()
                    self._record_usage(
                        "ollama",
                        success=True,
                        token_count=(usage.get("input_tokens", 0) + output_tokens),
                    )
                    reason = (
                        f"Fallback after {type(failure).__name__}; "
                        f"{fallback_reason} Required task capabilities remained satisfied."
                    )
                    duration = time.perf_counter() - start_time
                    log_timing(
                        "model_inference",
                        start_time,
                        task_id=task_id,
                        task_type=task_type,
                        task_category=profile.category.value,
                        provider="ollama",
                        model=fallback_model,
                        success=True,
                        retry_count=retry_count,
                        input_tokens=usage.get("input_tokens"),
                        output_tokens=output_tokens,
                        estimated_cost_usd=0.0,
                        escalation_reason=reason,
                        teacher_validation_required=profile.category == TaskCategory.CRITICAL,
                    )
                    return RouterResponse(
                        text=text,
                        parsed_json=parsed,
                        model_used=fallback_model,
                        routing_reason=reason,
                        duration_seconds=round(duration, 2),
                        tokens_evaluated=output_tokens,
                        provider="ollama",
                        task_id=task_id,
                        task_category=profile.category.value,
                        retry_count=retry_count,
                        input_tokens=usage.get("input_tokens"),
                        output_tokens=output_tokens,
                        estimated_cost_usd=0.0,
                        escalation_reason=f"local_fallback:{type(failure).__name__}",
                        teacher_validation_required=profile.category == TaskCategory.CRITICAL,
                    )
                except Exception as fallback_exc:
                    failure = fallback_exc
                    self._record_usage("ollama", success=False)
                    log_timing(
                        "model_inference",
                        start_time,
                        task_id=task_id,
                        task_type=task_type,
                        task_category=profile.category.value,
                        provider="ollama",
                        model=fallback_model,
                        success=False,
                        error_type=type(fallback_exc).__name__,
                        teacher_validation_required=True,
                        escalation_reason="single_capability_matched_local_fallback",
                    )

            if self._gemini_auto_fallback_allowed() and not gemini_attempted:
                return self._generate_gemini(
                    task_id=task_id,
                    start_time=start_time,
                    task_type=task_type,
                    prompt=prompt,
                    images=images,
                    system_instruction=system_instruction,
                    format_json=format_json,
                    response_json_schema=response_json_schema,
                    temperature=temperature,
                    profile=profile,
                    reason=f"Ollama request failed ({type(failure).__name__}); explicit Gemini escalation is enabled.",
                    requested_model=None,
                    exclude_models=excluded_models,
                )
            if isinstance(exc, NoSuitableModelError):
                raise
            raise NoSuitableModelError(
                f"Ollama inference failed for task {task_id} ({type(failure).__name__}); teacher validation is required."
            ) from None

    def _gemini_allowed_for_fallback(self) -> bool:
        return self.config.gemini_enabled or self.config.gemini_fallback_enabled

    def _gemini_auto_fallback_allowed(self) -> bool:
        return self._gemini_allowed_for_fallback() and not _LOCAL_ONLY.get()

    def _generate_gemini(
        self,
        *,
        task_id: str,
        start_time: float,
        task_type: str,
        prompt: str,
        images: list[Any] | None,
        system_instruction: str | None,
        format_json: bool,
        response_json_schema: dict | None,
        temperature: float,
        profile: TaskProfile,
        reason: str,
        requested_model: str | None,
        exclude_models: set[str] | None = None,
    ) -> RouterResponse:
        if not self.config.is_gemini() and not self._gemini_allowed_for_fallback():
            raise NoSuitableModelError(
                "Gemini escalation is disabled; teacher validation is required."
            )
        try:
            text, parsed, metadata = self.gemini.generate(
                prompt=prompt,
                images=images,
                system_instruction=system_instruction,
                format_json=format_json,
                response_json_schema=response_json_schema,
                temperature=temperature,
                task_profile=profile,
                requested_model=requested_model,
                exclude_models=exclude_models,
            )
        except Exception as exc:
            self._record_usage(
                "gemini",
                success=False,
                request_count=getattr(exc, "request_count", 1),
            )
            credential_id = getattr(exc, "credential_id", "")
            model_name = getattr(exc, "model_name", "")
            log_timing(
                "model_inference",
                start_time,
                task_id=task_id,
                task_type=task_type,
                task_category=profile.category.value,
                provider="gemini",
                model=model_name,
                credential_id=credential_id,
                success=False,
                error_type=type(exc).__name__,
                http_status=_http_status(exc),
                retry_count=getattr(exc, "retry_count", 0),
                teacher_validation_required=True,
                escalation_reason=reason,
            )
            failure = NoSuitableModelError(
                f"Gemini inference could not complete ({type(exc).__name__}); teacher validation is required."
            )
            # Preserve only safe attribution for operator diagnostics. Never
            # attach provider exception text or credential material.
            failure.credential_id = getattr(exc, "credential_id", "")
            failure.model_name = getattr(exc, "model_name", "")
            failure.status_code = getattr(exc, "status_code", _http_status(exc))
            failure.retry_count = getattr(exc, "retry_count", 0)
            raise failure from None

        token_total = (metadata.get("input_tokens") or 0) + (metadata.get("output_tokens") or 0)
        self._record_usage(
            "gemini",
            success=True,
            token_count=token_total,
            request_count=metadata.get("request_count", 1),
        )
        duration = time.perf_counter() - start_time
        log_timing(
            "model_inference",
            start_time,
            task_id=task_id,
            task_type=task_type,
            task_category=profile.category.value,
            provider="gemini",
            model=metadata["model"],
            credential_id=metadata["credential_id"],
            success=True,
            retry_count=metadata["retry_count"],
            input_tokens=metadata.get("input_tokens"),
            output_tokens=metadata.get("output_tokens"),
            estimated_cost_usd=metadata.get("estimated_cost_usd"),
            escalation_reason=reason,
            teacher_validation_required=profile.category in {TaskCategory.HIGH, TaskCategory.CRITICAL},
        )
        return RouterResponse(
            text=text,
            parsed_json=parsed,
            model_used=metadata["model"],
            routing_reason=reason,
            duration_seconds=round(duration, 2),
            tokens_evaluated=metadata.get("output_tokens") or 0,
            provider="gemini",
            credential_id=metadata["credential_id"],
            task_id=task_id,
            task_category=profile.category.value,
            retry_count=metadata["retry_count"],
            input_tokens=metadata.get("input_tokens"),
            output_tokens=metadata.get("output_tokens"),
            estimated_cost_usd=metadata.get("estimated_cost_usd"),
            escalation_reason=reason,
            teacher_validation_required=profile.category in {TaskCategory.HIGH, TaskCategory.CRITICAL},
        )


def _ollama_model_dict(model: OllamaModelCapability) -> dict[str, Any]:
    return {
        "name": model.name,
        "family": model.family,
        "families": list(model.families),
        "size_bytes": model.size_bytes,
        "parameter_size": model.parameter_size,
        "quantization_level": model.quantization_level,
        "supports_images": model.supports_images,
        "supports_structured_json": model.supports_structured_json,
        "reasoning_suitable": model.reasoning_suitable,
        "available": model.available,
        "loaded": model.loaded,
        "size_vram_bytes": model.size_vram_bytes,
        "context_length": model.context_length,
        "capabilities": list(model.capabilities),
    }


def _unload_other_loaded_models(
    client: OllamaClient,
    models: list[OllamaModelCapability],
    selected_model: str,
) -> None:
    """Keep Ollama at one loaded model on memory-constrained machines."""
    for model in models:
        if model.loaded and model.name != selected_model:
            if not client.unload_model(model.name):
                raise NoSuitableModelError(
                    "Could not unload a previously loaded Ollama model; refusing to load another model."
                )


def _to_gemini_image_parts(images: list[Any]) -> list[Any]:
    """Convert local image inputs to inline GenAI parts without file uploads."""
    import base64
    import mimetypes

    from google.genai import types

    parts: list[Any] = []
    for image in images:
        if image is None:
            continue
        if hasattr(image, "inline_data") or hasattr(image, "file_data") or hasattr(image, "uri"):
            parts.append(image)
            continue

        mime_type = "image/png"
        image_bytes: bytes
        if isinstance(image, Path):
            mime_type = mimetypes.guess_type(str(image))[0] or mime_type
            image_bytes = image.read_bytes()
        elif isinstance(image, str):
            path = Path(image)
            if path.is_file():
                mime_type = mimetypes.guess_type(str(path))[0] or mime_type
                image_bytes = path.read_bytes()
            else:
                encoded = image
                if image.startswith("data:") and "," in image:
                    header, encoded = image.split(",", 1)
                    mime_type = header[5:].split(";", 1)[0] or mime_type
                image_bytes = base64.b64decode(encoded)
        elif isinstance(image, bytes):
            image_bytes = image
        elif isinstance(image, Image.Image):
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            image_bytes = buffer.getvalue()
        elif hasattr(image, "tobytes"):
            try:
                image_bytes = image.tobytes("png")
            except TypeError:
                image_bytes = image.tobytes()
        else:
            raise ValueError("Unsupported image input for Gemini vision routing.")

        if image_bytes.startswith(b"%PDF-"):
            mime_type = "application/pdf"
        try:
            detected = Image.open(io.BytesIO(image_bytes)).format
            if detected:
                mime_type = f"image/{detected.lower()}"
        except Exception:
            pass
        parts.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))
    return parts


# Global singleton router instance
model_router = ModelRouter()
