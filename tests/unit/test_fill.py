import pytest

from applyme.lever.fill import FillConflict, missing_required, verify_overrides
from applyme.models import FieldRef


def test_verify_overrides_raises_on_persistent_mismatch():
    want = {"name": "Ethan Calder", "email": "ethan@applyme.site"}
    got = {"name": "Wrong Name", "email": "ethan@applyme.site"}  # parseResume clobbered name
    with pytest.raises(FillConflict):
        verify_overrides(want, got)


def test_missing_required_lists_empty_required_fields():
    fields = {"name": FieldRef(input_name="name", field_type="text", required=True),
              "org": FieldRef(input_name="org", field_type="text", required=False)}
    values = {"name": "", "org": ""}
    assert missing_required(fields, values) == ["name"]
