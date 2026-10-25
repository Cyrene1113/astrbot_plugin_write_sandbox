"""存量约束三层迁移：全局 constraints.json → 作品级 story_constraints.json（P2-T2e）。

用法：
  python scripts/migrate_constraints.py                  # dry-run：只出报告不落盘
  python scripts/migrate_constraints.py --apply          # 实际迁移（constraints.json 留 .bak_p2 备份）
  python scripts/migrate_constraints.py --work 返场 --keys description_focus,rhythm

规则：
  1) 只迁 --keys 指定的键（默认 description_focus,rhythm,length,perspective，
     四键均为《返场》专属剧情编排；scale/banned_words/keep_persona 留全局）。
  2) 键值非空且 != DEFAULT_CONSTRAINTS[key] 才迁；迁入 stories/<work>/story_constraints.json
     （merge 现有 dict；键已存在且值不同 → 报告冲突不覆盖）；stories/<work> 不存在
     → 报错退出，不自动建目录。
  3) 被迁键在全局恢复 DEFAULT_CONSTRAINTS 默认值；--apply 时先备份
     constraints.json → constraints.json.bak_p2（后缀在后，已存在不覆盖）再整 dict 回写。
  4) 幂等：所有 --keys 键均已是默认值 → 报告「已是默认，跳过」，不写任何文件。
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.constants import DEFAULT_CONSTRAINTS  # noqa: E402

DEFAULT_KEYS = "description_focus,rhythm,length,perspective"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写入（默认 dry-run）")
    ap.add_argument("--data-root", default="", help="plugin_data 数据根目录（默认自动探测）")
    ap.add_argument("--work", default="返场", help="迁入的作品名（其 stories 目录必须已存在）")
    ap.add_argument("--keys", default=DEFAULT_KEYS, help="要迁移的约束键（逗号分隔）")
    args = ap.parse_args()

    keys = [k.strip() for k in args.keys.split(",") if k.strip()]
    bad = [k for k in keys if k not in DEFAULT_CONSTRAINTS]
    if bad:
        print(f"未知约束键：{'、'.join(bad)}。可用：{', '.join(DEFAULT_CONSTRAINTS)}")
        return 2

    data_root = Path(args.data_root) if args.data_root else Path(
        os.environ.get("ASTRBOT_DATA_DIR", str(ROOT.parent / "data"))) / "plugin_data" / "astrbot_plugin_write_sandbox"
    if not data_root.exists():
        # 回退：插件源码位于 <data>/plugins/<name> 时，data 根为 ROOT.parent.parent
        alt = ROOT.parent.parent / "plugin_data" / "astrbot_plugin_write_sandbox"
        if alt.exists():
            data_root = alt
        else:
            print(f"找不到数据目录：{data_root}（用 --data-root 指定）")
            return 2

    report: list[str] = [
        f"# 约束三层迁移报告（{'APPLY' if args.apply else 'DRY-RUN'}）",
        f"数据目录：{data_root}",
        f"目标作品：《{args.work}》｜迁移键：{', '.join(keys)}\n",
    ]
    migrated = skipped = 0
    for uid_dir in sorted(p for p in data_root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        cf = uid_dir / "constraints.json"
        if not cf.exists():
            report.append(f"- {uid_dir.name}：无 constraints.json，跳过")
            continue
        try:
            g = json.loads(cf.read_text(encoding="utf-8"))
            if not isinstance(g, dict):
                raise ValueError("顶层不是 dict")
        except Exception as e:
            report.append(f"- ✗ {uid_dir.name}：constraints.json 解析失败（{e}）")
            continue

        work_dir = uid_dir / "stories" / args.work
        if not work_dir.is_dir():
            report.append(f"- ✗ {uid_dir.name}：stories/{args.work} 不存在（不自动建目录），退出")
            print("\n".join(report))
            return 2
        sf = work_dir / "story_constraints.json"
        existing: dict[str, Any] = {}
        if sf.exists():
            try:
                data = json.loads(sf.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    existing = data
                else:
                    raise ValueError("顶层不是 dict")
            except Exception as e:
                report.append(f"- ✗ {uid_dir.name}：story_constraints.json 解析失败（{e}），退出")
                print("\n".join(report))
                return 2

        lines = [f"- {'迁移' if args.apply else '计划'} {uid_dir.name} → 《{args.work}》"]
        to_migrate: dict[str, Any] = {}
        for k in keys:
            cur = g.get(k)
            if cur is None:
                lines.append(f"  · {k}：全局未设置，跳过")
            elif cur == "" or cur == []:
                lines.append(f"  · {k}：全局为空，跳过")
            elif cur == DEFAULT_CONSTRAINTS[k]:
                lines.append(f"  · {k}：已是默认，跳过")
            else:
                to_migrate[k] = cur
        if not to_migrate:
            lines.append("  · 所有键均已是默认，跳过（不写任何文件）")
            report.extend(lines)
            skipped += 1
            continue

        writable = {k: v for k, v in to_migrate.items() if k not in existing}
        same_keys = [k for k in to_migrate if k in existing and existing[k] == to_migrate[k]]
        conflict_keys = [k for k in to_migrate if k in existing and existing[k] != to_migrate[k]]
        for k in same_keys:
            lines.append(f"  · {k}：作品级已有同值，仅重置全局为默认")
        for k in conflict_keys:
            lines.append(
                f"  ⚠️ {k}：作品级已有不同值（不覆盖）｜作品级：{str(existing[k])[:60]}｜全局待迁：{str(to_migrate[k])[:60]}"
            )
        for k, v in writable.items():
            lines.append(f"  · {k} → story_constraints.json（全局恢复默认）：{str(v)[:60]}")
        if not writable and not same_keys:
            lines.append("  · 无可迁移项（全部冲突），不写任何文件")
            report.extend(lines)
            skipped += 1
            continue
        if not args.apply:
            report.extend(lines)
            migrated += 1
            continue
        # ---- apply ----
        bak = cf.with_name(cf.name + ".bak_p2")
        if not bak.exists():
            bak.write_text(cf.read_text(encoding="utf-8"), encoding="utf-8")
        if writable:
            merged = dict(existing)
            merged.update(writable)
            sf.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        # 被迁键（含作品级已有同值的）全局恢复默认；冲突键不动，保数据
        for k in list(writable) + same_keys:
            g[k] = copy.deepcopy(DEFAULT_CONSTRAINTS[k])
        cf.write_text(json.dumps(g, ensure_ascii=False, indent=2), encoding="utf-8")
        report.extend(lines)
        migrated += 1
    report.append(
        f"\n合计：{'迁移/计划' if not args.apply else '迁移'} {migrated} 个沙盒，已是默认跳过 {skipped} 个。"
        + ("（dry-run：未写任何文件）" if not args.apply else "")
    )
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
