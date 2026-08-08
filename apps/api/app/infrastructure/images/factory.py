"""Configuration-driven selection of the image provider."""

from __future__ import annotations

from app.application.ports import ImageGeneratorPort
from app.config import ImageProvider, Settings
from app.infrastructure.images.comfyui import ComfyUIImageGenerator
from app.infrastructure.images.simple import DisabledImageGenerator, MockImageGenerator


def build_image_generator(settings: Settings) -> ImageGeneratorPort:
    match settings.image_provider:
        case ImageProvider.DISABLED:
            return DisabledImageGenerator()
        case ImageProvider.MOCK:
            return MockImageGenerator()
        case ImageProvider.COMFYUI:
            return ComfyUIImageGenerator(settings)
