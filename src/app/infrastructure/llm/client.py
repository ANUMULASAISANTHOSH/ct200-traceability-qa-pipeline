"""Pluggable LLM client port and provider adapters."""

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union
import httpx
from src.app.config import Settings, get_settings
from src.app.domain.exceptions import ConfigurationError, LLMProviderError


class LLMClient(ABC):
    """Abstract LLM provider client interface."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Name of the provider, e.g., 'mock', 'openai'."""
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Model identifier used for generation."""
        pass

    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Generate a raw text response given system and user prompts.
        
        Raises:
            LLMProviderError: On network, rate-limit, or provider execution failures.
        """
        pass


class MockLLMClient(LLMClient):
    """Deterministic mock LLM client producing valid QA test suites referencing prompt context."""

    def __init__(
        self,
        model: str = "mock-qa-engine-v1",
        canned_responses: Optional[List[Union[str, Exception]]] = None,
    ) -> None:
        self._model = model
        self.canned_responses: List[Union[str, Exception]] = canned_responses or []
        self.call_count = 0
        self.last_system_prompt: Optional[str] = None
        self.last_user_prompt: Optional[str] = None

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def model_name(self) -> str:
        return self._model

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        self.call_count += 1
        self.last_system_prompt = system_prompt
        self.last_user_prompt = user_prompt

        # If a canned response queue is configured, pop and return or raise
        if self.canned_responses:
            item = self.canned_responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        # Extract available node-version IDs from user prompt to reference valid source snapshots
        found_nodes = re.findall(r'"node_version_id":\s*"([^"]+)"', user_prompt)
        if not found_nodes:
            found_nodes = re.findall(r'nv_[a-f0-9]{16}', user_prompt)
        
        primary_refs = found_nodes[:2] if found_nodes else ["nv_default_reference"]

        # Deterministic 3 executable test cases
        suite = {
            "test_cases": [
                {
                    "id": "TC-001",
                    "title": "Verify Operational Specifications and Normal Operating Baseline",
                    "preconditions": [
                        "System is connected to calibrated power source.",
                        "Operator interface is in standby mode.",
                    ],
                    "steps": [
                        {
                            "step_number": 1,
                            "action": "Power on the unit and observe the startup initialization sequence.",
                            "expected_result": "Self-test passes with no fault codes and indicator light shows ready.",
                        },
                        {
                            "step_number": 2,
                            "action": "Query operating parameters against manual specifications.",
                            "expected_result": "All operational limits align with declared manufacturer specifications.",
                        },
                    ],
                    "node_references": primary_refs,
                    "safety_critical": False,
                },
                {
                    "id": "TC-002",
                    "title": "Verify Parameter Threshold Safety Boundary and Alarm Response",
                    "preconditions": [
                        "Device is configured in active monitoring mode.",
                        "Safety sensor simulation test rig is attached.",
                    ],
                    "steps": [
                        {
                            "step_number": 1,
                            "action": "Introduce input pressure at upper boundary limit.",
                            "expected_result": "System remains operational within nominal tolerance without error.",
                        },
                        {
                            "step_number": 2,
                            "action": "Increase input pressure exceeding maximum allowable tolerance threshold.",
                            "expected_result": "Audible and visual high-priority alarm activates within specified limit.",
                        },
                    ],
                    "node_references": [primary_refs[0]],
                    "safety_critical": True,
                },
                {
                    "id": "TC-003",
                    "title": "Verify Hazard Interlock and Emergency Shutdown Functionality",
                    "preconditions": [
                        "Equipment is operating at steady nominal state.",
                    ],
                    "steps": [
                        {
                            "step_number": 1,
                            "action": "Trigger emergency stop hardware interlock.",
                            "expected_result": "Power to drive subsystems terminates immediately; fail-safe state is engaged.",
                        },
                        {
                            "step_number": 2,
                            "action": "Inspect system audit log for shutdown event timestamp.",
                            "expected_result": "Shutdown event is logged with corresponding safety state code.",
                        },
                    ],
                    "node_references": primary_refs,
                    "safety_critical": True,
                },
            ]
        }
        return json.dumps(suite, indent=2)


class OpenAILLMClient(LLMClient):
    """Production provider adapter for OpenAI Chat Completions API."""

    def __init__(
        self,
        api_key: Optional[str],
        model: str = "gpt-4o",
        timeout: float = 30.0,
        base_url: str = "https://api.openai.com/v1",
    ) -> None:
        if not api_key or not api_key.strip():
            raise ConfigurationError(
                "OpenAI LLM provider requested but LLM_API_KEY is not configured. "
                "Set the LLM_API_KEY environment variable or switch LLM_PROVIDER to 'mock'."
            )
        self._api_key = api_key.strip()
        self._model = model
        self._timeout = timeout
        self._base_url = base_url.rstrip("/")

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return self._model

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        }

        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(url, json=payload, headers=headers)
                if response.status_code != 200:
                    # Never log headers or API keys
                    raise LLMProviderError(
                        f"OpenAI API call failed with status {response.status_code}: {response.text[:200]}"
                    )
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                return content
        except httpx.RequestError as exc:
            # Network transport error without leaking sensitive credentials
            raise LLMProviderError(f"OpenAI HTTP connection error: {type(exc).__name__}") from None


def get_llm_client(settings: Optional[Settings] = None) -> LLMClient:
    """Factory selecting the appropriate LLMClient based on environment configuration."""
    cfg = settings or get_settings()
    provider = cfg.llm_provider.lower().strip()

    if provider == "mock":
        return MockLLMClient(model=cfg.llm_model)
    elif provider == "openai":
        return OpenAILLMClient(
            api_key=cfg.llm_api_key,
            model=cfg.llm_model,
        )
    else:
        raise ConfigurationError(
            f"Unsupported LLM provider: '{cfg.llm_provider}'. Supported providers are: 'mock', 'openai'."
        )
