# ComfyUI integration

Optional. The core game loop does not need images, and `IMAGE_PROVIDER=disabled` is the
default. Nothing here is installed or downloaded automatically — no ComfyUI, no
checkpoints, no LoRAs.

## What is implemented

The adapter (`apps/api/app/infrastructure/images/comfyui.py`) can:

- verify connectivity (`GET /system_stats`)
- load an **API-format** workflow JSON from `COMFYUI_WORKFLOW_PATH`
- substitute scene prompt, negative prompt, and seed into it
- submit it (`POST /prompt`) and capture the returned `prompt_id`
- report typed errors for every failure mode

## What is not

Retrieving the finished image. Submission returns a `prompt_id` with
`status: "queued"`; polling `/history/{prompt_id}` and downloading the output is
Phase 4 (see [../../docs/roadmap.md](../../docs/roadmap.md)).

## Configuration

```dotenv
IMAGE_PROVIDER=comfyui
COMFYUI_BASE_URL=http://127.0.0.1:8188
COMFYUI_WORKFLOW_PATH=ai/comfyui/workflows/your-workflow.api.json
COMFYUI_TIMEOUT_SECONDS=120
```

Relative paths resolve from the repository root. If the path is unset, `GET
/api/v1/ai/status` reports `misconfigured` and the rest of the application runs
normally.

## Exporting a workflow

Workflows **must be in API format**, which is a different export from the one the
ComfyUI menu offers by default:

1. Build your workflow in ComfyUI.
2. Enable **Settings → Enable Dev mode Options**.
3. Use **Save (API Format)**, not plain Save.
4. Put the file in `workflows/` with a `.api.json` suffix.

An API-format file is a flat object keyed by node id:

```json
{
  "3": { "class_type": "KSampler", "inputs": { "seed": 0, "steps": 20 } },
  "6": { "class_type": "CLIPTextEncode", "inputs": { "text": "a ruined tower" } }
}
```

A UI export instead has a top-level `"nodes"` array. The adapter detects that and fails
with a message telling you to re-export — it is the most common mistake here.

## Placeholders

Put these tokens in your workflow where the application should inject values:

| Token | Replaced with |
|---|---|
| `{{SCENE_PROMPT}}` | `VisualCue.scene_prompt` from the turn |
| `{{NEGATIVE_PROMPT}}` | the request's negative prompt |
| `{{SEED}}` | an integer seed (replaces the whole value, not a substring) |

Substitution walks every string in the workflow, so the tokens work in any node's
inputs.

## Where generated assets will live

`data/images/<session_id>/<job_id>.png`, served by the API and referenced from messages.
`data/` is gitignored — generated images are never committed.

## Model files

Checkpoints, LoRAs, and VAEs belong in your ComfyUI installation, not in this
repository. `.safetensors`, `.ckpt`, and `.gguf` are gitignored so a stray copy cannot be
committed.
