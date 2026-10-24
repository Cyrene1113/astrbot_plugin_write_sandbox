"""存量角色卡 v1 → v2 迁移（P1-T6）。

用法：
  python scripts/migrate_v1_v2.py            # dry-run：只出报告不落盘
  python scripts/migrate_v1_v2.py --apply    # 实际迁移（源卡留 .bak_v1 备份）

规则：
  1) story.arc / story.plot_hooks 移出：作品卡 → 对应作品 story_state
     （characters[].arc / foreshadowing 追加去重）；全局卡 → 汇总存档
     plugin_data/<uid>/migration_dropped.json（不丢数据，由用户决定去留）。
  2) 补 persona 骨架与 meta（schema_version=2.0 / canonical_id / inferred=[]）。
  3) 不自动改写任何既有文字；secrets/relations.note 含元视角词的仅在报告中列出。
  4) 幂等：schema_version 已是 2.0 的卡跳过。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.cardops import PLOT_KEYS, detect_meta_sentences, normalize_card  # noqa: E402


def iter_card_files(uid_dir: Path):
    def _cards(d: Path):
        # 备份文件（*.json.bak_v1 等）不参与遍历，防二次迁移
        return [p for p in sorted(d.glob("*.json")) if ".bak" not in p.name]
    yield from _cards(uid_dir / "characters")
    stories = uid_dir / "stories"
    if stories.exists():
        for story in sorted(stories.iterdir()):
            if story.is_dir():
                yield from _cards(story / "characters")


def work_of(uid_dir: Path, card_path: Path) -> str:
    try:
        return card_path.parent.parent.relative_to(uid_dir / "stories").as_posix()
    except ValueError:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写入（默认 dry-run）")
    ap.add_argument("--data-root", default="", help="plugin_data 根目录（默认自动探测）")
    args = ap.parse_args()

    data_root = Path(args.data_root) if args.data_root else Path(
        os.environ.get("ASTRBOT_DATA_DIR", str(ROOT.parent / "data"))) / "plugin_data" / "astrbot_plugin_write_sandbox"
    if not data_root.exists():
        print(f"找不到数据目录：{data_root}（用 --data-root 指定）")
        return 2

    report: list[str] = [f"# 角色卡 v1→v2 迁移报告（{'APPLY' if args.apply else 'DRY-RUN'}）\n"]
    migrated = skipped = 0
    for uid_dir in sorted(p for p in data_root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        dropped: dict[str, list] = {}
        for card_path in iter_card_files(uid_dir):
            try:
                card = json.loads(card_path.read_text(encoding="utf-8"))
            except Exception as e:
                report.append(f"- ✗ {card_path.relative_to(data_root)}：JSON 解析失败（{e}）")
                continue
            meta = card.get("meta") if isinstance(card.get("meta"), dict) else {}
            if meta.get("schema_version") == "2.0":
                skipped += 1
                continue
            work = work_of(uid_dir, card_path)
            new_card, moved = normalize_card(card)
            name = new_card["basic"].get("name", card_path.stem)
            lines = [f"- {'迁移' if args.apply else '计划'} {card_path.relative_to(data_root)}（{name}）"]
            for k in PLOT_KEYS:
                if moved.get(k):
                    lines.append(f"  · story.{k} → " + ("设定书" if work else "migration_dropped.json"))
            # 元视角污染只报告不改
            texts = []
            bg = card.get("background") if isinstance(card.get("background"), dict) else {}
            for r in bg.get("relations") or []:
                if isinstance(r, dict):
                    texts.append(str(r.get("note") or ""))
            texts += [str(s) for s in (bg.get("secrets") or [])]
            joined = "\n".join(t for t in texts if t)
            for h in detect_meta_sentences(joined):
                if h["kind"] == "auto" or "第" in h["line"]:
                    lines.append(f"  ⚠️ 疑似元视角内容（人工决定是否改）：{h['line'][:60]}")
            # persona 缺项提示
            persona = new_card["persona"]
            missing = [k for k, v in persona.items() if not v]
            if missing:
                lines.append(f"  · persona 待补（update 可改）：{ '、'.join(missing) }")
            if not args.apply:
                report.extend(lines)
                migrated += 1
                continue
            # ---- apply ----
            if moved:
                if work:
                    state_file = uid_dir / "stories" / work / "story_state.json"
                    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else \
                        {"meta": {}, "world": {}, "characters": [], "foreshadowing": [], "unresolved": []}
                    chars = state.setdefault("characters", [])
                    entry = next((c for c in chars if isinstance(c, dict) and c.get("name") == name), None)
                    if entry is None:
                        entry = {"name": name}
                        chars.append(entry)
                    if moved.get("arc"):
                        entry["arc"] = moved["arc"]
                    fsh = state.setdefault("foreshadowing", [])
                    known = {str(f.get("desc")) for f in fsh if isinstance(f, dict)}
                    for hook in moved.get("plot_hooks") or []:
                        if str(hook) not in known:
                            fsh.append({"desc": str(hook), "status": "未收"})
                    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
                else:
                    dropped.setdefault(str(card_path.relative_to(uid_dir)), []).extend(
                        [f"{k}: {v}" for k, v in moved.items() if v])
            # 备份名后缀放最后（汐.json.bak_v1）：*.json glob 不会匹配，卡池/选角/审计不受污染
            bak = card_path.with_name(card_path.name + ".bak_v1")
            if not bak.exists():
                bak.write_text(card_path.read_text(encoding="utf-8"), encoding="utf-8")
            card_path.write_text(json.dumps(new_card, ensure_ascii=False, indent=2), encoding="utf-8")
            report.extend(lines)
            migrated += 1
        if dropped and args.apply:
            out = uid_dir / "migration_dropped.json"
            old = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
            old.update(dropped)
            out.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8")
    report.append(f"\n合计：{'迁移/计划' if not args.apply else '迁移'} {migrated} 张，已是 v2 跳过 {skipped} 张。")
    out_text = "\n".join(report)
    print(out_text)
    (ROOT / "scripts" / "migration_report.md").write_text(out_text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
