"""Persistence: JSON files for stage outputs and a SQLite cache for LLM results.

Each pipeline run writes one file per stage to outputs/<run_id>/<stage>.json, so any
stage can be rerun or debugged from the previous stage's saved output. LLM results go to
one SQLite file shared by all runs (config.yaml paths.cache_db), so a rerun pays only for
what changed.
"""

import dataclasses
import json
import os
import sqlite3
import types
import typing
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")


def to_jsonable(obj: Any) -> Any:
    """Dataclasses -> dicts, Enums -> their values, recursively."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    return obj


def from_jsonable(data: Any, tp: Any) -> Any:
    """Rebuild a value of type tp (e.g. Document, list[Deviation]) from to_jsonable output."""
    origin = typing.get_origin(tp)
    if origin in (typing.Union, types.UnionType):  # X | None
        if data is None:
            return None
        (inner,) = [a for a in typing.get_args(tp) if a is not type(None)]
        return from_jsonable(data, inner)
    if origin is list:
        (item,) = typing.get_args(tp)
        return [from_jsonable(x, item) for x in data]
    if origin is dict:
        _, val = typing.get_args(tp)
        return {k: from_jsonable(v, val) for k, v in data.items()}
    if isinstance(tp, type) and issubclass(tp, Enum):
        return tp(data)
    if dataclasses.is_dataclass(tp):
        hints = typing.get_type_hints(tp)
        return tp(**{f.name: from_jsonable(data[f.name], hints[f.name])
                     for f in dataclasses.fields(tp) if f.name in data})
    return data


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


class RunStore:
    """Saves and loads stage outputs under <outputs_dir>/<run_id>/."""

    def __init__(self, outputs_dir: str | Path, run_id: str | None = None):
        self.run_id = run_id or new_run_id()
        self.dir = Path(outputs_dir) / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, stage: str) -> Path:
        return self.dir / f"{stage}.json"

    def exists(self, stage: str) -> bool:
        return self.path(stage).exists()

    def save(self, stage: str, obj: Any) -> Path:
        """Write obj to <stage>.json; the temp-file swap avoids half-written files."""
        path = self.path(stage)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(to_jsonable(obj), indent=1, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, path)
        return path

    def load(self, stage: str, tp: type[T] | Any) -> T:
        """Read <stage>.json back as type tp, e.g. store.load("diff", list[Deviation])."""
        data = json.loads(self.path(stage).read_text(encoding="utf-8"))
        return from_jsonable(data, tp)


class ResultCache:
    """Key -> JSON value in a SQLite file. The caller builds keys that change whenever
    the result could (the classifier hashes prompt version, model and input)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.execute("CREATE TABLE IF NOT EXISTS results "
                         "(key TEXT PRIMARY KEY, value TEXT NOT NULL, created TEXT NOT NULL)")
        self._db.commit()

    def get(self, key: str) -> Any | None:
        row = self._db.execute("SELECT value FROM results WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, value: Any) -> None:
        self._db.execute("INSERT OR REPLACE INTO results VALUES (?, ?, ?)",
                         (key, json.dumps(value, ensure_ascii=False),
                          datetime.now().isoformat(timespec="seconds")))
        self._db.commit()

    def __len__(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM results").fetchone()[0]

    def close(self) -> None:
        self._db.close()
