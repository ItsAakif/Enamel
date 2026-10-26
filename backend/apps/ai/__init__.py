"""AI platform app (T123/T124).

Split into four layers:

* ``providers`` -- a replaceable ``AIProvider`` protocol with a deterministic
  ``MockProvider`` and an ``OpenRouterProvider`` adapter.
* ``prompts`` -- the code-owned prompt registry (bodies in code, metadata in the
  database).
* ``services`` -- invocation pipeline, structured-output validation, cost and
  budget guards, and invocation logging (hashes and metrics only).
* ``tools`` / ``voice`` -- the server-validated tool registry and the simulated
  voice conversation engine (T124).
"""
