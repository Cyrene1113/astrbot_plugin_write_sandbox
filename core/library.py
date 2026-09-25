"""写作沙盒 · library 域（Mixin 拆分自 core.py，2026-09-21）。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .constants import _safe_title


class LibraryMixin:
    """library 域方法。"""

    def library_texts(self) -> list[str]:
        return self._read_texts(self.library_dir)

    def favorite_texts(self) -> list[str]:
        return self._read_texts(self.favorites_dir)

    def library_sources(self) -> list[tuple[str, str]]:
        return self._read_sources(self.library_dir)

    def favorite_sources(self) -> list[tuple[str, str]]:
        return self._read_sources(self.favorites_dir)

    def _read_sources(directory: Path) -> list[tuple[str, str]]:
        """读取目录下所有 txt/md 素材，返回 [(文件名, 内容)]，供逐篇学习。"""
        sources: list[tuple[str, str]] = []
        for p in sorted(directory.glob("*")):
            if p.suffix.lower() not in (".txt", ".md"):
                continue
            try:
                content = p.read_text(encoding="utf-8").strip()
                if content:
                    sources.append((p.name, content))
            except Exception:
                continue
        return sources

    def _read_texts(directory: Path) -> list[str]:
        texts: list[str] = []
        for p in sorted(directory.glob("*")):
            if p.suffix.lower() not in (".txt", ".md"):
                continue
            try:
                content = p.read_text(encoding="utf-8").strip()
                if content:
                    texts.append(content)
            except Exception:
                continue
        return texts

    def save_favorite(self, text: str, tag: str = "") -> Path:
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        title = _safe_title(tag)
        filename = f"{ts}_{title}.txt" if title else f"{ts}.txt"
        path = self.favorites_dir / filename
        path.write_text(text, encoding="utf-8")
        return path
