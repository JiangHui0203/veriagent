"""Read local API configuration without executing a shell or exporting secrets."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional

from dotenv import dotenv_values


DEFAULT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def read_environment(
    env_file: Path = DEFAULT_ENV_FILE,
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, str]:
    # No interpolation: a key containing '$' must remain literal. Explicit
    # process environment variables take priority over the local file.
    values = {
        key: value
        for key, value in dotenv_values(env_file, interpolate=False).items()
        if value is not None
    }
    values.update(os.environ if environ is None else environ)
    return values
