import pytest

from tldm.drive import parse_drive_input


class TestParseDriveInput:
    @pytest.mark.parametrize(
        ("input_str", "expected_id"),
        [
            ("1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms", "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"),
            (
                "https://drive.google.com/file/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/view",
                "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms",
            ),
            (
                "https://drive.google.com/file/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/view?usp=sharing",
                "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms",
            ),
            (
                "https://drive.google.com/open?id=1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms",
                "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms",
            ),
        ],
    )
    def test_valid_inputs(self, input_str, expected_id):
        assert parse_drive_input(input_str) == expected_id

    @pytest.mark.parametrize(
        "input_str",
        [
            "short",
            "https://example.com/not-a-drive-url",
            "",
        ],
    )
    def test_invalid_inputs(self, input_str):
        with pytest.raises(ValueError, match="Cannot parse"):
            parse_drive_input(input_str)
