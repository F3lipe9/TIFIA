"""LLM client interface and providers.

The classifier only talks to LLMClient.complete_json, so another provider is one more
subclass registered in PROVIDERS and named in config.yaml (llm.provider). Each client
counts the tokens it uses, for cost tracking against the budget.
"""

import csv
import json
import logging
import os
import re
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_USAGE_LOCK = threading.Lock()  # classifier workers add usage from several threads

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


class LLMError(Exception):
    """The reply was unusable: refused, cut off, or not a JSON object."""


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0                 # not counting the cached tokens below
    output_tokens: int = 0                # includes thinking
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def add(self, usage: Any) -> None:
        """Add one response's usage (any object with these token attributes). Safe to
        call from several threads at once."""
        with _USAGE_LOCK:
            self.calls += 1
            for f in fields(self)[1:]:
                setattr(self, f.name,
                        getattr(self, f.name) + (getattr(usage, f.name, 0) or 0))

    def cost(self, prices: dict[str, float]) -> float:
        """USD, from prices per million tokens: input, output, cache_write, cache_read."""
        return (self.input_tokens * prices["input"]
                + self.output_tokens * prices["output"]
                + self.cache_creation_input_tokens * prices["cache_write"]
                + self.cache_read_input_tokens * prices["cache_read"]) / 1e6


LEDGER_COLUMNS = ["time", "executed", "template", "model", "deviations", "llm_labels",
                  "cached", "needs_review", "calls", "input_tokens", "output_tokens",
                  "cache_write_tokens", "cache_read_tokens", "cost_usd", "total_usd"]


def append_usage_log(path: Path, row: dict) -> float:
    """Append a run's row (LEDGER_COLUMNS but total_usd) to the CSV cost ledger at path;
    returns the total cost of all runs logged so far."""
    total = row["cost_usd"]
    if path.exists():
        with open(path, encoding="utf-8", newline="") as f:
            total += sum(float(r["cost_usd"]) for r in csv.DictReader(f))
    row = {**row, "total_usd": round(total, 4)}
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LEDGER_COLUMNS)
        if new:
            writer.writeheader()
        writer.writerow(row)
    return total


def parse_json_object(text: str) -> dict:
    """The JSON object in a reply, allowing a ```json fence around it."""
    try:
        data = json.loads(_FENCE.sub("", text.strip()))
    except json.JSONDecodeError as e:
        raise LLMError(f"reply is not valid JSON ({e})") from e
    if not isinstance(data, dict):
        raise LLMError("reply is JSON but not an object")
    return data


class LLMClient(ABC):
    model_id = "llm"  # identifies model and settings in cache keys

    def __init__(self):
        self.usage = Usage()

    @abstractmethod
    def complete_json(self, system: str, user: str, schema: dict | None = None) -> dict:
        """One request; the reply as a JSON object. schema (JSON Schema) constrains the
        reply where the provider supports it. Raises LLMError for an unusable reply;
        API errors (authentication, rate limits after retries) propagate."""

    @classmethod
    def model_id_from_config(cls, config: dict) -> str:
        return cls.model_id


class AnthropicClient(LLMClient):
    """Claude through the Anthropic SDK, with structured outputs for the schema and the
    system prompt cached between calls. Thinking is adaptive (the model's default);
    effort sets how much it thinks. One client is shared by the classifier's worker
    threads (the SDK client is thread-safe)."""

    def __init__(self, model: str, max_tokens: int = 16000, effort: str | None = None,
                 api_key: str | None = None, client: Any = None, api_retries: int = 6):
        super().__init__()
        if client is None:
            import anthropic
            # the SDK retries rate limits (429), overload (529), other 5xx and connection
            # errors with exponential backoff, honoring retry-after; its default of 2
            # retries is too few with parallel requests on a new key's low rate limits.
            # Without a key the SDK looks up ANTHROPIC_API_KEY or a login profile itself.
            client = anthropic.Anthropic(api_key=api_key or None, max_retries=api_retries)
        self._client = client
        self.model, self.max_tokens, self.effort = model, max_tokens, effort
        self.model_id = self.model_id_from_config({"model": model, "effort": effort})

    def complete_json(self, system: str, user: str, schema: dict | None = None) -> dict:
        output_config: dict[str, Any] = {}
        if self.effort:
            output_config["effort"] = self.effort
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
        }
        if output_config:
            request["output_config"] = output_config
        response = self._client.messages.create(**request)
        self.usage.add(response.usage)
        log.debug("%s: %s in, %s out", self.model, response.usage.input_tokens,
                  response.usage.output_tokens)
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise LLMError(f"request refused ({getattr(details, 'category', None) or 'no reason'})")
        if response.stop_reason == "max_tokens":
            raise LLMError(f"reply cut off at max_tokens={self.max_tokens}")
        return parse_json_object("".join(b.text for b in response.content if b.type == "text"))

    @classmethod
    def model_id_from_config(cls, config: dict) -> str:
        return f"anthropic:{config['model']}:{config.get('effort') or 'default'}"

    @classmethod
    def from_config(cls, config: dict) -> "AnthropicClient":
        """Raises RuntimeError if no credentials are found, before any request."""
        key_env = config.get("api_key_env", "LLM_API_KEY")
        client = cls(config["model"], max_tokens=config.get("max_tokens", 16000),
                     effort=config.get("effort"), api_key=os.environ.get(key_env) or None,
                     api_retries=config.get("api_retries", 6))
        sdk = client._client
        if not (getattr(sdk, "api_key", None) or getattr(sdk, "auth_token", None)):
            raise RuntimeError(f"no Anthropic API key: set {key_env} in .env (see "
                               ".env.example) or ANTHROPIC_API_KEY in the environment")
        return client


PROVIDERS: dict[str, type[LLMClient]] = {"anthropic": AnthropicClient}


def _provider(config: dict) -> type[LLMClient]:
    provider = config["provider"]
    if provider not in PROVIDERS:
        raise ValueError(f"unknown llm.provider {provider!r}; known: {', '.join(PROVIDERS)}")
    return PROVIDERS[provider]


def make_client(config: dict) -> LLMClient:
    """The client for config.yaml's llm section."""
    return _provider(config).from_config(config)


def model_id(config: dict) -> str:
    """The model_id make_client(config) would have, without creating the client."""
    return _provider(config).model_id_from_config(config)
