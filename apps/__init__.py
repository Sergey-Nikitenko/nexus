"""The surface — applications that compose Nexus and expose it.

The API, CLI, worker, and dashboard live here. They compose Nexus from injected
components (never through global state) and observe it through contracts/events.
The execution plane never depends on this layer.
"""
