#!/usr/bin/env python3
"""
自动化泛化基准实验。

- e2e：raw.h5ad → 完整 Graph A + Graph B（Agent 自主预处理），markers 写到 data/benchmark/<ds>/markers.json。
- full / no_sc / no_cr / no_boost / no_sc_no_cr：跳过 Graph A，使用 **e2e 产生的 markers.json**，仅消融 Graph B。
- gold_* / baseline：跳过 Graph A，使用 prepare_datasets 生成的 gold_markers.json。

前置: `uv run python scripts/benchmark/prepare_datasets.py`（生成 raw.h5ad 与 gold_markers.json）

示例:
  uv run python scripts/benchmark/run_experiments.py --datasets pbmc3k --dry-run
  uv run python scripts/benchmark/run_experiments.py --datasets pbmc3k
  uv run python scripts/benchmark/run_experiments.py --datasets pbmc3k --skip-e2e
  uv run python scripts/benchmark/run_experiments.py --datasets pbmc3k --repeats 3  # 每条件重复 3 次
  # 两阶段：先各数据集串行 e2e（Graph A 用 Docker，勿并发），再仅 Graph B 多数据集并行：
  uv run python scripts/benchmark/run_experiments.py --only-e2e --datasets paul15 tabula_muris_lung
  uv run python scripts/benchmark/run_experiments.py --skip-e2e --datasets paul15 tabula_muris_lung --jobs 4 --repeats 3
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_BENCHMARK = PROJECT_ROOT / "data" / "benchmark"
EXPERIMENT_ROOT = PROJECT_ROOT / "experiment_records" / "benchmark"
MAIN_PY = PROJECT_ROOT / "src" / "omnicell_agent" / "main.py"
BASELINE_PY = PROJECT_ROOT / "scripts" / "benchmark" / "run_baseline_annotation.py"

DATA_DIR = PROJECT_ROOT / "data"

DEFAULT_DATASETS = ["pbmc3k", "baron_pancreas", "paul15", "tabula_muris_lung", "spatial_breast"]

# Graph B 消融：金标准 markers + 各开关
GOLD_CONDITIONS = [
    ("gold_full", {"ENABLE_SELF_CONSISTENCY": "1", "ENABLE_CONSISTENCY_REVIEWER": "1", "ENABLE_BOOST": "1"}),
    ("gold_no_sc", {"ENABLE_SELF_CONSISTENCY": "0", "ENABLE_CONSISTENCY_REVIEWER": "1", "ENABLE_BOOST": "1"}),
    ("gold_no_cr", {"ENABLE_SELF_CONSISTENCY": "1", "ENABLE_CONSISTENCY_REVIEWER": "0", "ENABLE_BOOST": "1"}),
    ("gold_no_boost", {"ENABLE_SELF_CONSISTENCY": "1", "ENABLE_CONSISTENCY_REVIEWER": "1", "ENABLE_BOOST": "0"}),
    ("gold_no_sc_no_cr", {"ENABLE_SELF_CONSISTENCY": "0", "ENABLE_CONSISTENCY_REVIEWER": "0", "ENABLE_BOOST": "1"}),
]

E2E_ENV = {"ENABLE_SELF_CONSISTENCY": "1", "ENABLE_CONSISTENCY_REVIEWER": "1", "ENABLE_BOOST": "1"}

# Agent 产出的 markers + Graph B 消融（与 GOLD_CONDITIONS 开关对齐，便于与 gold_full 等对照）
AGENT_MARKER_CONDITIONS = [
    ("full", {"ENABLE_SELF_CONSISTENCY": "1", "ENABLE_CONSISTENCY_REVIEWER": "1", "ENABLE_BOOST": "1"}),
    ("no_sc", {"ENABLE_SELF_CONSISTENCY": "0", "ENABLE_CONSISTENCY_REVIEWER": "1", "ENABLE_BOOST": "1"}),
    ("no_cr", {"ENABLE_SELF_CONSISTENCY": "1", "ENABLE_CONSISTENCY_REVIEWER": "0", "ENABLE_BOOST": "1"}),
    ("no_boost", {"ENABLE_SELF_CONSISTENCY": "1", "ENABLE_CONSISTENCY_REVIEWER": "1", "ENABLE_BOOST": "0"}),
    ("no_sc_no_cr", {"ENABLE_SELF_CONSISTENCY": "0", "ENABLE_CONSISTENCY_REVIEWER": "0", "ENABLE_BOOST": "1"}),
]


def _env(extra: dict[str, str]) -> dict[str, str]:
    e = os.environ.copy()
    e.update(extra)
    return e


def _run(cmd: list[str], env: dict[str, str], dry: bool) -> int:
    if dry:
        print("DRY:", " ".join(cmd))
        return 0
    r = subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=env)
    return r.returncode


def _run_graph_b_condition(
    cond: str,
    extra_env: dict[str, str],
    markers_path: Path,
    raw_h5ad: Path,
    instruction: str,
    species: str,
    tissue: str,
    meta_path: Path,
    ds: str,
    run_label: str | None,
    dry: bool,
) -> int:
    """执行一个 Graph B 消融条件（通用逻辑，agent/gold 共用）。"""
    if run_label:
        od = EXPERIMENT_ROOT / ds / cond / run_label
    else:
        od = EXPERIMENT_ROOT / ds / cond
    od.mkdir(parents=True, exist_ok=True)

    env = _env(extra_env)
    cmd = [
        sys.executable, str(MAIN_PY),
        "--data", str(raw_h5ad),
        "--instruction", instruction,
        "--override-species", species,
        "--override-tissue", tissue,
        "--skip-graph-a",
        "--markers-json", str(markers_path),
        "--annotation-dump", str(od / "annotation_result.json"),
    ]
    rc = _run(cmd, env, dry)
    if rc != 0:
        return rc
    if not dry:
        mm = json.loads(meta_path.read_text(encoding="utf-8"))
        mm["condition"] = cond
        if run_label:
            mm["run"] = run_label
        (od / "meta.json").write_text(
            json.dumps(mm, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0


def _run_baseline(
    gold_markers: Path,
    species: str,
    tissue: str,
    meta_path: Path,
    ds: str,
    run_label: str | None,
    dry: bool,
) -> int:
    """执行 baseline 条件。"""
    if run_label:
        bo = EXPERIMENT_ROOT / ds / "baseline" / run_label
    else:
        bo = EXPERIMENT_ROOT / ds / "baseline"
    bo.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, str(BASELINE_PY),
        "--markers-json", str(gold_markers),
        "--output", str(bo / "annotation_result.json"),
        "--species", species,
        "--tissue", tissue,
    ]
    rc = _run(cmd, _env({}), dry)
    if not dry and rc == 0:
        shutil.copy2(meta_path, bo / "meta.json")
        with open(bo / "meta.json", encoding="utf-8") as f:
            bm = json.load(f)
        bm["condition"] = "baseline"
        if run_label:
            bm["run"] = run_label
        with open(bo / "meta.json", "w", encoding="utf-8") as f:
            json.dump(bm, f, ensure_ascii=False, indent=2)
    return rc


def run_dataset(
    ds: str,
    dry: bool,
    skip_e2e: bool,
    repeats: int,
    *,
    only_e2e: bool = False,
    only_gold: bool = False,
) -> int:
    """仅 ``only_e2e=True`` 时跑完 e2e 即返回，用于与各数据集串行占用 Docker；Graph B 另用 ``--skip-e2e`` 阶段。"""
    ds_dir = DATA_BENCHMARK / ds
    meta_path = ds_dir / "meta.json"
    raw_h5ad = ds_dir / "raw.h5ad"
    gold_markers = ds_dir / "gold_markers.json"
    agent_markers = ds_dir / "markers.json"

    if not meta_path.is_file() or not raw_h5ad.is_file():
        print(f"Skip {ds}: missing meta.json or raw.h5ad under {ds_dir}", file=sys.stderr)
        return 1
    if not gold_markers.is_file():
        print(f"Skip {ds}: missing gold_markers.json — run prepare_datasets.py", file=sys.stderr)
        return 1

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    instruction = meta["instruction"]
    species = meta.get("species", "Human")
    tissue = meta.get("tissue", "Unknown")

    markers_rel = f"benchmark/{ds}/markers.json"

    # 多轮实验标签：repeats=1 时不加子目录（向后兼容），>1 时加 run_1/run_2/...
    def run_labels() -> list[str | None]:
        if repeats <= 1:
            return [None]
        return [f"run_{i}" for i in range(1, repeats + 1)]

    # 1) e2e：完整 Graph A + B，仅跑 1 次（不受 repeats 控制，因 markers.json 只需要一份）
    if not skip_e2e and not only_gold:
        e2e_out = EXPERIMENT_ROOT / ds / "e2e"
        e2e_out.mkdir(parents=True, exist_ok=True)
        env = _env(E2E_ENV)
        cmd = [
            sys.executable, str(MAIN_PY),
            "--data", str(raw_h5ad),
            "--instruction", instruction,
            "--override-species", species,
            "--override-tissue", tissue,
            "--override-out-markers", markers_rel,
            "--annotation-dump", str(e2e_out / "annotation_result.json"),
        ]
        rc = _run(cmd, env, dry)
        if rc != 0:
            return rc
        if not dry:
            if not agent_markers.is_file():
                m_legacy = DATA_DIR / "markers.json"
                if m_legacy.is_file():
                    shutil.copy2(m_legacy, agent_markers)
                else:
                    print(
                        f"Warning: expected {agent_markers} after e2e",
                        file=sys.stderr,
                    )
            mm = json.loads(meta_path.read_text(encoding="utf-8"))
            mm["condition"] = "e2e"
            (e2e_out / "meta.json").write_text(
                json.dumps(mm, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        if only_e2e:
            return 0

    # 2) Agent markers 条件（full / no_sc / no_cr / no_boost / no_sc_no_cr）
    if only_gold:
        pass
    elif agent_markers.is_file():
        for cond, extra in AGENT_MARKER_CONDITIONS:
            for rl in run_labels():
                rc = _run_graph_b_condition(
                    cond, extra, agent_markers, raw_h5ad,
                    instruction, species, tissue, meta_path, ds, rl, dry,
                )
                if rc != 0:
                    return rc
    elif not dry and not skip_e2e:
        print(
            f"Note: {agent_markers} missing — skip agent marker conditions "
            f"(run e2e first or place markers.json).",
            file=sys.stderr,
        )

    # 3) Gold markers 条件
    for cond, extra in GOLD_CONDITIONS:
        for rl in run_labels():
            rc = _run_graph_b_condition(
                cond, extra, gold_markers, raw_h5ad,
                instruction, species, tissue, meta_path, ds, rl, dry,
            )
            if rc != 0:
                return rc

    # 4) Baseline
    for rl in run_labels():
        rc = _run_baseline(gold_markers, species, tissue, meta_path, ds, rl, dry)
        if rc != 0:
            return rc

    return 0


def _run_dataset_subprocess(payload: tuple) -> tuple[str, int]:
    """
    子进程入口（仅 Graph B 路径）：须为模块级函数以便 ProcessPoolExecutor 可 pickle。
    payload: (ds, dry, repeats, only_gold)
    """
    ds, dry, repeats, only_gold = payload
    return ds, run_dataset(
        ds,
        dry,
        skip_e2e=True,
        repeats=repeats,
        only_e2e=False,
        only_gold=only_gold,
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "基准实验。e2e 使用 Graph A（Docker）应串行；"
            "Graph B 仅 LLM 时可用 --skip-e2e --jobs N 多数据集并行。"
        ),
    )
    ap.add_argument("--datasets", nargs="*", default=DEFAULT_DATASETS)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--skip-e2e",
        action="store_true",
        help="跳过端到端 e2e（不跑 Graph A）；仍会跑 gold_* 与 baseline（需 gold_markers.json）",
    )
    ap.add_argument(
        "--only-e2e",
        action="store_true",
        help="仅跑 e2e（Graph A+B 各一次），不跑 full/no_*/gold_*/baseline；与 --skip-e2e 互斥",
    )
    ap.add_argument(
        "--only-gold",
        action="store_true",
        help="仅跑 gold_* 与 baseline，不跑 e2e 或 Agent-marker full/no_*；用于 gold_markers 修复后的最小重跑",
    )
    ap.add_argument(
        "--jobs",
        type=int,
        default=1,
        metavar="N",
        help=(
            "并行数据集数量（仅在与 --skip-e2e 联用时生效，多进程跑各数据集的 Graph B）。"
            "默认 1 = 串行。e2e 阶段切勿使用多进程（共用 Docker kernel）。"
        ),
    )
    ap.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="每个条件重复运行次数（默认 1 = 向后兼容单次）；>1 时结果存入 condition/run_N 子目录",
    )
    ap.add_argument(
        "--skip-full",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = ap.parse_args()
    skip_e2e = args.skip_e2e or args.skip_full
    only_e2e = args.only_e2e
    only_gold = args.only_gold
    if only_gold:
        skip_e2e = True

    if only_e2e and skip_e2e:
        print(
            "Error: --only-e2e 与 --skip-e2e 不可同时使用",
            file=sys.stderr,
        )
        sys.exit(2)
    if only_e2e and only_gold:
        print(
            "Error: --only-e2e 与 --only-gold 不可同时使用",
            file=sys.stderr,
        )
        sys.exit(2)
    if args.jobs > 1 and not skip_e2e:
        print(
            "Error: --jobs > 1 仅支持与 --skip-e2e 联用（仅并行 Graph B）。"
            "请先对各数据集跑完 e2e，再执行本组合。",
            file=sys.stderr,
        )
        sys.exit(2)
    if args.jobs > 1 and only_e2e:
        print(
            "Error: --only-e2e 不能与 --jobs > 1 联用（e2e 必须串行）",
            file=sys.stderr,
        )
        sys.exit(2)
    if args.jobs < 1:
        print("Error: --jobs 须 >= 1", file=sys.stderr)
        sys.exit(2)

    EXPERIMENT_ROOT.mkdir(parents=True, exist_ok=True)
    code = 0

    if args.jobs > 1 and skip_e2e:
        workers = min(args.jobs, len(args.datasets))
        print(
            f"Graph B only: running {len(args.datasets)} dataset(s) "
            f"with up to {workers} parallel worker(s) …",
            file=sys.stderr,
        )
        payloads = [(ds, args.dry_run, args.repeats, only_gold) for ds in args.datasets]
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_run_dataset_subprocess, p): p[0] for p in payloads}
            for fut in as_completed(futures):
                ds_name = futures[fut]
                try:
                    ds_done, rc = fut.result()
                    if rc != 0:
                        code = 1
                        print(f"Failed {ds_done} (exit {rc})", file=sys.stderr)
                except Exception as e:
                    code = 1
                    print(f"Failed {ds_name}: {e}", file=sys.stderr)
    else:
        for ds in args.datasets:
            code = (
                run_dataset(
                    ds,
                    args.dry_run,
                    skip_e2e,
                    args.repeats,
                    only_e2e=only_e2e,
                    only_gold=only_gold,
                )
                or code
            )
    sys.exit(code)


if __name__ == "__main__":
    main()
