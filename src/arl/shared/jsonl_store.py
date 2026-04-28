from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

TModel = TypeVar("TModel", bound=BaseModel)


def append_model(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(model.model_dump(mode="json"), ensure_ascii=False))
        handle.write("\n")


def load_models(path: Path, model_type: type[TModel]) -> list[TModel]:
    if not path.exists():
        return []

    items: list[TModel] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            payload = line.strip()
            if not payload:
                continue
            try:
                items.append(model_type.model_validate(json.loads(payload)))
            except (json.JSONDecodeError, ValidationError):
                continue
    return items
