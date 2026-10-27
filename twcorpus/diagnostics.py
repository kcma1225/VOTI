"""Environment checks shown in UI and CLI. Report what is missing; never fake availability."""
from __future__ import annotations

import importlib.metadata
import importlib.util
import shutil
import subprocess
from pathlib import Path


def _version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def tools() -> dict:
    result = {}
    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        if path is None:
            result[tool] = "missing"
            continue
        output = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=10).stdout
        result[tool] = output.splitlines()[0] if output else "unknown version"
    runtimes = [name for name in ("deno", "node", "bun") if shutil.which(name)]
    result["js_runtime (yt-dlp)"] = ", ".join(runtimes) or "missing"
    for package in ("yt-dlp", "speechbrain", "torch", "scikit-learn", "starlette", "uvicorn"):
        result[package] = _version(package) or "missing"
    return result


def compute_devices() -> dict:
    """Imports torch; call only on demand because it is slow."""
    if importlib.util.find_spec("torch") is None:
        return {"torch": "missing", "cpu": False, "cuda": False, "mps": False}
    import torch
    return {"torch": torch.__version__, "cpu": True, "cuda": bool(torch.cuda.is_available()),
            "mps": bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())}


def storage(data_dir: Path) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(data_dir)
    sizes: dict[str, int] = {}
    for path in data_dir.rglob("*"):
        if path.is_file() and not path.is_symlink():
            top = path.relative_to(data_dir).parts[0]
            sizes[top] = sizes.get(top, 0) + path.stat().st_size
    return {"free_bytes": usage.free, "total_bytes": usage.total,
            "data_bytes": sum(sizes.values()), "by_folder": sizes}
