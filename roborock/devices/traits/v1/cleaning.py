"""Trait for starting cleaning runs with per-room settings."""

import logging
from collections.abc import Sequence
from dataclasses import replace

from roborock.data import (
    CleanRoutes,
    CleanSequenceType,
    SegmentCleanEntry,
    SegmentCleanRequest,
    VacuumModes,
    WaterModes,
)
from roborock.exceptions import RoborockUnsupportedFeature
from roborock.roborock_typing import RoborockCommand

from .device_features import DeviceFeaturesTrait
from .status import StatusTrait

_LOGGER = logging.getLogger(__name__)


class CleaningTrait:
    """Start cleaning runs with per-room fan, water, route and pass settings.

    Unlike most traits this holds no state and has no ``refresh()``: the
    underlying commands are write-only and the device exposes no getter for
    ``seq_type``. Current cleaning state lives on the status trait, which the
    caller is responsible for refreshing after starting a run.
    """

    def __init__(self, device_features: DeviceFeaturesTrait, status: StatusTrait) -> None:
        """Initialize the cleaning trait."""
        self._device_features = device_features
        self._status = status
        self._rpc_channel = None

    @property
    def supports_clean_then_mop(self) -> bool:
        """Whether the device can vacuum a room fully, then mop it."""
        return bool(self._device_features.is_clean_then_mop_mode_supported)

    @property
    def supports_clean_then_mop_repeat(self) -> bool:
        """Whether clean-then-mop can be combined with multiple passes."""
        return bool(self._device_features.is_ctm_with_repeat_supported)

    async def clean_segments(
        self,
        segments: Sequence[int],
        *,
        repeat: int = 1,
        clean_then_mop: bool = False,
        fan_power: VacuumModes | int | None = None,
        water_box_mode: WaterModes | int | None = None,
        mop_mode: CleanRoutes | int | None = None,
    ) -> None:
        """Clean the given rooms, applying the same settings to each."""
        seq_type = CleanSequenceType.clean_then_mop if clean_then_mop else CleanSequenceType.simultaneous
        entries = [
            SegmentCleanEntry(
                segment=segment,
                fan_power=fan_power,
                water_box_mode=water_box_mode,
                mop_mode=mop_mode,
                repeat=repeat,
                seq_type=seq_type,
            )
            for segment in segments
        ]
        await self.clean_segments_advanced(SegmentCleanRequest(data=entries, repeat=repeat))

    async def clean_segments_advanced(self, request: SegmentCleanRequest) -> None:
        """Clean rooms with independent settings for each room."""
        if not self._rpc_channel:
            raise ValueError("Device trait in invalid state")
        resolved = self._validate_and_resolve(request)
        await self._rpc_channel.send_command(
            RoborockCommand.APP_SEGMENT_CLEAN_SUBDIVISION,
            params=resolved.as_params(),
        )

    def _validate_and_resolve(self, request: SegmentCleanRequest) -> SegmentCleanRequest:
        """Feature-gate the request and fill unset modes from current status."""
        if not request.data:
            raise ValueError("At least one segment is required")
        if request.repeat < 1:
            raise ValueError("repeat must be at least 1")

        features = self._device_features
        entries = [self._resolve_entry(entry, request.repeat, features) for entry in request.data]
        return replace(request, data=entries)

    def _resolve_entry(
        self,
        entry: SegmentCleanEntry,
        run_repeat: int,
        features: DeviceFeaturesTrait,
    ) -> SegmentCleanEntry:
        """Validate one room entry and inherit unset modes from status."""
        if entry.repeat < 1:
            raise ValueError("repeat must be at least 1")
        if entry.seq_type is CleanSequenceType.clean_then_mop:
            if not features.is_clean_then_mop_mode_supported:
                raise RoborockUnsupportedFeature("Clean then mop is not supported by this device")
            if max(entry.repeat, run_repeat) > 1 and not features.is_ctm_with_repeat_supported:
                raise RoborockUnsupportedFeature(
                    "Clean then mop cannot be combined with multiple passes on this device"
                )
        mop_mode = entry.mop_mode if entry.mop_mode is not None else self._status.mop_mode
        return replace(
            entry,
            fan_power=entry.fan_power if entry.fan_power is not None else self._status.fan_power,
            water_box_mode=(entry.water_box_mode if entry.water_box_mode is not None else self._status.water_box_mode),
            mop_mode=mop_mode if features.is_clean_route_setting_supported else None,
        )
