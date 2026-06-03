"""Property-based tests for Pydantic schemas."""

import pytest
from hypothesis import given, settings as hypothesis_settings
from hypothesis import strategies as st

from app.models.schemas import LogEntryCreate, LogLevel


VALID_SERVICES = [
    "api-gateway",
    "auth-service",
    "payment-service",
    "notification-service",
    "database-proxy",
]


@pytest.mark.property
@given(
    entry=st.builds(
        LogEntryCreate,
        timestamp=st.datetimes(
            min_value=__import__("datetime").datetime(2020, 1, 1),
            max_value=__import__("datetime").datetime(2030, 12, 31),
        ),
        service=st.sampled_from(VALID_SERVICES),
        level=st.sampled_from(list(LogLevel)),
        message=st.text(min_size=1, max_size=200),
    )
)
@hypothesis_settings(max_examples=100)
def test_log_entry_create_round_trip(entry: LogEntryCreate):
    """For all valid LogEntryCreate objects, round-trip through JSON is lossless."""
    json_str = entry.model_dump_json()
    restored = LogEntryCreate.model_validate_json(json_str)
    assert restored == entry
