from __future__ import annotations

import sys
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.provider_registry import (
    GeminiCredentialError,
    GeminiCredentialManager,
    GeminiRateLimitError,
    GeminiRequestFailure,
    OllamaModelCapability,
    TaskCategory,
    classify_task,
)
from services.router import (
    AIConfiguration,
    GeminiProvider,
    ModelRouter,
    NoSuitableModelError,
    OllamaClient,
    RoutingPolicy,
    _family_from_ollama_name,
)


def _model(name: str, methods: list[str], **features):
    return {
        "name": f"models/{name}",
        "supported_actions": methods,
        "input_token_limit": 1000,
        "output_token_limit": 500,
        **features,
    }


def test_task_classification_uses_operation_and_evidence_not_prompt_length():
    short = classify_task("classification", prompt="x")
    long = classify_task("classification", prompt="x " * 5000)
    visual = classify_task("grading", question_type="DIAGRAM", has_images=True)
    disputed = classify_task("grading", is_disputed=True)

    assert short.category is long.category is TaskCategory.LOW
    assert visual.category is TaskCategory.HIGH
    assert visual.requires_vision and visual.requires_reasoning
    assert disputed.category is TaskCategory.CRITICAL
    assert disputed.independent_verification


def test_gemini_model_features_are_unknown_until_metadata_or_allowlist_confirms_them():
    metadata = _model("gemini-fast", ["generateContent"])
    unknown = GeminiCredentialManager(
        environ={}, client_factory=lambda _: None
    )
    from services.provider_registry import _gemini_model_capability

    capability = _gemini_model_capability(metadata, {})
    assert capability.available
    assert capability.supports_images is None
    assert capability.supports_structured_json is None
    assert capability.reasoning_suitable is None

    verified = _gemini_model_capability(
        metadata,
        {
            "GEMINI_VISION_MODELS": "gemini-fast",
            "GEMINI_STRUCTURED_JSON_MODELS": "gemini-fast",
            "GEMINI_REASONING_MODELS": "gemini-fast",
        },
    )
    assert verified.supports_images is True
    assert verified.supports_structured_json is True
    assert verified.reasoning_suitable is True
    assert unknown.configured_status()[0]["validation_status"] == "not_configured"


class _FakeModels:
    def __init__(self, generation=None):
        self.generation = generation
        self.list_calls = 0
        self.generate_calls = 0

    def list(self, **_kwargs):
        self.list_calls += 1
        return [_model("gemini-fast", ["generateContent"])]

    def generate_content(self, **_kwargs):
        self.generate_calls += 1
        if isinstance(self.generation, Exception):
            raise self.generation
        return SimpleNamespace(
            text='{"ok": true}',
            usage_metadata=SimpleNamespace(prompt_token_count=5, candidates_token_count=2),
        )


class _FakeGeminiClient:
    def __init__(self, models):
        self.models = models


class _FakeEmbeddingModels(_FakeModels):
    def list(self, **_kwargs):
        self.list_calls += 1
        return [_model("gemini-embedding-2", ["embedContent"])]

    def embed_content(self, *, config, **_kwargs):
        self.generate_calls += 1
        return SimpleNamespace(
            embeddings=[SimpleNamespace(values=[0.25] * config.output_dimensionality)]
        )


def _credential_env(**overrides):
    env = {
        "GEMINI_API_KEY_1": "secret-slot-one",
        "GEMINI_API_KEY_2": "secret-slot-two",
        "GEMINI_API_KEY_3": "secret-slot-three",
        "GEMINI_KEY_1_OWNER_AUTHORIZED": "true",
        "GEMINI_KEY_2_OWNER_AUTHORIZED": "true",
        "GEMINI_KEY_3_OWNER_AUTHORIZED": "true",
        "GEMINI_STRUCTURED_JSON_MODELS": "gemini-fast",
        "GEMINI_REASONING_MODELS": "gemini-fast",
        "GEMINI_VISION_MODELS": "gemini-fast",
        "GEMINI_CRITICAL_VALIDATION_MODELS": "gemini-fast",
        "GEMINI_MAX_RETRIES": "0",
        "GEMINI_QUOTA_COOLDOWN_SECONDS": "60",
    }
    env.update(overrides)
    return env


def test_three_authorized_gemini_slots_validate_independently_without_returning_secrets():
    clients = {}

    def factory(secret):
        models = _FakeModels()
        clients[secret] = _FakeGeminiClient(models)
        return clients[secret]

    manager = GeminiCredentialManager(_credential_env(), client_factory=factory)
    results = manager.validate_all_credentials()

    assert [item["credential_id"] for item in results[:3]] == [
        "gemini-key-1", "gemini-key-2", "gemini-key-3"
    ]
    assert all(item["status"] == "valid" for item in results[:3])
    assert all(client.models.list_calls == 1 for client in clients.values())
    safe_status = str(manager.configured_status())
    assert "secret-slot" not in safe_status
    assert all(item["models"][0]["supports_images"] is True for item in results[:3])


def test_critical_gemini_selection_does_not_silently_use_unlisted_weaker_model():
    env = _credential_env(
        GEMINI_API_KEY_2="",
        GEMINI_API_KEY_3="",
        GEMINI_KEY_2_OWNER_AUTHORIZED="false",
        GEMINI_KEY_3_OWNER_AUTHORIZED="false",
        GEMINI_MODEL="gemini-fast",
        GEMINI_CRITICAL_VALIDATION_MODELS="gemini-pro,gemini-flash",
    )
    manager = GeminiCredentialManager(
        env,
        client_factory=lambda _secret: _FakeGeminiClient(_FakeModels()),
    )
    manager.validate_credential("gemini-key-1")

    with pytest.raises(GeminiCredentialError):
        manager.select_model(
            "gemini-key-1",
            requires_vision=True,
            requires_structured_json=True,
            requires_reasoning=True,
            task_category=TaskCategory.CRITICAL.value,
        )


def test_missing_or_unowned_gemini_keys_are_not_selected():
    empty = GeminiCredentialManager({}, client_factory=lambda _: None)
    assert all(item["validation_status"] == "not_configured" for item in empty.configured_status())
    with pytest.raises(GeminiCredentialError):
        empty.select_credential()

    unowned = GeminiCredentialManager(
        {"GEMINI_API_KEY_1": "unowned-secret"}, client_factory=lambda _: None
    )
    assert unowned.configured_status()[0]["authorized"] is False
    with pytest.raises(GeminiCredentialError):
        unowned.select_credential()

    prioritized = GeminiCredentialManager(
        _credential_env(GEMINI_CREDENTIAL_PRIORITY="2,1,3"),
        client_factory=lambda _: None,
    )
    assert prioritized.select_credential() == "gemini-key-2"


def test_legacy_gemini_key_requires_explicit_owner_authorization():
    legacy_env = {
        "GEMINI_API_KEY": "legacy-secret",
        "GEMINI_KEY_1_OWNER_AUTHORIZED": "false",
        "GEMINI_KEY_2_OWNER_AUTHORIZED": "false",
        "GEMINI_KEY_3_OWNER_AUTHORIZED": "false",
    }
    manager = GeminiCredentialManager(legacy_env, client_factory=lambda _: None)
    assert manager.configured_status()[-1]["configured"] is True
    assert manager.configured_status()[-1]["authorized"] is False
    with pytest.raises(GeminiCredentialError):
        manager.select_credential()

    legacy_env["GEMINI_LEGACY_KEY_OWNER_AUTHORIZED"] = "true"
    authorized = GeminiCredentialManager(legacy_env, client_factory=lambda _: None)
    assert authorized.select_credential() == "gemini-legacy"


def test_duplicate_legacy_alias_is_not_validated_or_counted_twice():
    env = _credential_env(
        GEMINI_API_KEY="secret-slot-three",
        GEMINI_LEGACY_KEY_OWNER_AUTHORIZED="true",
    )
    clients = {}

    def factory(secret):
        models = _FakeModels()
        clients[secret] = _FakeGeminiClient(models)
        return clients[secret]

    manager = GeminiCredentialManager(env, client_factory=factory)
    statuses = manager.validate_all_credentials()

    assert sum(status["status"] == "valid" for status in statuses) == 3
    assert statuses[-1]["status"] == "duplicate"
    assert statuses[-1]["duplicate_of"] == "gemini-key-3"
    assert len(clients) == 3
    assert all(client.models.list_calls == 1 for client in clients.values())
    legacy_status = manager.configured_status()[-1]
    assert legacy_status["selected"] is False
    assert legacy_status["duplicate_of"] == "gemini-key-3"


def test_gemini_registry_never_uses_more_than_three_distinct_keys():
    env = _credential_env(
        GEMINI_API_KEY="secret-legacy-fourth",
        GEMINI_LEGACY_KEY_OWNER_AUTHORIZED="true",
    )
    manager = GeminiCredentialManager(env, client_factory=lambda _: _FakeGeminiClient(_FakeModels()))
    assert manager.select_credential() == "gemini-key-1"
    assert manager.configured_status()[-1]["selected"] is False
    assert manager.configured_status()[-1]["duplicate_of"] == "limit_reached"


def test_embedding_requests_use_discovered_embedcontent_model_through_router():
    clients = {}

    def factory(secret):
        client = _FakeGeminiClient(_FakeEmbeddingModels())
        clients[secret] = client
        return client

    env = _credential_env(
        GEMINI_API_KEY_2="",
        GEMINI_API_KEY_3="",
        GEMINI_KEY_2_OWNER_AUTHORIZED="false",
        GEMINI_KEY_3_OWNER_AUTHORIZED="false",
        GEMINI_EMBEDDING_MODEL="gemini-embedding-2",
    )
    manager = GeminiCredentialManager(env, client_factory=factory)
    config = AIConfiguration()
    config.provider = "ollama"
    config.gemini_enabled = True
    router = ModelRouter()
    router.config = config
    router.gemini = GeminiProvider(manager)

    vectors = router.embed_texts(["first", "second"])

    assert vectors is not None and len(vectors) == 2
    assert all(len(vector) == 768 for vector in vectors)
    assert clients["secret-slot-one"].models.generate_calls == 2
    assert router.usage_summary()["external"]["requests"] == 2
    assert manager.configured_status()[0]["request_count"] == 2


def test_rate_limit_blocks_all_gemini_slots_and_never_rotates_credentials():
    clients = {}

    def factory(secret):
        models = _FakeModels(generation=RuntimeError("quota exceeded per day limit"))
        clients[secret] = _FakeGeminiClient(models)
        return clients[secret]

    manager = GeminiCredentialManager(_credential_env(), client_factory=factory)
    provider = GeminiProvider(manager)
    profile = classify_task("classification", requires_structured_json=True)

    with pytest.raises(GeminiRequestFailure) as error:
        provider.generate("classify", format_json=True, task_profile=profile)
    assert error.value.credential_id == "gemini-key-1"
    assert error.value.rate_limited
    assert clients["secret-slot-one"].models.generate_calls == 1
    assert "secret-slot-two" not in clients
    with pytest.raises(GeminiRateLimitError):
        provider.generate("classify", format_json=True, task_profile=profile)
    assert "secret-slot-two" not in clients


def test_quota_exhaustion_persists_across_manager_restart_until_operator_reset(tmp_path):
    state_path = tmp_path / "provider-state.json"
    env = _credential_env(GEMINI_PROVIDER_STATE_FILE=str(state_path))
    manager = GeminiCredentialManager(env, client_factory=lambda _secret: _FakeGeminiClient(_FakeModels()))
    manager.record_failure("gemini-key-1", RuntimeError("quota exceeded per day limit"))

    restarted = GeminiCredentialManager(
        env,
        client_factory=lambda _secret: _FakeGeminiClient(_FakeModels()),
    )
    assert restarted.configured_status()[0]["provider_blocked_reason"] == "quota_exhausted"
    with pytest.raises(GeminiRateLimitError):
        restarted.select_credential()
    with pytest.raises(GeminiCredentialError):
        restarted.clear_quota_exhaustion(provider_reset_confirmed=False)

    restarted.clear_quota_exhaustion(provider_reset_confirmed=True)
    assert restarted.select_credential() == "gemini-key-1"
    assert "secret-slot-one" not in state_path.read_text(encoding="utf-8")


def test_ollama_discovery_reads_tags_show_and_loaded_state_without_pulling_models():
    config = AIConfiguration()
    config.ollama_base_url = "http://ollama.test"
    config.discovery_cache_seconds = 30
    client = OllamaClient(config)
    requested = []

    def get(url, **_kwargs):
        requested.append(url)
        if url.endswith("/api/tags"):
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"models": [
                    {"name": "qwen3.5:4b", "size": 4_000, "details": {
                        "family": "qwen", "families": ["qwen"], "parameter_size": "4B"
                    }},
                    {"name": "gemma4:e4b", "size": 8_000, "details": {
                        "family": "gemma", "families": ["gemma"], "parameter_size": "4B"
                    }},
                ]},
            )
        if url.endswith("/api/ps"):
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"models": [{"name": "gemma4:e4b", "size_vram": 5000}]},
            )
        return SimpleNamespace(status_code=200, json=lambda: {"version": "0.6.0"})

    def post(url, *, json, **_kwargs):
        requested.append(url)
        name = json["model"]
        caps = ["completion", "tools"]
        if name == "gemma4:e4b":
            caps += ["vision", "thinking"]
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"capabilities": caps, "model_info": {"context_length": 8192}},
        )

    client._session.get = MagicMock(side_effect=get)
    client._session.post = MagicMock(side_effect=post)
    registry = client.discover_models(refresh=True)

    assert registry["online"] is True
    models = {item.name: item for item in registry["models"]}
    assert models["qwen3.5:4b"].family == "qwen"
    assert models["qwen3.5:4b"].supports_images is False
    assert models["qwen3.5:4b"].supports_structured_json is True
    assert models["qwen3.5:4b"].reasoning_suitable is None
    assert models["gemma4:e4b"].supports_images is True
    assert models["gemma4:e4b"].reasoning_suitable is True
    assert models["gemma4:e4b"].loaded is True
    assert models["gemma4:e4b"].size_vram_bytes == 5000
    assert not any("/api/pull" in url for url in requested)


def test_ollama_family_falls_back_to_model_name_when_metadata_is_missing():
    assert _family_from_ollama_name("qwen3.5:4b") == "qwen"
    assert _family_from_ollama_name("gemma4:e4b") == "gemma"


def test_local_router_never_sends_images_to_text_only_model():
    config = AIConfiguration()
    config.provider = "ollama"
    config.gemini_enabled = False
    config.gemini_fallback_enabled = False
    router = ModelRouter()
    router.config = config
    router.policy = RoutingPolicy(config)
    router.ollama = MagicMock()
    router.ollama.discover_models.return_value = {
        "online": True,
        "models": [OllamaModelCapability(
            name="qwen-text", supports_images=False, supports_structured_json=True,
            reasoning_suitable=True,
        )],
    }

    with pytest.raises(NoSuitableModelError):
        router.generate(
            "difficult_ocr", "read this handwriting", images=[b"page"],
            format_json=True, requires_reasoning=True,
        )
    router.ollama.chat.assert_not_called()


def test_simple_local_task_prefers_configured_lightweight_model():
    config = AIConfiguration()
    config.provider = "ollama"
    config.gemini_enabled = False
    config.model_priorities[TaskCategory.LOW.value] = ["qwen-lite", "larger-model"]
    router = ModelRouter()
    router.config = config
    router.policy = RoutingPolicy(config)
    router.ollama = MagicMock()
    router.ollama.discover_models.return_value = {
        "online": True,
        "models": [
            OllamaModelCapability(name="qwen-lite", supports_structured_json=True),
            OllamaModelCapability(name="larger-model", supports_structured_json=True),
        ],
    }
    router.ollama.chat.return_value = ('{"classifications": []}', {"classifications": []}, 1)
    router.ollama.last_usage.return_value = {"input_tokens": 3, "output_tokens": 1}
    router.ollama.last_retry_count.return_value = 0

    result = router.generate("classification", "classify the questions")

    assert result.model_used == "qwen-lite"
    router.ollama.chat.assert_called_once()
    assert router.ollama.chat.call_args.kwargs["model"] == "qwen-lite"


def test_high_complexity_prefers_capable_local_model_before_gemini_when_enabled():
    config = AIConfiguration()
    config.provider = "ollama"
    config.gemini_enabled = True
    config.local_first = True
    router = ModelRouter()
    router.config = config
    router.ollama = MagicMock()
    router.ollama.discover_models.return_value = {
        "online": True,
        "models": [OllamaModelCapability(
            name="gemma4:e4b", supports_images=True,
            supports_structured_json=True, reasoning_suitable=True,
        )],
    }
    router.ollama.chat.return_value = ('{"answer": "read"}', {"answer": "read"}, 4)
    router.ollama.last_usage.return_value = {"input_tokens": 20, "output_tokens": 4}
    router.ollama.last_retry_count.return_value = 0
    router.gemini.generate = MagicMock(return_value=(
        '{"answer": "read"}', {"answer": "read"}, {
            "model": "gemini-3.8-flash", "credential_id": "gemini-key-2",
            "retry_count": 0, "input_tokens": 10, "output_tokens": 4,
            "estimated_cost_usd": None,
        }
    ))

    result = router.generate(
        "grading", "interpret diagram", images=[b"image"],
        question_type="DIAGRAM", format_json=True, requires_reasoning=True,
    )

    assert result.provider == "ollama"
    assert result.model_used == "gemma4:e4b"
    assert result.task_category == TaskCategory.HIGH.value
    router.gemini.generate.assert_not_called()


def test_local_first_does_not_call_gemini_when_capable_local_model_succeeds():
    config = AIConfiguration()
    config.provider = "ollama"
    config.gemini_enabled = True
    config.local_first = True
    router = ModelRouter()
    router.config = config
    router.policy = RoutingPolicy(config)
    router.gemini.generate = MagicMock(side_effect=NoSuitableModelError("no key"))
    router.ollama = MagicMock()
    router.ollama.discover_models.return_value = {
        "online": True,
        "models": [OllamaModelCapability(
            name="local-vision-reasoner", supports_images=True,
            supports_structured_json=True, reasoning_suitable=True,
        )],
    }
    router.ollama.chat.return_value = ('{"ok": true}', {"ok": True}, 2)
    router.ollama.last_usage.return_value = {"input_tokens": 4, "output_tokens": 2}
    router.ollama.last_retry_count.return_value = 0

    result = router.generate(
        "grading", "interpret diagram", images=[b"image"],
        question_type="DIAGRAM", format_json=True, requires_reasoning=True,
    )

    assert result.provider == "ollama"
    assert result.model_used == "local-vision-reasoner"
    router.gemini.generate.assert_not_called()
    router.ollama.chat.assert_called_once()


def test_local_capability_gap_escalates_to_gemini_when_enabled():
    config = AIConfiguration()
    config.provider = "ollama"
    config.gemini_enabled = True
    config.gemini_fallback_enabled = True
    config.local_first = True
    router = ModelRouter()
    router.config = config
    router.ollama = MagicMock()
    router.ollama.discover_models.return_value = {
        "online": True,
        "models": [OllamaModelCapability(
            name="qwen-text-only", supports_images=False,
            supports_structured_json=True, reasoning_suitable=True,
        )],
    }
    router.gemini.generate = MagicMock(return_value=(
        '{"answer": "read"}', {"answer": "read"}, {
            "model": "gemini-3.8-flash", "credential_id": "gemini-key-1",
            "retry_count": 0, "input_tokens": 8, "output_tokens": 3,
            "estimated_cost_usd": None,
        }
    ))

    result = router.generate(
        "difficult_ocr", "read this handwriting", images=[b"image"],
        format_json=True, requires_reasoning=True,
    )

    assert result.provider == "gemini"
    assert result.model_used == "gemini-3.8-flash"
    router.gemini.generate.assert_called_once()


def test_gemini_key_material_is_absent_from_failure_logs(caplog):
    secret = "fake-sensitive-key-value"
    clients = {}

    class AuthFailure(Exception):
        code = 401

    def factory(value):
        models = _FakeModels(generation=AuthFailure(f"invalid key {secret}"))
        clients[value] = _FakeGeminiClient(models)
        return clients[value]

    env = _credential_env(
        GEMINI_API_KEY_2="",
        GEMINI_API_KEY_3="",
        GEMINI_KEY_2_OWNER_AUTHORIZED="false",
        GEMINI_KEY_3_OWNER_AUTHORIZED="false",
    )
    env["GEMINI_API_KEY_1"] = secret
    credentials = GeminiCredentialManager(env, client_factory=factory)
    config = AIConfiguration()
    config.provider = "ollama"
    config.gemini_enabled = True
    router = ModelRouter()
    router.config = config
    router.gemini = GeminiProvider(credentials)
    router.ollama = MagicMock()
    router.ollama.discover_models.return_value = {"online": False, "models": []}

    with caplog.at_level(logging.INFO), pytest.raises(NoSuitableModelError):
        router.generate("grading", "review this grading", is_disputed=True)

    assert secret not in caplog.text
    assert secret not in str(credentials.configured_status())
    assert "gemini-key-1" in caplog.text
