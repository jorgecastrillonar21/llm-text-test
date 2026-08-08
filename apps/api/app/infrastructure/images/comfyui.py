"""ComfyUI image provider.

Scope is deliberately narrow for this phase: verify connectivity, load an
API-format workflow, submit it, and return the prompt id ComfyUI assigns.
Polling history and downloading the finished image is Phase 4 -- see
docs/roadmap.md and ai/comfyui/README.md.
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from app.application.ports import ImageGenerationRequest, ImageGenerationResult, ProviderStatus
from app.config import Settings
from app.domain.errors import ImageGenerationError

logger = logging.getLogger(__name__)

# Placeholders a workflow may use; substituted before submission.
PROMPT_PLACEHOLDER = "{{SCENE_PROMPT}}"
NEGATIVE_PLACEHOLDER = "{{NEGATIVE_PROMPT}}"
SEED_PLACEHOLDER = "{{SEED}}"


class ComfyUIImageGenerator:
    name = "comfyui"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._base_url = settings.comfyui_base_url.rstrip("/")
        self._timeout = settings.comfyui_timeout_seconds
        self._workflow_path = settings.workflow_path
        self._client = client

    async def status(self) -> ProviderStatus:
        extra = {
            "base_url": self._base_url,
            "workflow": str(self._workflow_path) if self._workflow_path else "unconfigured",
        }
        try:
            await self._request("GET", "/system_stats", None)
        except ImageGenerationError as exc:
            return ProviderStatus(
                provider=self.name, state="unreachable", detail=str(exc), extra=extra
            )

        if self._workflow_path is None:
            return ProviderStatus(
                provider=self.name,
                state="misconfigured",
                detail=(
                    "ComfyUI is reachable but COMFYUI_WORKFLOW_PATH is not set. "
                    "Export a workflow in API format -- see ai/comfyui/README.md."
                ),
                extra=extra,
            )
        if not self._workflow_path.is_file():
            return ProviderStatus(
                provider=self.name,
                state="misconfigured",
                detail=f"Workflow file not found: {self._workflow_path}",
                extra=extra,
            )

        return ProviderStatus(
            provider=self.name,
            state="ready",
            detail=f"ComfyUI reachable at {self._base_url}.",
            extra=extra,
        )

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        workflow = self._load_workflow()
        prepared = _substitute(workflow, request)
        body = await self._request("POST", "/prompt", {"prompt": prepared})

        prompt_id = body.get("prompt_id")
        if not isinstance(prompt_id, str):
            raise ImageGenerationError(
                f"ComfyUI accepted the job but returned no prompt_id: {body}",
                provider=self.name,
            )
        return ImageGenerationResult(
            job_id=prompt_id,
            provider=self.name,
            status="queued",
            detail="Submitted to ComfyUI. Result retrieval is not implemented yet.",
        )

    def _load_workflow(self) -> dict[str, Any]:
        if self._workflow_path is None:
            raise ImageGenerationError("COMFYUI_WORKFLOW_PATH is not set.", provider=self.name)
        try:
            raw = Path(self._workflow_path).read_text(encoding="utf-8")
        except OSError as exc:
            raise ImageGenerationError(
                f"Cannot read workflow {self._workflow_path}: {exc}", provider=self.name
            ) from exc

        try:
            workflow = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ImageGenerationError(
                f"Workflow {self._workflow_path} is not valid JSON: {exc}", provider=self.name
            ) from exc

        if not isinstance(workflow, dict):
            raise ImageGenerationError(
                "Workflow root must be a JSON object. Export it in API format, not UI format.",
                provider=self.name,
            )
        # API-format workflows are {node_id: {class_type, inputs}}; UI exports have "nodes".
        if "nodes" in workflow and "class_type" not in str(workflow)[:2000]:
            raise ImageGenerationError(
                "Workflow looks like a UI export. Enable 'Save (API Format)' in ComfyUI.",
                provider=self.name,
            )
        return workflow

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        try:
            if self._client is not None:
                response = await self._client.request(method, url, json=payload)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.request(method, url, json=payload)
        except httpx.ConnectError as exc:
            raise ImageGenerationError(
                f"Cannot reach ComfyUI at {self._base_url}. Is it running?",
                provider=self.name,
            ) from exc
        except httpx.TimeoutException as exc:
            raise ImageGenerationError(
                f"ComfyUI timed out after {self._timeout:.0f}s.", provider=self.name
            ) from exc

        if response.status_code >= 400:
            raise ImageGenerationError(
                f"ComfyUI returned HTTP {response.status_code}: {response.text[:300]}",
                provider=self.name,
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise ImageGenerationError(
                f"ComfyUI returned a non-JSON body: {response.text[:200]}", provider=self.name
            ) from exc
        return body if isinstance(body, dict) else {"result": body}


def _substitute(workflow: dict[str, Any], request: ImageGenerationRequest) -> dict[str, Any]:
    """Replace placeholder tokens in workflow input values."""
    prepared = copy.deepcopy(workflow)

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {key: walk(value) for key, value in node.items()}
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, str):
            if node == SEED_PLACEHOLDER:
                return request.seed if request.seed is not None else 0
            return node.replace(PROMPT_PLACEHOLDER, request.scene_prompt).replace(
                NEGATIVE_PLACEHOLDER, request.negative_prompt
            )
        return node

    return walk(prepared)
