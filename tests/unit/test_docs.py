"""Documentation must match the code."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_configuration_reference_is_up_to_date() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_config_reference.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
