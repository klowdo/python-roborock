"""Tests for the CleaningTrait class."""

from typing import cast
from unittest.mock import AsyncMock

import pytest

from roborock.data import SHORT_MODEL_TO_ENUM, CleanSequenceType, SegmentCleanEntry, SegmentCleanRequest
from roborock.data.v1 import CleanRoutes, VacuumModes, WaterModes
from roborock.device_features import DeviceFeatures
from roborock.devices.traits.v1.cleaning import CleaningTrait
from roborock.devices.traits.v1.device_features import DeviceFeaturesTrait
from roborock.devices.traits.v1.status import StatusTrait
from roborock.exceptions import RoborockUnsupportedFeature
from roborock.roborock_typing import RoborockCommand
from tests import mock_data


def _create_cleaning_trait(
    rpc_channel: AsyncMock,
    **feature_overrides: bool,
) -> CleaningTrait:
    """Create a cleaning trait backed by mop-capable V1 features."""
    short_model = mock_data.A27_PRODUCT_DATA["model"].split(".")[-1]
    features = DeviceFeatures.from_feature_flags(
        new_feature_info=0,
        new_feature_info_str="",
        feature_info=[],
        product_nickname=SHORT_MODEL_TO_ENUM[short_model],
    )
    features.is_clean_route_setting_supported = True
    features.is_clean_then_mop_mode_supported = True
    features.is_ctm_with_repeat_supported = True
    for feature_name, value in feature_overrides.items():
        if not hasattr(features, feature_name):
            raise AttributeError(f"Unknown DeviceFeatures override: {feature_name}")
        setattr(features, feature_name, value)

    features_trait = cast(DeviceFeaturesTrait, features)
    status = StatusTrait(features_trait, region="us")
    status.fan_power = 102
    status.water_box_mode = 202
    status.mop_mode = 300

    trait = CleaningTrait(features_trait, status)
    trait._rpc_channel = rpc_channel
    return trait


async def test_clean_segments_emits_verified_payload(mock_rpc_channel: AsyncMock) -> None:
    """The emitted payload matches the shape accepted by real firmware."""
    trait = _create_cleaning_trait(mock_rpc_channel)

    await trait.clean_segments([4], repeat=1, clean_then_mop=True)

    mock_rpc_channel.send_command.assert_called_once_with(
        RoborockCommand.APP_SEGMENT_CLEAN_SUBDIVISION,
        params={
            "auto_dustCollection": 1,
            "auto_dry": 1,
            "is_new_clean": 1,
            "clean_order_mode": 0,
            "repeat": 1,
            "data": [
                {
                    "id": 4,
                    "need_wash": 1,
                    "wash_mode": 2,
                    "segment": 4,
                    "fan_power": 102,
                    "water_box_mode": 202,
                    "mop_mode": 300,
                    "repeat": 1,
                    "seq_type": 1,
                }
            ],
        },
    )


def test_wire_keys_are_not_camelized() -> None:
    """Guard against serializing through as_dict, which would emit seqType.

    RoborockBase.as_dict camelizes every key, so it cannot produce seq_type or
    the mixed-case auto_dustCollection that the firmware requires.
    """
    entry = SegmentCleanEntry(segment=4, seq_type=CleanSequenceType.clean_then_mop)
    entry_params = entry.as_params()
    assert "seq_type" in entry_params
    assert "seqType" not in entry_params

    request_params = SegmentCleanRequest(data=[entry]).as_params()
    assert "auto_dustCollection" in request_params
    assert "autoDustCollection" not in request_params


async def test_repeat_written_at_both_levels(mock_rpc_channel: AsyncMock) -> None:
    """The app writes repeat top level and per room; so do we."""
    trait = _create_cleaning_trait(mock_rpc_channel)

    await trait.clean_segments([4], repeat=2)

    params = mock_rpc_channel.send_command.call_args.kwargs["params"]
    assert params["repeat"] == 2
    assert params["data"][0]["repeat"] == 2


async def test_clean_then_mop_disabled_sends_zero(mock_rpc_channel: AsyncMock) -> None:
    """seq_type is sent explicitly as 0 rather than omitted."""
    trait = _create_cleaning_trait(mock_rpc_channel)

    await trait.clean_segments([4], clean_then_mop=False)

    params = mock_rpc_channel.send_command.call_args.kwargs["params"]
    assert params["data"][0]["seq_type"] == 0


async def test_unknown_mode_code_passes_through(mock_rpc_channel: AsyncMock) -> None:
    """A firmware mode code this library does not model is still forwarded.

    Dropping it makes the firmware reject the whole request.
    """
    trait = _create_cleaning_trait(mock_rpc_channel)
    trait._status.water_box_mode = 235

    await trait.clean_segments([4])

    params = mock_rpc_channel.send_command.call_args.kwargs["params"]
    assert params["data"][0]["water_box_mode"] == 235


async def test_explicit_modes_override_status(mock_rpc_channel: AsyncMock) -> None:
    """Explicit modes win over the values inherited from status."""
    trait = _create_cleaning_trait(mock_rpc_channel)

    await trait.clean_segments(
        [4],
        fan_power=VacuumModes.TURBO,
        water_box_mode=WaterModes.HIGH,
        mop_mode=CleanRoutes.DEEP,
    )

    entry = mock_rpc_channel.send_command.call_args.kwargs["params"]["data"][0]
    assert entry["fan_power"] == VacuumModes.TURBO.code
    assert entry["water_box_mode"] == WaterModes.HIGH.code
    assert entry["mop_mode"] == CleanRoutes.DEEP.code


async def test_mop_mode_omitted_when_unsupported(mock_rpc_channel: AsyncMock) -> None:
    """mop_mode is left out on devices without clean route settings."""
    trait = _create_cleaning_trait(mock_rpc_channel, is_clean_route_setting_supported=False)

    await trait.clean_segments([4])

    assert "mop_mode" not in mock_rpc_channel.send_command.call_args.kwargs["params"]["data"][0]


async def test_clean_then_mop_unsupported_raises(mock_rpc_channel: AsyncMock) -> None:
    """Requesting clean then mop on an incapable device raises and sends nothing."""
    trait = _create_cleaning_trait(mock_rpc_channel, is_clean_then_mop_mode_supported=False)

    with pytest.raises(RoborockUnsupportedFeature):
        await trait.clean_segments([4], clean_then_mop=True)

    mock_rpc_channel.send_command.assert_not_called()


async def test_clean_then_mop_with_repeat_unsupported_raises(mock_rpc_channel: AsyncMock) -> None:
    """Multi pass clean then mop is gated separately from clean then mop."""
    trait = _create_cleaning_trait(mock_rpc_channel, is_ctm_with_repeat_supported=False)

    with pytest.raises(RoborockUnsupportedFeature):
        await trait.clean_segments([4], repeat=2, clean_then_mop=True)

    mock_rpc_channel.send_command.assert_not_called()


async def test_multiple_segments_with_independent_settings(mock_rpc_channel: AsyncMock) -> None:
    """Each room carries its own settings in the advanced form."""
    trait = _create_cleaning_trait(mock_rpc_channel)
    request = SegmentCleanRequest(
        data=[
            SegmentCleanEntry(segment=1, repeat=1, seq_type=CleanSequenceType.simultaneous),
            SegmentCleanEntry(segment=6, repeat=2, seq_type=CleanSequenceType.clean_then_mop),
        ],
        repeat=2,
    )

    await trait.clean_segments_advanced(request)

    entries = mock_rpc_channel.send_command.call_args.kwargs["params"]["data"]
    assert [entry["segment"] for entry in entries] == [1, 6]
    assert [entry["seq_type"] for entry in entries] == [0, 1]
    assert [entry["repeat"] for entry in entries] == [1, 2]


@pytest.mark.parametrize(
    "request_data",
    [
        SegmentCleanRequest(data=[]),
        SegmentCleanRequest(data=[SegmentCleanEntry(segment=4)], repeat=0),
        SegmentCleanRequest(data=[SegmentCleanEntry(segment=4, repeat=0)]),
    ],
)
async def test_invalid_requests_rejected(mock_rpc_channel: AsyncMock, request_data: SegmentCleanRequest) -> None:
    """Empty or non positive requests are rejected before reaching the device."""
    trait = _create_cleaning_trait(mock_rpc_channel)

    with pytest.raises(ValueError):
        await trait.clean_segments_advanced(request_data)

    mock_rpc_channel.send_command.assert_not_called()
