from pathlib import Path

from mypy import api

SAMPLE = Path(__file__).parent / "typing_sample.py"


def test_typed_usage_passes_strict_mypy() -> None:
    # What a strictly typed project using firedantic sees
    stdout, stderr, status = api.run(["--strict", str(SAMPLE)])
    assert status == 0, stdout + stderr
