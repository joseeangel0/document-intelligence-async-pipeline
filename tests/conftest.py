from pathlib import Path

import pytest

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@pytest.fixture
def sample():
    def _get(name: str) -> str:
        path = SAMPLES / name
        if not path.exists():
            pytest.skip(f"sample {name} missing (run scripts/make_samples.py)")
        return str(path)

    return _get
