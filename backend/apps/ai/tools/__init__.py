"""Tool registry package (T124).

Kept import-light on purpose: ``registry.get_registry()`` imports the concrete
tool modules lazily, so importing ``apps.ai.tools`` never drags in the whole
domain layer.
"""

__all__: list[str] = []
