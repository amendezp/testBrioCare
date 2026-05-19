"""Load and validate exercise scripts from YAML/JSON files."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from briocare.scripts.schema import ExerciseScript


class ScriptValidationError(Exception):
    """Raised when a script file cannot be parsed or fails validation.

    The message names the file and lists one ``<dotted.loc>: <msg>`` line per
    underlying error.
    """


def _format_validation_error(path: Path, exc: ValidationError) -> str:
    lines = [f"{path}: invalid exercise script"]
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        lines.append(f"  {loc or '<root>'}: {err['msg']}")
    return "\n".join(lines)


def load_script(path: str | Path) -> ExerciseScript:
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScriptValidationError(f"{path}: cannot read file ({exc})") from exc

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ScriptValidationError(f"{path}: YAML parse error: {exc}") from exc

    if not isinstance(data, dict):
        raise ScriptValidationError(f"{path}: top level must be a mapping, got {type(data).__name__}")

    try:
        return ExerciseScript.model_validate(data)
    except ValidationError as exc:
        raise ScriptValidationError(_format_validation_error(path, exc)) from exc
