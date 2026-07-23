#!/usr/bin/env python3
"""离线生成或校验 API v1 与事件 v1 公共契约快照。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_SOURCE = REPOSITORY_ROOT / "backend" / "src"
if str(BACKEND_SOURCE) not in sys.path:
    sys.path.insert(0, str(BACKEND_SOURCE))

from omnicell_agent.api.app import create_app  # noqa: E402
from omnicell_agent.runs.events import (  # noqa: E402
    PERSISTED_EVENT_ADAPTER,
    TRANSIENT_EVENT_ADAPTER,
)


TARGETS = {
    REPOSITORY_ROOT / "contracts" / "openapi" / "v1.json": lambda: create_app().openapi(),
    REPOSITORY_ROOT
    / "contracts"
    / "events"
    / "v1.schema.json": lambda: {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "OmniCell Agent Event Contract v1",
        # Event snapshot 是序列化后的 wire contract，而不是接受默认值的
        # Python 构造输入 contract。
        "persisted": PERSISTED_EVENT_ADAPTER.json_schema(mode="serialization"),
        "transient": TRANSIENT_EVENT_ADAPTER.json_schema(mode="serialization"),
    },
}


def _render(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验已落盘快照，不修改文件",
    )
    args = parser.parse_args()
    stale: list[Path] = []
    for path, build in TARGETS.items():
        expected = _render(build())
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != expected:
                stale.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(expected, encoding="utf-8")
    if stale:
        for path in stale:
            print(f"契约快照需要重新生成：{path.relative_to(REPOSITORY_ROOT)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
