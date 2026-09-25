"""Observability — read-side projections over the durable event stream.

Tracing, metrics, and event surfaces live here. They consume the event log and
project it; they never execute a capability, mutate execution state, or import a
provider. The event stream is the source of truth.
"""
