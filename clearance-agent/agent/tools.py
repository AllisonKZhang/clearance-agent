from __future__ import annotations
import os, subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

def _safe_path(rel_path: str) -> Path:
    p = (REPO_ROOT / rel_path).resolve()
    if not str(p).startswith(str(REPO_ROOT.resolve())):
        raise ValueError("Path escapes repo root.")
    return p

def read_text(rel_path: str) -> str:
    p = _safe_path(rel_path)
    return p.read_text(encoding="utf-8")

def write_text(rel_path: str, content: str) -> str:
    p = _safe_path(rel_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {rel_path} ({len(content)} chars)"

def run_cmd(cmd: str) -> str:
    # Keep it simple: run in repo root
    result = subprocess.run(
        cmd, shell=True, cwd=str(REPO_ROOT),
        capture_output=True, text=True
    )
    out = (result.stdout or "") + (result.stderr or "")
    return f"exit={result.returncode}\n{out}"