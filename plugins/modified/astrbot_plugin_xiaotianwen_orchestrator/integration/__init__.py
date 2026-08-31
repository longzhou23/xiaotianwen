"""Explicit, side-effect-free adapters used by the P1 isolated test layer."""

from .astrbot_adapter import AstrBotObservationAdapter
from .fake_runtime import FakeAstrBotRuntime, FakeProvider, FakeOneBot
from .observer import (
    CAPTURE_MODES,
    OBSERVATION_SCHEMA_VERSION,
    ObservationAdapter,
    RuntimeObservation,
    RuntimeObservationStore,
)

__all__ = [
    "AstrBotObservationAdapter",
    "CAPTURE_MODES",
    "FakeAstrBotRuntime",
    "FakeOneBot",
    "FakeProvider",
    "OBSERVATION_SCHEMA_VERSION",
    "ObservationAdapter",
    "RuntimeObservation",
    "RuntimeObservationStore",
]
