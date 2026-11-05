"""Provider capability and Gemini credential registry.

This module stores provider metadata only. Secret values are held privately,
never returned from the diagnostic methods, and are represented in logs by a
stable slot identifier (for example ``gemini-key-2``).
"""

from __future__ import annotations

import os
import json
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from dataclasses import dataclass, field
from pathlib import Path
from enum import Enum
from typing import Any, Callable, Mapping


class TaskCategory(str, Enum):
    LOW = "low_complexity"
    MEDIUM = "medium_complexity"
    HIGH = "high_complexity"
    CRITICAL = "critical_validation"


@dataclass(frozen=True)
class TaskProfile:
    category: TaskCategory
    requires_vision: bool = False
    requires_reasoning: bool = False
    requires_structured_json: bool = False
    independent_verification: bool = False
    reason: str = ""


def classify_task(
    task_type: str,
    *,
    prompt: str = "",
    has_images: bool = False,
    question_type: str = "THEORY",
    illegible_count: int = 0,
    is_disputed: bool = False,
    task_category: str | TaskCategory | None = None,
    requires_vision: bool | None = None,
    requires_reasoning: bool | None = None,
    requires_structured_json: bool = False,
) -> TaskProfile:
    """Classify by operation and evidence needs; prompt length is irrelevant."""
    kind = (task_type or "").strip().lower().replace("-", "_")
    question_kind = (question_type or "THEORY").strip().upper()
    prompt_lower = (prompt or "").lower()
    independent = (
        kind in {"critique", "resolution", "evidence_verification"}
        or is_disputed
    )
    ambiguity = (
        is_disputed
        or illegible_count > 0
        or any(term in prompt_lower for term in (
            "ambiguous", "unreadable", "conflicting", "uncertain mapping", "uncertain evidence"
        ))
    )

    if task_category is not None:
        try:
            category = task_category if isinstance(task_category, TaskCategory) else TaskCategory(str(task_category))
            reason = "Task category was explicitly supplied by the caller."
        except ValueError:
            category = TaskCategory.MEDIUM
            reason = "Unknown explicit task category; using bounded medium-complexity routing."
    elif kind in {"critical_validation", "resolution", "evidence_verification", "critique"} or ambiguity:
        category = TaskCategory.CRITICAL
        reason = "Evidence is disputed, unreadable, or requires independent validation."
    elif (
        kind in {"math_reasoning", "complex_math", "diagram_interpretation", "difficult_ocr"}
        or question_kind in {"NUMERICAL", "DIAGRAM", "MIXED"}
    ):
        category = TaskCategory.HIGH
        reason = "The task requires mathematical, visual, or multi-step reasoning."
    elif kind in {
        "classification", "student_meta", "normalization", "text_normalization",
        "metadata_extraction", "json_formatting", "answer_segmentation", "simple_text",
        "math_syntax",
    }:
        category = TaskCategory.LOW
        reason = "The task is a bounded extraction, normalization, or classification operation."
    else:
        category = TaskCategory.MEDIUM
        reason = "The task requires standard interpretation or structured response extraction."

    vision_required = bool(has_images) if requires_vision is None else requires_vision
    reasoning_required = (
        category in {TaskCategory.HIGH, TaskCategory.CRITICAL}
        if requires_reasoning is None
        else requires_reasoning
    )
    if question_kind == "DIAGRAM" or kind in {"diagram_interpretation", "difficult_ocr"}:
        vision_required = True
    return TaskProfile(
        category=category,
        requires_vision=vision_required,
        requires_reasoning=reasoning_required,
        requires_structured_json=requires_structured_json,
        independent_verification=independent,
        reason=reason,
    )


@dataclass(frozen=True)
class GeminiModelCapability:
    name: str
    supported_methods: tuple[str, ...] = ()
    supports_images: bool | None = None
    supports_structured_json: bool | None = None
    reasoning_suitable: bool | None = None
    supports_embeddings: bool | None = None
    input_token_limit: int | None = None
    output_token_limit: int | None = None
    available: bool = True


@dataclass(frozen=True)
class OllamaModelCapability:
    name: str
    family: str = ""
    families: tuple[str, ...] = ()
    size_bytes: int | None = None
    parameter_size: str = ""
    quantization_level: str = ""
    supports_images: bool | None = None
    supports_structured_json: bool | None = None
    reasoning_suitable: bool | None = None
    available: bool = True
    loaded: bool = False
    size_vram_bytes: int | None = None
    context_length: int | None = None
    capabilities: tuple[str, ...] = ()


@dataclass
class _CredentialState:
    credential_id: str
    secret: str | None = field(default=None, repr=False)
    authorized: bool = False
    source_env: str = ""
    fingerprint: str = field(default="", repr=False)
    validation_status: str = "not_checked"
    validation_error_type: str = ""
    validated_at: float = 0.0
    models: list[GeminiModelCapability] = field(default_factory=list)
    requests: int = 0
    successes: int = 0
    errors: int = 0
    last_status_code: int | None = None


class GeminiCredentialError(RuntimeError):
    """Safe provider error whose message contains no credential material."""


class GeminiRateLimitError(GeminiCredentialError):
    """A Gemini rate or quota response; no other credential is tried."""


class GeminiRequestFailure(RuntimeError):
    """Sanitized inference failure with safe request attribution."""

    def __init__(
        self,
        message: str,
        *,
        credential_id: str,
        model_name: str,
        retry_count: int,
        status_code: int | None,
        rate_limited: bool = False,
        request_count: int = 1,
    ):
        super().__init__(message)
        self.credential_id = credential_id
        self.model_name = model_name
        self.retry_count = retry_count
        self.status_code = status_code
        self.rate_limited = rate_limited
        self.request_count = max(1, request_count)


class GeminiCredentialManager:
    """Securely manage three explicitly authorized Gemini credential slots.

    Numbered slots require a matching ``GEMINI_KEY_N_OWNER_AUTHORIZED``
    opt-in. The legacy single key remains supported but also requires its
    explicit ``GEMINI_LEGACY_KEY_OWNER_AUTHORIZED`` opt-in. A quota response
    blocks all slots until the provider's retry window expires; it never
    triggers key rotation.
    """

    MAX_DISTINCT_CREDENTIALS = 3

    def __init__(
        self,
        environ: Mapping[str, str] | None = None,
        *,
        client_factory: Callable[[str], Any] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self._env = environ if environ is not None else os.environ
        self._client_factory = client_factory
        self._clock = clock
        self._lock = threading.RLock()
        self._states: dict[str, _CredentialState] = {}
        self._clients: dict[str, tuple[str, Any]] = {}
        self._global_blocked_until = 0.0
        self._global_quota_exhausted = False
        state_file = str(self._env.get("GEMINI_PROVIDER_STATE_FILE", "")).strip()
        self._provider_state_file = Path(state_file).expanduser() if state_file else None
        self._load_provider_limits()
        self._load_states()

    def _load_provider_limits(self) -> None:
        """Restore only provider cooldown state; never persist keys or answer data."""
        if self._provider_state_file is None or not self._provider_state_file.is_file():
            return
        try:
            stored = json.loads(self._provider_state_file.read_text(encoding="utf-8"))
            self._global_quota_exhausted = bool(stored.get("quota_exhausted", False))
            self._global_blocked_until = max(
                0.0, float(stored.get("blocked_until", 0.0) or 0.0)
            )
            if self._global_quota_exhausted:
                self._global_blocked_until = float("inf")
            elif self._global_blocked_until <= self._clock():
                self._global_blocked_until = 0.0
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # A damaged state file must not trigger credential rotation.
            self._global_blocked_until = float("inf")
            self._global_quota_exhausted = True
            _logger.warning("Gemini provider cooldown state is unreadable; blocking cloud use.")

    def _persist_provider_limits(self) -> None:
        if self._provider_state_file is None:
            return
        target = self._provider_state_file
        try:
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=target.parent,
                prefix=f"{target.name}.", suffix=".tmp", delete=False,
            ) as state_file:
                os.chmod(state_file.name, 0o600)
                json.dump({
                    "quota_exhausted": self._global_quota_exhausted,
                    "blocked_until": (
                        0 if self._global_quota_exhausted else self._global_blocked_until
                    ),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }, state_file)
                temporary_path = Path(state_file.name)
            temporary_path.replace(target)
            os.chmod(target, 0o600)
        except OSError:
            # Failing to persist a block should fail closed for this process.
            self._global_quota_exhausted = True
            self._global_blocked_until = float("inf")
            _logger.error("Could not persist Gemini cooldown state; cloud use is blocked.")

    def clear_quota_exhaustion(self, *, provider_reset_confirmed: bool) -> None:
        """Clear a persistent quota block only after an operator confirms reset."""
        if not provider_reset_confirmed:
            raise GeminiCredentialError("Provider quota reset must be explicitly confirmed.")
        with self._lock:
            self._global_quota_exhausted = False
            self._global_blocked_until = 0.0
            self._persist_provider_limits()

    def _load_states(self) -> None:
        for slot in (1, 2, 3):
            key_name = f"GEMINI_API_KEY_{slot}"
            secret = str(self._env.get(key_name, "")).strip()
            self._states[f"gemini-key-{slot}"] = _CredentialState(
                credential_id=f"gemini-key-{slot}",
                secret=secret or None,
                authorized=_as_bool(self._env.get(f"GEMINI_KEY_{slot}_OWNER_AUTHORIZED", "false")),
                source_env=key_name,
            )

        legacy_secret = str(self._env.get("GEMINI_API_KEY", "")).strip()
        legacy_authorized = _as_bool(
            self._env.get("GEMINI_LEGACY_KEY_OWNER_AUTHORIZED", "false")
        )
        self._states["gemini-legacy"] = _CredentialState(
            credential_id="gemini-legacy",
            secret=legacy_secret or None,
            authorized=legacy_authorized,
            source_env="GEMINI_API_KEY",
        )

    def _refresh_secrets_if_changed(self) -> None:
        # Environment changes are only read at manager creation. Callers can
        # replace/recreate the manager after changing credentials; this avoids
        # mid-request key switching or hidden hot reloads.
        return

    def configured_status(self) -> list[dict[str, Any]]:
        """Return safe per-slot status. Secret values are never included."""
        with self._lock:
            self._refresh_secrets_if_changed()
            aliases = self._credential_aliases()
            selected_ids = {state.credential_id for state in self._ordered_eligible_states()}
            return [
                {
                    "credential_id": state.credential_id,
                    "configured": bool(state.secret),
                    "authorized": bool(state.authorized and state.secret),
                    "selected": state.credential_id in selected_ids,
                    "duplicate_of": aliases.get(state.credential_id, ""),
                    "validation_status": (
                        f"duplicate_of:{aliases[state.credential_id]}"
                        if state.credential_id in aliases
                        else state.validation_status if state.secret and state.authorized
                        else "not_configured" if not state.secret
                        else "not_authorized"
                    ),
                    "model_count": len(state.models),
                    "request_count": state.requests,
                    "success_count": state.successes,
                    "error_count": state.errors,
                    "last_status_code": state.last_status_code,
                    "provider_blocked_reason": (
                        "quota_exhausted" if self._global_quota_exhausted
                        else "rate_limit_cooldown" if self._clock() < self._global_blocked_until
                        else ""
                    ),
                }
                for state in self._states.values()
            ]

    def _eligible_states(self) -> list[_CredentialState]:
        return [
            state for state in self._states.values()
            if state.secret and state.authorized and state.validation_status != "invalid"
        ]

    def _priority_ordered_eligible_states(self) -> list[_CredentialState]:
        eligible = self._eligible_states()
        by_id = {state.credential_id: state for state in eligible}
        aliases = {
            "1": "gemini-key-1", "slot1": "gemini-key-1", "key1": "gemini-key-1",
            "2": "gemini-key-2", "slot2": "gemini-key-2", "key2": "gemini-key-2",
            "3": "gemini-key-3", "slot3": "gemini-key-3", "key3": "gemini-key-3",
            "legacy": "gemini-legacy",
        }
        configured = _csv_values(self._env.get("GEMINI_CREDENTIAL_PRIORITY", "1,2,3,legacy"))
        ordered_ids = [
            aliases.get(value.lower(), value)
            for value in configured
        ]
        ordered = [by_id[name] for name in ordered_ids if name in by_id]
        ordered.extend(state for state in eligible if state.credential_id not in set(ordered_ids))
        return ordered

    def _credential_aliases(self) -> dict[str, str]:
        """Map duplicate slots and credentials beyond the authorized cap safely."""
        seen: dict[str, str] = {}
        aliases: dict[str, str] = {}
        unique_count = 0
        for state in self._priority_ordered_eligible_states():
            fingerprint = _fingerprint(state.secret or "")
            canonical = seen.get(fingerprint)
            if canonical:
                aliases[state.credential_id] = canonical
            elif unique_count >= self.MAX_DISTINCT_CREDENTIALS:
                aliases[state.credential_id] = "limit_reached"
            else:
                seen[fingerprint] = state.credential_id
                unique_count += 1
        return aliases

    def _ordered_eligible_states(self) -> list[_CredentialState]:
        """Return unique authorized credentials, capped at the configured total."""
        ordered: list[_CredentialState] = []
        seen: set[str] = set()
        for state in self._priority_ordered_eligible_states():
            fingerprint = _fingerprint(state.secret or "")
            if fingerprint in seen:
                continue
            if len(ordered) >= self.MAX_DISTINCT_CREDENTIALS:
                break
            seen.add(fingerprint)
            ordered.append(state)
        return ordered

    def select_credential(self) -> str:
        """Choose an explicitly authorized slot under the configured policy."""
        with self._lock:
            if self._global_quota_exhausted:
                raise GeminiRateLimitError(
                    "Gemini quota is blocked until an operator confirms provider reset."
                )
            if self._clock() < self._global_blocked_until:
                raise GeminiRateLimitError("Gemini is in a provider-directed cooldown window.")
            eligible = self._ordered_eligible_states()
            if not eligible:
                if any(state.secret for state in self._states.values()):
                    raise GeminiCredentialError("No configured Gemini credential has owner authorization.")
                raise GeminiCredentialError("No Gemini API key is configured.")
            # Credentials use stable slot priority. Round-robin rotation is
            # intentionally unsupported so per-key limits cannot be bypassed.
            return eligible[0].credential_id

    def client_for(self, credential_id: str) -> Any:
        with self._lock:
            state = self._states.get(credential_id)
            if state is None or not state.secret or not state.authorized:
                raise GeminiCredentialError("Gemini credential is missing or not owner-authorized.")
            fingerprint = _fingerprint(state.secret)
            cached = self._clients.get(credential_id)
            if cached and cached[0] == fingerprint:
                return cached[1]
            factory = self._client_factory or _default_gemini_client_factory
            client = factory(state.secret)
            self._clients[credential_id] = (fingerprint, client)
            state.fingerprint = fingerprint
            return client

    def validate_credential(self, credential_id: str, *, force: bool = False) -> dict[str, Any]:
        """Validate one key with the non-generation model-list endpoint."""
        with self._lock:
            state = self._states.get(credential_id)
            if state is None:
                return {"credential_id": credential_id, "status": "unknown_slot", "models": []}
            if not state.secret:
                return {"credential_id": credential_id, "status": "not_configured", "models": []}
            if not state.authorized:
                return {"credential_id": credential_id, "status": "not_authorized", "models": []}
            if self._global_quota_exhausted:
                return {"credential_id": credential_id, "status": "blocked_quota_reset_required", "models": []}
            if self._clock() < self._global_blocked_until:
                return {"credential_id": credential_id, "status": "skipped_provider_cooldown", "models": []}
            alias = self._credential_aliases().get(credential_id)
            if alias:
                status = "over_limit" if alias == "limit_reached" else "duplicate"
                return {
                    "credential_id": credential_id,
                    "status": status,
                    "duplicate_of": alias,
                    "models": [],
                }
            cache_seconds = max(30, _int_env(self._env, "GEMINI_MODEL_LIST_TTL_SECONDS", 300))
            if (
                not force
                and state.validated_at
                and self._clock() - state.validated_at < cache_seconds
                and state.validation_status in {"valid", "valid_no_models", "invalid"}
            ):
                return self._safe_validation_result(state)

        try:
            client = self.client_for(credential_id)
            try:
                model_iter = client.models.list(config={"page_size": 1000})
            except TypeError:
                model_iter = client.models.list()
            models = [_gemini_model_capability(item, self._env) for item in model_iter]
            with self._lock:
                state = self._states[credential_id]
                state.models = models
                state.validation_status = "valid" if models else "valid_no_models"
                state.validation_error_type = ""
                state.validated_at = self._clock()
                state.last_status_code = None
                return self._safe_validation_result(state)
        except Exception as exc:
            status_code = _http_status(exc)
            _, _, quota_exhausted = self.record_failure(
                credential_id,
                exc,
                retry_after_seconds=_retry_after_seconds(exc),
            )
            with self._lock:
                state = self._states[credential_id]
                state.last_status_code = status_code
                state.validation_error_type = type(exc).__name__
                error_message = str(exc).lower()
                invalid_key_message = any(
                    marker in error_message
                    for marker in ("api key not valid", "api_key_invalid", "invalid api key")
                )
                state.validation_status = (
                    "invalid" if (status_code in {401, 403} or invalid_key_message) and not quota_exhausted
                    else "unavailable"
                )
                state.validated_at = self._clock()
                return self._safe_validation_result(state)

    def validate_all_credentials(self) -> list[dict[str, Any]]:
        """Validate each configured, owner-authorized key independently."""
        results = []
        slots = list(self._states)
        for index, slot in enumerate(slots):
            results.append(self.validate_credential(slot, force=True))
            with self._lock:
                cooling_down = self._clock() < self._global_blocked_until
            if cooling_down:
                results.extend(
                    {"credential_id": remaining, "status": "skipped_provider_cooldown", "models": []}
                    for remaining in slots[index + 1:]
                )
                break
        return results

    def select_model(
        self,
        credential_id: str,
        *,
        requires_vision: bool,
        requires_structured_json: bool,
        requires_reasoning: bool,
        requested_model: str | None = None,
        exclude_models: set[str] | None = None,
        task_category: str | None = None,
    ) -> GeminiModelCapability:
        result = self.validate_credential(credential_id)
        if result["status"] != "valid":
            raise GeminiCredentialError("Gemini key validation did not return an available model list.")
        with self._lock:
            state = self._states[credential_id]
            models = list(state.models)
        category_key = (task_category or "").upper()
        category_priority = _csv_values(self._env.get(f"GEMINI_{category_key}_MODELS", "")) if category_key else []
        priority = category_priority or _csv_values(self._env.get("GEMINI_MODEL_PRIORITY", ""))
        strict_category_priority = category_key in {
            "HIGH_COMPLEXITY", "CRITICAL_VALIDATION"
        }
        if requested_model:
            priority = [requested_model, *[name for name in priority if name != requested_model]]
        configured_model = str(self._env.get("GEMINI_MODEL", "")).strip()
        if configured_model and configured_model not in priority and (
            not strict_category_priority or not priority
        ):
            priority.append(configured_model)

        by_name = {model.name: model for model in models}
        ordered = [by_name[name] for name in priority if name in by_name]
        if not strict_category_priority:
            ordered.extend(model for model in models if model.name not in set(priority))
        suitable = [
            model for model in ordered
            if model.available
            and any(method.lower().replace("_", "") == "generatecontent" for method in model.supported_methods)
            and (not requires_vision or model.supports_images is True)
            and (not requires_structured_json or model.supports_structured_json is True)
            and (not requires_reasoning or model.reasoning_suitable is True)
            and model.name not in (exclude_models or set())
        ]
        if not suitable:
            requirements = []
            if requires_vision:
                requirements.append("vision input")
            if requires_structured_json:
                requirements.append("structured JSON output")
            if requires_reasoning:
                requirements.append("reasoning support")
            raise GeminiCredentialError(
                "No validated Gemini model satisfies the configured capabilities: "
                + ", ".join(requirements or ["generateContent"]) + "."
            )
        return suitable[0]

    def select_credential_and_model(
        self,
        *,
        requires_vision: bool,
        requires_structured_json: bool,
        requires_reasoning: bool,
        requested_model: str | None = None,
        exclude_models: set[str] | None = None,
        task_category: str | None = None,
    ) -> tuple[str, GeminiModelCapability]:
        """Choose an authorized key that has a suitable model, without quota rotation."""
        with self._lock:
            if self._global_quota_exhausted:
                raise GeminiRateLimitError(
                    "Gemini quota is blocked until an operator confirms provider reset."
                )
            if self._clock() < self._global_blocked_until:
                raise GeminiRateLimitError("Gemini is in a provider-directed cooldown window.")
            candidates = self._ordered_eligible_states()
        for state in candidates:
            validation = self.validate_credential(state.credential_id)
            with self._lock:
                cooling_down = self._clock() < self._global_blocked_until
            if cooling_down:
                raise GeminiRateLimitError("Gemini is in a provider-directed cooldown window.")
            if validation["status"] != "valid":
                continue
            try:
                capability = self.select_model(
                    state.credential_id,
                    requires_vision=requires_vision,
                    requires_structured_json=requires_structured_json,
                    requires_reasoning=requires_reasoning,
                    requested_model=requested_model,
                    exclude_models=exclude_models,
                    task_category=task_category,
                )
                return state.credential_id, capability
            except GeminiCredentialError:
                continue

        if self._clock() < self._global_blocked_until:
            raise GeminiRateLimitError("Gemini is in a provider-directed cooldown window.")
        if not candidates:
            raise GeminiCredentialError("No configured Gemini credential has owner authorization.")
        raise GeminiCredentialError(
            "No authorized Gemini key has a model satisfying the task's required capabilities."
        )

    def select_embedding_credential_and_model(
        self,
        *,
        requested_model: str | None = None,
    ) -> tuple[str, GeminiModelCapability]:
        """Select an authorized key/model pair with embedContent access."""
        with self._lock:
            if self._global_quota_exhausted:
                raise GeminiRateLimitError(
                    "Gemini quota is blocked until an operator confirms provider reset."
                )
            if self._clock() < self._global_blocked_until:
                raise GeminiRateLimitError("Gemini is in a provider-directed cooldown window.")
            candidates = self._ordered_eligible_states()

        priority = _csv_values(
            requested_model
            or self._env.get("GEMINI_EMBEDDING_MODEL_PRIORITY", "")
            or self._env.get("GEMINI_EMBEDDING_MODEL", "")
        )
        for state in candidates:
            validation = self.validate_credential(state.credential_id)
            with self._lock:
                if self._clock() < self._global_blocked_until:
                    raise GeminiRateLimitError("Gemini is in a provider-directed cooldown window.")
                models = list(self._states[state.credential_id].models)
            if validation["status"] != "valid":
                continue
            by_name = {model.name: model for model in models}
            ordered = [by_name[name] for name in priority if name in by_name]
            ordered.extend(model for model in models if model.name not in set(priority))
            selected = next((
                model for model in ordered
                if model.supports_embeddings is True
                and any(
                    method.lower().replace("_", "") == "embedcontent"
                    for method in model.supported_methods
                )
            ), None)
            if selected is not None:
                return state.credential_id, selected

        if not candidates:
            raise GeminiCredentialError("No configured Gemini credential has owner authorization.")
        raise GeminiCredentialError("No authorized Gemini key has an available embedding model.")

    def record_request(self, credential_id: str) -> None:
        with self._lock:
            state = self._states.get(credential_id)
            if state:
                state.requests += 1

    def record_success(self, credential_id: str, status_code: int | None = None) -> None:
        with self._lock:
            state = self._states.get(credential_id)
            if state:
                state.successes += 1
                state.last_status_code = status_code

    def record_failure(
        self,
        credential_id: str,
        exc: Exception,
        *,
        retry_after_seconds: float | None = None,
    ) -> tuple[int | None, bool, bool]:
        """Record failure and return (status, retryable, quota_exhausted)."""
        status_code = _http_status(exc)
        message = str(exc).lower()
        quota_exhausted = any(word in message for word in (
            "quota exceeded", "quota exhausted", "per day limit", "daily quota", "resource_exhausted"
        ))
        is_rate_limit = (
            status_code == 429
            or quota_exhausted
            or "rate limit" in message
            or "resource_exhausted" in message
        )
        exception_name = type(exc).__name__.lower()
        retryable = (
            status_code in {408, 500, 502, 503, 504}
            or any(token in exception_name for token in ("timeout", "connection", "transport"))
            or (is_rate_limit and not quota_exhausted)
        )
        with self._lock:
            state = self._states.get(credential_id)
            if state:
                state.errors += 1
                state.last_status_code = status_code
                invalid_key_message = any(
                    marker in message
                    for marker in ("api key not valid", "api_key_invalid", "invalid api key")
                )
                if (status_code in {401, 403} or invalid_key_message) and not quota_exhausted:
                    state.validation_status = "invalid"
            if is_rate_limit:
                # Provider throttling is treated globally so another owner's
                # key is never used to evade a limit on this request.
                if quota_exhausted:
                    self._global_quota_exhausted = True
                    self._global_blocked_until = float("inf")
                else:
                    fallback_cooldown = _int_env(self._env, "GEMINI_QUOTA_COOLDOWN_SECONDS", 60)
                    wait_seconds = retry_after_seconds if retry_after_seconds is not None else fallback_cooldown
                    self._global_blocked_until = max(
                        self._global_blocked_until,
                        self._clock() + max(1.0, wait_seconds),
                    )
                self._persist_provider_limits()
        return status_code, retryable, quota_exhausted

    def usage_summary(self) -> dict[str, Any]:
        with self._lock:
            states = [state for state in self._states.values() if state.secret]
            return {
                "provider": "gemini",
                "requests": sum(state.requests for state in states),
                "successes": sum(state.successes for state in states),
                "errors": sum(state.errors for state in states),
                "provider_blocked_reason": (
                    "quota_exhausted" if self._global_quota_exhausted
                    else "rate_limit_cooldown" if self._clock() < self._global_blocked_until
                    else ""
                ),
                "credential_slots": [
                    {
                        "credential_id": state.credential_id,
                        "requests": state.requests,
                        "successes": state.successes,
                        "errors": state.errors,
                        "last_status_code": state.last_status_code,
                    }
                    for state in states
                ],
            }

    def _safe_validation_result(self, state: _CredentialState) -> dict[str, Any]:
        return {
            "credential_id": state.credential_id,
            "status": state.validation_status,
            "model_count": len(state.models),
            "error_type": state.validation_error_type,
            "models": [
                {
                    "name": model.name,
                    "family": _family_from_model_name(model.name),
                    "size": None,
                    "supports_images": model.supports_images,
                    "supports_structured_json": model.supports_structured_json,
                    "reasoning_suitable": model.reasoning_suitable,
                    "supports_embeddings": model.supports_embeddings,
                    "available": model.available,
                    "loaded": None,
                    "supported_methods": list(model.supported_methods),
                    "input_token_limit": model.input_token_limit,
                    "output_token_limit": model.output_token_limit,
                }
                for model in state.models
            ],
        }


def _gemini_model_capability(item: Any, environ: Mapping[str, str]) -> GeminiModelCapability:
    name = str(_value(item, "name", default="")).strip()
    if name.startswith("models/"):
        name = name[len("models/"):]
    methods = _as_string_tuple(_value(
        item,
        "supported_actions",
        "supported_generation_methods",
        "supportedGenerationMethods",
        default=(),
    ))
    method_set = {method.lower().replace("_", "") for method in methods}
    is_generator = "generatecontent" in method_set
    supports_embeddings = "embedcontent" in method_set
    input_modalities = _as_string_tuple(_value(
        item, "input_modalities", "supported_input_modalities", "inputModalities", default=()
    ))
    if input_modalities:
        image_support: bool | None = any(value.lower() in {"image", "images", "video", "pdf"} for value in input_modalities)
    else:
        vision_names = set(_csv_values(environ.get("GEMINI_VISION_MODELS", "")))
        image_support = True if name in vision_names and is_generator else None

    structured_value = _value(item, "supports_structured_json", "supportsStructuredJson", default=None)
    json_names = set(_csv_values(environ.get("GEMINI_STRUCTURED_JSON_MODELS", "")))
    if structured_value is None:
        json_support: bool | None = True if name in json_names and is_generator else None
    else:
        json_support = bool(structured_value)

    reasoning_value = _value(item, "thinking", "supports_reasoning", "supportsThinking", default=None)
    reasoning_names = set(_csv_values(environ.get("GEMINI_REASONING_MODELS", "")))
    if reasoning_value is None:
        reasoning: bool | None = True if name in reasoning_names else None
    else:
        reasoning = bool(reasoning_value)

    return GeminiModelCapability(
        name=name,
        supported_methods=methods,
        supports_images=image_support,
        supports_structured_json=json_support,
        reasoning_suitable=reasoning,
        supports_embeddings=supports_embeddings,
        input_token_limit=_optional_int(_value(item, "input_token_limit", "inputTokenLimit", default=None)),
        output_token_limit=_optional_int(_value(item, "output_token_limit", "outputTokenLimit", default=None)),
        available=is_generator or supports_embeddings,
    )


def _default_gemini_client_factory(api_key: str) -> Any:
    from google import genai
    from google.genai import types

    timeout_ms = max(1_000, _int_env(os.environ, "GEMINI_REQUEST_TIMEOUT_MS", 120_000))
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=timeout_ms,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )


def _http_status(exc: Exception) -> int | None:
    for candidate in (
        getattr(exc, "code", None),
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        try:
            if candidate is not None:
                return int(candidate)
        except (TypeError, ValueError):
            pass
    text = str(exc)
    match = re.search(r"\b(400|401|403|404|408|429|500|502|503|504)\b", text)
    return int(match.group(1)) if match else None


def _retry_after_seconds(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    value = headers.get("Retry-After") or headers.get("retry-after")
    try:
        return max(0.0, float(value)) if value is not None else None
    except (TypeError, ValueError):
        if not value:
            return None
        try:
            retry_at = parsedate_to_datetime(str(value))
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _as_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _int_env(environ: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _csv_values(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        values = [str(item).strip() for item in value]
    else:
        values = [item.strip() for item in str(value or "").split(",")]
    return [item.removeprefix("models/") for item in values if item]


def _as_string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    try:
        return tuple(str(item) for item in value)
    except TypeError:
        return ()


def _value(item: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(item, Mapping) and name in item:
            return item[name]
        if hasattr(item, name):
            return getattr(item, name)
    return default


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _fingerprint(secret: str) -> str:
    # Used only for local client-cache invalidation and never logged or returned.
    import hashlib
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _family_from_model_name(name: str) -> str:
    return name.split(":", 1)[0].split("-", 1)[0]


gemini_credentials = GeminiCredentialManager()
