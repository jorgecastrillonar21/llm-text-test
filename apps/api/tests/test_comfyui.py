"""ComfyUI adapter boundary: connectivity, workflow loading, job submission."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.application.ports import ImageGenerationRequest
from app.config import ImageProvider, Settings
from app.domain.errors import ImageGenerationError
from app.infrastructure.images.comfyui import ComfyUIImageGenerator
from app.infrastructure.images.simple import DisabledImageGenerator, MockImageGenerator

API_WORKFLOW = {
    "3": {
        "class_type": "KSampler",
        "inputs": {"seed": "{{SEED}}", "steps": 20},
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "{{SCENE_PROMPT}}"},
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "{{NEGATIVE_PROMPT}}"},
    },
}


def write_workflow(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


async def test_disabled_provider_reports_disabled_and_refuses_to_generate() -> None:
    generator = DisabledImageGenerator()
    assert (await generator.status()).state == "disabled"
    with pytest.raises(ImageGenerationError, match="disabled"):
        await generator.generate(ImageGenerationRequest(scene_prompt="x"))


async def test_mock_image_provider_returns_a_job_id() -> None:
    result = await MockImageGenerator().generate(ImageGenerationRequest(scene_prompt="a city"))
    assert result.status == "mocked"
    assert result.job_id.startswith("mock-")


async def test_comfyui_unreachable_is_reported_not_raised() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    generator = ComfyUIImageGenerator(Settings(image_provider=ImageProvider.COMFYUI), client=client)
    status = await generator.status()
    assert status.state == "unreachable"
    assert "Cannot reach ComfyUI" in status.detail
    await client.aclose()


async def test_comfyui_reachable_without_workflow_is_misconfigured() -> None:
    def ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"system": {}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(ok))
    generator = ComfyUIImageGenerator(Settings(image_provider=ImageProvider.COMFYUI), client=client)
    status = await generator.status()
    assert status.state == "misconfigured"
    assert "COMFYUI_WORKFLOW_PATH" in status.detail
    await client.aclose()


async def test_comfyui_ready_when_reachable_and_workflow_present(tmp_path: Path) -> None:
    workflow = write_workflow(tmp_path, API_WORKFLOW)

    def ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"system": {}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(ok))
    generator = ComfyUIImageGenerator(
        Settings(image_provider=ImageProvider.COMFYUI, comfyui_workflow_path=str(workflow)),
        client=client,
    )
    assert (await generator.status()).state == "ready"
    await client.aclose()


async def test_generate_substitutes_placeholders_and_returns_prompt_id(tmp_path: Path) -> None:
    workflow = write_workflow(tmp_path, API_WORKFLOW)
    captured: dict[str, object] = {}

    def submit(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.read()))
        return httpx.Response(200, json={"prompt_id": "abc-123", "number": 1})

    client = httpx.AsyncClient(transport=httpx.MockTransport(submit))
    generator = ComfyUIImageGenerator(
        Settings(image_provider=ImageProvider.COMFYUI, comfyui_workflow_path=str(workflow)),
        client=client,
    )
    result = await generator.generate(
        ImageGenerationRequest(scene_prompt="a ruined tower", negative_prompt="blurry", seed=42)
    )

    assert result.job_id == "abc-123"
    assert result.status == "queued"

    prompt = captured["prompt"]
    assert prompt["6"]["inputs"]["text"] == "a ruined tower"  # type: ignore[index]
    assert prompt["7"]["inputs"]["text"] == "blurry"  # type: ignore[index]
    assert prompt["3"]["inputs"]["seed"] == 42  # type: ignore[index]
    await client.aclose()


async def test_ui_format_workflow_is_rejected_with_actionable_message(tmp_path: Path) -> None:
    """UI exports are the most common ComfyUI mistake; name it explicitly."""
    workflow = write_workflow(tmp_path, {"nodes": [{"id": 1, "type": "KSampler"}], "links": []})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    )
    generator = ComfyUIImageGenerator(
        Settings(image_provider=ImageProvider.COMFYUI, comfyui_workflow_path=str(workflow)),
        client=client,
    )
    with pytest.raises(ImageGenerationError, match="API Format"):
        await generator.generate(ImageGenerationRequest(scene_prompt="x"))
    await client.aclose()


async def test_missing_workflow_file_is_reported(tmp_path: Path) -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    )
    generator = ComfyUIImageGenerator(
        Settings(
            image_provider=ImageProvider.COMFYUI,
            comfyui_workflow_path=str(tmp_path / "nope.json"),
        ),
        client=client,
    )
    assert (await generator.status()).state == "misconfigured"
    with pytest.raises(ImageGenerationError, match="Cannot read workflow"):
        await generator.generate(ImageGenerationRequest(scene_prompt="x"))
    await client.aclose()
