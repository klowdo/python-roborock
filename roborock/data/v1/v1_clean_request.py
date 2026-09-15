"""Request models for per-room ("subdivision") cleaning runs.

These models describe a single cleaning *run*, not persisted device settings.
``seq_type`` in particular has no getter or setter on the device: it is only
meaningful as part of an ``app_segment_clean_subdivision`` request, and
``set_clean_motor_mode`` silently ignores it.

The wire format uses keys that :meth:`RoborockBase.as_dict` cannot produce; it
camelizes every key, so ``seq_type`` would be emitted as ``seqType``, and
``auto_dustCollection`` is mixed case and unreachable by any automatic
transform. These models therefore serialize through an explicit ``as_params``,
following the same pattern as :meth:`RoborockBaseTimer.as_list`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..code_mappings import RoborockEnum, RoborockModeEnum
from ..containers import RoborockBase
from .v1_clean_modes import CleanRoutes, VacuumModes, WaterModes

DEVICE_CHOSEN_CLEAN_ORDER = 0
"""The only verified ``clean_order_mode``; the app forces it in FCC regions."""

DEFAULT_WASH_MODE = 2
"""The ``wash_mode`` the app sends for a normal cleaning run."""


def _mode_code(value: RoborockModeEnum | int | None) -> int | None:
    """Return the firmware code for a mode given as an enum member or raw code.

    Raw codes pass through unchanged so that a mode this library does not model
    yet is still forwarded to the device rather than silently dropped.
    """
    if value is None:
        return None
    if isinstance(value, RoborockModeEnum):
        return value.code
    return int(value)


class CleanSequenceType(RoborockEnum):
    """Whether a pass vacuums and mops together, or vacuums then mops."""

    unknown = -1
    simultaneous = 0
    clean_then_mop = 1


@dataclass
class SegmentCleanEntry(RoborockBase):
    """Per-room settings for one room in a segment cleaning run."""

    segment: int
    """Segment (room) id, as returned by the rooms trait.

    Sent twice on the wire, as both ``id`` and ``segment``; the firmware
    rejects the request if either is missing.
    """

    fan_power: VacuumModes | int | None = None
    """Suction mode, as an enum member or a raw firmware code."""

    water_box_mode: WaterModes | int | None = None
    """Water flow mode, as an enum member or a raw firmware code."""

    mop_mode: CleanRoutes | int | None = None
    """Mop route, as an enum member or a raw firmware code."""

    repeat: int = 1
    """Number of passes over this room."""

    seq_type: CleanSequenceType = CleanSequenceType.simultaneous
    """Whether to vacuum this room fully before mopping it."""

    need_wash: bool = True
    """Whether the mop is washed while cleaning this room."""

    wash_mode: int = DEFAULT_WASH_MODE

    mop_type: int | None = None
    """Mop module selection; omitted when unset."""

    mop_power: int | None = None
    """Mop vibration power; omitted when unset."""

    def as_params(self) -> dict[str, int]:
        """Build the wire representation of this entry.

        Hand-built rather than derived from ``as_dict`` so that ``seq_type``
        is not camelized into ``seqType``.
        """
        params: dict[str, int] = {
            "id": self.segment,
            "need_wash": int(self.need_wash),
            "wash_mode": self.wash_mode,
            "segment": self.segment,
        }
        if (fan_power := _mode_code(self.fan_power)) is not None:
            params["fan_power"] = fan_power
        if (water_box_mode := _mode_code(self.water_box_mode)) is not None:
            params["water_box_mode"] = water_box_mode
        if (mop_mode := _mode_code(self.mop_mode)) is not None:
            params["mop_mode"] = mop_mode
        params["repeat"] = self.repeat
        params["seq_type"] = int(self.seq_type)
        if self.mop_type is not None:
            params["mop_type"] = self.mop_type
        if self.mop_power is not None:
            params["mop_power"] = self.mop_power
        return params


@dataclass
class SegmentCleanRequest(RoborockBase):
    """A complete ``app_segment_clean_subdivision`` request."""

    data: list[SegmentCleanEntry] = field(default_factory=list)
    """Per-room settings, one entry per room to clean."""

    repeat: int = 1
    """Run-level pass count, written alongside the per-room value."""

    auto_dust_collection: bool = True
    auto_dry: bool = True
    is_new_clean: bool = True

    def as_params(self) -> dict[str, Any]:
        """Build the wire payload.

        Hand-built rather than derived from ``as_dict``, which cannot emit the
        mixed-case ``auto_dustCollection`` key.
        """
        return {
            "auto_dustCollection": int(self.auto_dust_collection),
            "auto_dry": int(self.auto_dry),
            "is_new_clean": int(self.is_new_clean),
            "clean_order_mode": DEVICE_CHOSEN_CLEAN_ORDER,
            "repeat": self.repeat,
            "data": [entry.as_params() for entry in self.data],
        }
