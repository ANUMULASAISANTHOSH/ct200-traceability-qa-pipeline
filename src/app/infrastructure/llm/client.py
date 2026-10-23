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

        # Parse available structured source snapshot nodes from user prompt
        source_nodes: List[Dict[str, Any]] = []
        try:
            match = re.search(
                r'=== BEGIN QUOTED SOURCE MANUAL DATA.*?===\s*(\[.*?\])\s*=== END QUOTED SOURCE MANUAL DATA ===',
                user_prompt,
                re.DOTALL,
            )
            if match:
                source_nodes = json.loads(match.group(1))
        except Exception:
            pass

        # Fallback regex extraction of node IDs if JSON block was not matched
        if not source_nodes:
            found_ids = re.findall(r'"node_version_id":\s*"([^"]+)"', user_prompt)
            if not found_ids:
                found_ids = re.findall(r'nv_[a-f0-9]{16}', user_prompt)
            source_nodes = [
                {"node_version_id": nid, "text": "", "heading": "", "section_number": ""}
                for nid in found_ids
            ]

        def safe_str(val: Any) -> str:
            return str(val).lower() if val is not None else ""

        def matches_keywords(node: Dict[str, Any], kws: List[str]) -> bool:
            combined = f"{safe_str(node.get('heading'))} {safe_str(node.get('section_number'))} {safe_str(node.get('text'))}"
            return any(kw.lower() in combined for kw in kws)

        # Identify requirement-specific nodes with preference for substantive content nodes:
        # 1. Error Codes Table (Table 4.2)
        err_node = next(
            (n for n in source_nodes if n.get("node_type") == "table" or "rapid deflation" in safe_str(n.get("text")) or "motion artifact" in safe_str(n.get("text"))),
            next((n for n in source_nodes if matches_keywords(n, ["table", "error codes"])), None)
        )
        # 2. Inflation Increment Paragraph (Sec 3.2)
        inf_node = next(
            (n for n in source_nodes if ("40 mmhg" in safe_str(n.get("text")) or "increment" in safe_str(n.get("text"))) and n.get("node_type") == "paragraph"),
            next((n for n in source_nodes if matches_keywords(n, ["inflation", "180 mmhg"])), None)
        )
        # 3. Battery Life Specification Paragraph (Sec 2.1.1.1)
        bat_node = next(
            (n for n in source_nodes if ("300" in safe_str(n.get("text")) or "cycles" in safe_str(n.get("text"))) and n.get("node_type") == "paragraph"),
            next((n for n in source_nodes if matches_keywords(n, ["battery life", "alkaline"])), None)
        )
        # 4. General Overview (Sec 1)
        overview_node = next(
            (n for n in source_nodes if safe_str(n.get("section_number")) == "1" or "device overview" in safe_str(n.get("heading"))),
            None
        )

        test_cases = []

        if bat_node:
            test_cases.append({
                "id": f"TC-{len(test_cases)+1:03d}",
                "title": "Verify battery operating life rating and low battery indicator threshold",
                "preconditions": [
                    "Device powered by 4 AA alkaline batteries.",
                    "Variable DC battery simulator connected for threshold calibration.",
                ],
                "steps": [
                    {
                        "step_number": 1,
                        "action": "Lower simulated battery capacity below declared specification cutoff percentage.",
                        "expected_result": "Low battery indicator icon illuminates promptly on LCD display.",
                    },
                    {
                        "step_number": 2,
                        "action": "Execute measurement cycles under nominal load until battery cutoff.",
                        "expected_result": "Total completed cycles meet or exceed rated battery life specification.",
                    },
                ],
                "node_references": [bat_node["node_version_id"]],
                "safety_critical": False,
            })

        if inf_node:
            test_cases.append({
                "id": f"TC-{len(test_cases)+1:03d}",
                "title": "Verify automatic cuff inflation target and stepped incremental pressurization",
                "preconditions": [
                    "Oscillometric testing fixture connected to pneumatic cuff port.",
                    "Device initialized in standard adult measurement mode.",
                ],
                "steps": [
                    {
                        "step_number": 1,
                        "action": "Initiate measurement without detecting pulse at initial target pressure.",
                        "expected_result": "Device inflates to 180 mmHg, then advances in designated stepped mmHg increments.",
                    },
                    {
                        "step_number": 2,
                        "action": "Monitor inflation limit before aborting measurement.",
                        "expected_result": "Device stops inflation and vents automatically before exceeding 299 mmHg cutoff.",
                    },
                ],
                "node_references": [inf_node["node_version_id"]],
                "safety_critical": True,
            })

        if err_node:
            test_cases.append({
                "id": f"TC-{len(test_cases)+1:03d}",
                "title": "Verify overpressure condition rapid deflation timing and safety alarm",
                "preconditions": [
                    "Pneumatic manifold equipped with rapid pressure spike inducer.",
                    "Digital pressure recorder sampling at 100 Hz.",
                ],
                "steps": [
                    {
                        "step_number": 1,
                        "action": "Induce pneumatic overpressure condition during inflation phase.",
                        "expected_result": "Measurement aborts, initiates rapid auto-deflation within specified second limit, and displays E3.",
                    },
                    {
                        "step_number": 2,
                        "action": "Attempt to silence audible alarm during active E3 overpressure state.",
                        "expected_result": "Overpressure alarm cannot be silenced by operator in accordance with safety interlock.",
                    },
                ],
                "node_references": [err_node["node_version_id"]],
                "safety_critical": True,
            })

        if overview_node and len(test_cases) < 5:
            test_cases.append({
                "id": f"TC-{len(test_cases)+1:03d}",
                "title": "Verify power-on self-test initialization and display baseline",
                "preconditions": [
                    "Device assembled according to manufacturer specifications.",
                    "Batteries installed with correct polarity.",
                ],
                "steps": [
                    {
                        "step_number": 1,
                        "action": "Press and hold power button for one second to power on the device.",
                        "expected_result": "System initializes self-test, illuminates display segments, and enters ready mode.",
                    },
                ],
                "node_references": [overview_node["node_version_id"]],
                "safety_critical": False,
            })

        # Ensure between 3 and 5 test cases for any arbitrary context
        while len(test_cases) < 3:
            idx = len(test_cases)
            ref_node = source_nodes[idx % len(source_nodes)] if source_nodes else {"node_version_id": "nv_default"}
            test_cases.append({
                "id": f"TC-{len(test_cases)+1:03d}",
                "title": f"Verify system specification compliance for requirement {idx + 1}",
                "preconditions": [
                    "Device calibrated to factory baseline standards.",
                ],
                "steps": [
                    {
                        "step_number": 1,
                        "action": "Exercise device function against quoted manual requirements.",
                        "expected_result": "Device operates within nominal tolerances without error.",
                    },
                ],
                "node_references": [ref_node["node_version_id"]],
                "safety_critical": False,
            })

        suite = {"test_cases": test_cases[:5]}
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
