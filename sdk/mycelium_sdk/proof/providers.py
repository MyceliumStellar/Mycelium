"""
LLM provider registry for the proof layer — worker agents + judge panels.

The panel's whole security argument rests on *model diversity*: a jury of
independent model families is far harder to fool (a prompt injection that beats
one family rarely beats another) than N copies of one model. This module exposes
a uniform ``complete_fn(system, user) -> text`` so the worker and each judge seat
can be wired to a different model — across different providers — with one shape.

Models are named ``provider:model``:

    nvidia:meta/llama-3.3-70b-instruct      groq:llama-3.3-70b-versatile
    nvidia:deepseek-ai/deepseek-v4-pro      groq:openai/gpt-oss-120b
    gemini:gemini-2.5-flash                 anthropic:claude-3-5-sonnet-20241022
    openai:gpt-4o

``resolve_completer("groq:llama-3.3-70b-versatile")`` returns the backend. A bare
``model`` (no ``provider:`` prefix) defaults to NVIDIA. NVIDIA, Groq, and OpenAI
speak the OpenAI Chat Completions API, so one client covers them; Gemini and
Anthropic use their respective API shapes via specialized completers. Keys come
from the environment, never code.
"""

import os
import json
from typing import Callable, Dict, List, Optional, Tuple

import requests

from mycelium_sdk.logging import get_logger

_log = get_logger("proof.providers")

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"
ANTHROPIC_BASE_URL = "https://api.anthropic.com"

# provider -> (base_url, api_key_env)
PROVIDERS: Dict[str, Tuple[str, str]] = {
    "nvidia": (NVIDIA_BASE_URL, "NVIDIA_API_KEY"),
    "groq": (GROQ_BASE_URL, "GROQ_API_KEY"),
    "openai": (OPENAI_BASE_URL, "OPENAI_API_KEY"),
    "gemini": (GEMINI_BASE_URL, "GEMINI_API_KEY"),
    "anthropic": (ANTHROPIC_BASE_URL, "ANTHROPIC_API_KEY"),
}
DEFAULT_PROVIDER = "nvidia"

Completer = Callable[[str, str], str]


def split_spec(spec: str) -> Tuple[str, str]:
    """
    Split ``provider:model`` into ``(provider, model)``. Only a known provider
    prefix is treated as a provider — model ids legitimately contain ``/`` or ``:``,
    so a bare model is left to the default.
    """
    if ":" in spec:
        head, rest = spec.split(":", 1)
        if head in PROVIDERS:
            return head, rest
    return DEFAULT_PROVIDER, spec


def openai_chat_completer(
    model: str,
    *,
    base_url: str = NVIDIA_BASE_URL,
    api_key_env: str = "NVIDIA_API_KEY",
    api_key: Optional[str] = None,
    max_tokens: int = 2048,
    temperature: float = 0.2,
    timeout: int = 120,
) -> Completer:
    """
    Return a ``complete_fn(system, user) -> text`` bound to one OpenAI-compatible
    ``model`` at ``base_url``. Low temperature by default so a judge's score is
    reproducible enough to mean something.
    """
    key = api_key or os.environ.get(api_key_env)
    if not key:
        raise RuntimeError(
            f"No API key for model {model!r}: set ${api_key_env} (or pass api_key)."
        )
    url = base_url.rstrip("/") + "/chat/completions"

    def complete(system: str, user: str) -> str:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"{model}: HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"{model}: unexpected response shape: {str(data)[:300]}") from exc

    return complete


def gemini_chat_completer(
    model: str,
    *,
    api_key_env: str = "GEMINI_API_KEY",
    api_key: Optional[str] = None,
    max_tokens: int = 2048,
    temperature: float = 0.2,
    timeout: int = 120,
) -> Completer:
    """
    Return a ``complete_fn(system, user) -> text`` bound to a Gemini model
    using Google's direct REST API.
    """
    key = api_key or os.environ.get(api_key_env)
    if not key:
        raise RuntimeError(
            f"No API key for model {model!r}: set ${api_key_env} (or pass api_key)."
        )
    clean_model = model if model.startswith("models/") else f"models/{model}"
    url = f"https://generativelanguage.googleapis.com/v1beta/{clean_model}:generateContent?key={key}"

    def complete(system: str, user: str) -> str:
        gen_config = {"temperature": temperature, "maxOutputTokens": max_tokens}
        if "json" in system.lower() or "json" in user.lower():
            gen_config["responseMimeType"] = "application/json"

        payload = {
            "contents": [{"parts": [{"text": user}]}],
            "systemInstruction": {"parts": [{"text": system}]},
            "generationConfig": gen_config,
        }
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"{model}: HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"{model}: unexpected response shape: {str(data)[:300]}") from exc

    return complete


def anthropic_chat_completer(
    model: str,
    *,
    api_key_env: str = "ANTHROPIC_API_KEY",
    api_key: Optional[str] = None,
    max_tokens: int = 2048,
    temperature: float = 0.2,
    timeout: int = 120,
) -> Completer:
    """
    Return a ``complete_fn(system, user) -> text`` bound to an Anthropic model
    using Anthropic's direct Messages REST API.
    """
    key = api_key or os.environ.get(api_key_env)
    if not key:
        raise RuntimeError(
            f"No API key for model {model!r}: set ${api_key_env} (or pass api_key)."
        )
    url = "https://api.anthropic.com/v1/messages"

    def complete(system: str, user: str) -> str:
        payload = {
            "model": model,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        if resp.status_code != 200:
            raise RuntimeError(f"{model}: HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        try:
            return data["content"][0]["text"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"{model}: unexpected response shape: {str(data)[:300]}") from exc

    return complete


def resolve_completer(spec: str, *, api_key: Optional[str] = None, **kw) -> Completer:
    """
    Build a completer from a ``provider:model`` spec (e.g. ``groq:llama-3.3-70b-
    versatile``, ``gemini:gemini-2.5-flash``, or ``anthropic:claude-3-5-sonnet-20241022``).
    """
    provider, model = split_spec(spec)
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider {provider!r}; known: {sorted(PROVIDERS)}.")

    if provider == "gemini":
        return gemini_chat_completer(model, api_key_env="GEMINI_API_KEY", api_key=api_key, **kw)
    elif provider == "anthropic":
        return anthropic_chat_completer(model, api_key_env="ANTHROPIC_API_KEY", api_key=api_key, **kw)
    else:
        base_url, key_env = PROVIDERS[provider]
        return openai_chat_completer(model, base_url=base_url, api_key_env=key_env, api_key=api_key, **kw)


def list_models(provider: str, *, api_key: Optional[str] = None, timeout: int = 20) -> List[str]:
    """Discover the models a provider serves (its ``/models`` endpoint or equivalent),
    so a poster/agent picks a real id rather than guessing. Mirrors the CLI/frontend
    model-discovery flow."""
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider {provider!r}; known: {sorted(PROVIDERS)}.")
    base_url, key_env = PROVIDERS[provider]
    key = api_key or os.environ.get(key_env)
    if not key:
        raise RuntimeError(f"No API key for {provider}: set ${key_env}.")

    if provider == "gemini":
        url = f"{base_url}/v1beta/models?key={key}"
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        # Clean model names: strip 'models/' prefix
        return [m["name"].split("/")[-1] for m in data.get("models", [])]

    elif provider == "anthropic":
        url = f"{base_url}/v1/models"
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01"
        }
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return [m["id"] for m in data.get("data", [])]

    else:
        resp = requests.get(
            base_url.rstrip("/") + "/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout,
        )
        resp.raise_for_status()
        return [m["id"] for m in resp.json().get("data", [])]


# Back-compat: the P1 demo imported `nvidia(...)`.
def nvidia(model: str, **kw) -> Completer:
    return openai_chat_completer(model, **kw)
