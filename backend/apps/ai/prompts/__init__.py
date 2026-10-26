"""Prompt registry package (T123)."""

from .registry import (
    PromptNotFound,
    PromptSpec,
    all_prompts,
    get_prompt,
    prompt_metadata,
    register,
    sync_prompt_templates,
)

__all__ = [
    "PromptNotFound",
    "PromptSpec",
    "all_prompts",
    "get_prompt",
    "prompt_metadata",
    "register",
    "sync_prompt_templates",
]
