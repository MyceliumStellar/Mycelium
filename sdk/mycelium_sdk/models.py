"""
Provider model discovery.

When a developer builds an API-backed agent (Gemini / Anthropic / OpenAI-style
endpoints), we must NEVER guess a model identifier — a hallucinated name fails
at runtime. Instead, given the provider and the developer's API key, we query
the provider's own "list models" endpoint and return the real, currently
available model ids. The CLI (`mycelium init`) presents these for selection.

Only the stdlib + `requests` (already an SDK dependency) is used, so this works
without installing any provider SDK.
"""

from typing import List, Optional

import requests

# Frameworks whose models we can enumerate. Cloud providers need an API key;
# ollama is a local server and needs none.
API_FRAMEWORKS = ("gemini", "anthropic", "openai", "ollama")
# Frameworks that do NOT require an API key for discovery (local runtimes).
KEYLESS_FRAMEWORKS = ("ollama",)

DEFAULT_OLLAMA_URL = "http://localhost:11434"

_TIMEOUT = 20


class ModelDiscoveryError(Exception):
    """Raised when a provider's model list cannot be retrieved."""


def supports_discovery(framework: str) -> bool:
    """True if `framework` exposes a model list we can enumerate."""
    return (framework or "").lower() in API_FRAMEWORKS


def requires_api_key(framework: str) -> bool:
    """True if discovery for `framework` needs an API key (cloud providers)."""
    fw = (framework or "").lower()
    return fw in API_FRAMEWORKS and fw not in KEYLESS_FRAMEWORKS


def list_models(
    framework: str, api_key: Optional[str] = None, base_url: Optional[str] = None
) -> List[str]:
    """
    Return the list of model ids available for `framework`.

    - gemini    -> Google Generative Language API (models supporting generateContent)
    - anthropic -> Anthropic Messages API model catalogue
    - openai    -> any OpenAI-compatible endpoint (override host via base_url)
    - ollama    -> a local ollama server (no key; override host via base_url)

    Raises ModelDiscoveryError on auth/network failure so callers can fall back
    to manual entry rather than proceeding with a guessed name.
    """
    fw = (framework or "").lower()
    key = (api_key or "").strip()
    if requires_api_key(fw) and not key:
        raise ModelDiscoveryError("An API key is required to list available models.")

    if fw == "gemini":
        return _list_gemini(key)
    if fw == "anthropic":
        return _list_anthropic(key)
    if fw == "openai":
        return _list_openai(key, base_url)
    if fw == "ollama":
        return _list_ollama(base_url)
    raise ModelDiscoveryError(
        f"Model discovery is not supported for framework '{framework}'."
    )


def _get_json(url: str, headers: Optional[dict] = None) -> dict:
    try:
        res = requests.get(url, headers=headers or {}, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise ModelDiscoveryError(f"Network error contacting the model API: {exc}") from exc
    if res.status_code in (401, 403):
        raise ModelDiscoveryError("The API key was rejected (unauthorized). Check the key.")
    if not res.ok:
        raise ModelDiscoveryError(f"Model API returned HTTP {res.status_code}: {res.text[:200]}")
    try:
        return res.json()
    except ValueError as exc:
        raise ModelDiscoveryError("Model API returned a non-JSON response.") from exc


def _list_gemini(api_key: str) -> List[str]:
    data = _get_json(
        f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    )
    models = []
    for m in data.get("models", []):
        # Only surface models that can actually generate content (not embeddings/TTS-only).
        methods = m.get("supportedGenerationMethods", [])
        if methods and "generateContent" not in methods:
            continue
        name = m.get("name", "")
        # API returns "models/gemini-2.5-flash"; the usable id is the last segment.
        models.append(name.split("/", 1)[1] if name.startswith("models/") else name)
    if not models:
        raise ModelDiscoveryError("No generateContent-capable models returned for this key.")
    return models


def _list_anthropic(api_key: str) -> List[str]:
    data = _get_json(
        "https://api.anthropic.com/v1/models",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
    )
    models = [m["id"] for m in data.get("data", []) if "id" in m]
    if not models:
        raise ModelDiscoveryError("No models returned for this Anthropic key.")
    return models


def _list_openai(api_key: str, base_url: Optional[str]) -> List[str]:
    base = (base_url or "https://api.openai.com/v1").rstrip("/")
    data = _get_json(f"{base}/models", headers={"Authorization": f"Bearer {api_key}"})
    models = [m["id"] for m in data.get("data", []) if "id" in m]
    if not models:
        raise ModelDiscoveryError("No models returned for this endpoint.")
    return models


def _list_ollama(base_url: Optional[str]) -> List[str]:
    base = (base_url or DEFAULT_OLLAMA_URL).rstrip("/")
    try:
        data = _get_json(f"{base}/api/tags")
    except ModelDiscoveryError as exc:
        # The most common cause is no local server running — make that actionable.
        raise ModelDiscoveryError(
            f"Could not reach an ollama server at {base} ({exc}). "
            "Is `ollama serve` running? Pull a model with e.g. `ollama pull llama3`."
        ) from exc
    models = [m["name"] for m in data.get("models", []) if m.get("name")]
    if not models:
        raise ModelDiscoveryError(
            f"No models installed on the ollama server at {base}. "
            "Pull one with e.g. `ollama pull llama3`."
        )
    return models
