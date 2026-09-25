"""写作沙盒 · cleanup 域（Mixin 拆分自 core.py，2026-09-21）。"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .constants import CHAPTERS_DIR_NAME


class CleanupMixin:
    """cleanup 域方法。"""

    def list_stale_files(self, days: int = 30) -> list[dict[str, Any]]:
        """列出 outputs/ 和各作品 chapters/ 下超过 days 天未修改的旧文件。"""
        cutoff = time.time() - max(1, days) * 86400
        results: list[dict[str, Any]] = []

        def _scan(root: Path, label: str) -> None:
            if not root.exists():
                return
            for p in root.rglob("*.txt"):
                try:
                    st = p.stat()
                    if st.st_mtime < cutoff:
                        results.append({
                            "path": str(p),
                            "name": p.name,
                            "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                            "size": st.st_size,
                            "label": label,
                        })
                except Exception:
                    continue

        _scan(self.outputs_dir, "短篇输出")
        if self.stories_dir.exists():
            for wd in self.stories_dir.iterdir():
                if wd.is_dir():
                    _scan(wd / CHAPTERS_DIR_NAME, f"作品《{wd.name}》章节")
        results.sort(key=lambda r: r["mtime"], reverse=True)
        return results
