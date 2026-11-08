"""Build changed sources and launch the current standalone test application."""

import argparse
import ctypes
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source_fingerprint(root=ROOT, *, environment=None):
    digest = hashlib.sha256()
    paths = [root / "main.py", root / "pyproject.toml"]
    for directory in ("nodera", "scripts", "packaging"):
        paths.extend(
            path
            for path in (root / directory).rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix not in {".pyc", ".pyo"}
            and path != root / "nodera/resources/build-info.json"
        )
    for path in sorted(paths):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    if environment is None:
        environment = sorted(
            (distribution.metadata["Name"], distribution.version)
            for distribution in importlib.metadata.distributions()
        )
    digest.update(json.dumps([sys.version, environment], sort_keys=True).encode())
    return digest.hexdigest()


def build_metadata(executable, root=ROOT):
    executable = executable.resolve()
    if not executable.is_relative_to((root / "dist/builds").resolve()):
        raise ValueError("Test executable must be inside dist/builds")
    if not executable.is_file():
        raise ValueError("Test executable is missing")
    metadata = json.loads(
        (executable.parent / "_internal/nodera/resources/build-info.json").read_text(
            encoding="utf-8"
        )
    )
    if metadata.get("channel") != "test":
        raise ValueError("Expected a test build")
    return metadata


def record_build(executable, root=ROOT):
    metadata = build_metadata(executable, root)
    state = root / "artifacts/test-launcher/latest.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    temporary = state.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {"executable": str(executable.resolve()), "fingerprint": metadata["source_fingerprint"]}
        ),
        encoding="utf-8",
    )
    temporary.replace(state)


def current_build(fingerprint, root=ROOT):
    try:
        state = json.loads((root / "artifacts/test-launcher/latest.json").read_text())
        executable = Path(state["executable"])
        metadata = build_metadata(executable, root)
        if state["fingerprint"] == fingerprint == metadata.get("source_fingerprint"):
            return executable
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def build(root=ROOT, *, progress=True):
    window = None
    if progress:
        import tkinter as tk
        from tkinter import ttk

        window = tk.Tk()
        window.title("Nodera Test")
        window.geometry("420x120")
        window.resizable(False, False)
        window.protocol("WM_DELETE_WINDOW", window.iconify)
        ttk.Label(window, text="Обновление тестовой сборки Nodera…").pack(pady=(20, 12))
        bar = ttk.Progressbar(window, mode="indeterminate", length=360)
        bar.pack()
        bar.start()
        window.update()
    log = root / "artifacts/test-launcher/build.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log.open("w", encoding="utf-8") as output:
            process = subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(root / "scripts/build-test.ps1"),
                ],
                cwd=root,
                stdout=output,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            while process.poll() is None:
                if window:
                    window.update()
                time.sleep(0.05)
            if process.returncode:
                raise RuntimeError(f"Сборка не удалась. Журнал: {log}")
    finally:
        if window:
            window.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--record-build", type=Path)
    parser.add_argument("--no-launch", action="store_true")
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    if arguments.record_build:
        record_build(arguments.record_build)
        return 0
    fingerprint = source_fingerprint()
    executable = current_build(fingerprint)
    if arguments.check:
        print(
            json.dumps(
                {
                    "needs_build": executable is None,
                    "executable": str(executable) if executable else None,
                }
            )
        )
        return 0
    import msvcrt

    lock_path = ROOT / "artifacts/test-launcher/launch.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError("Обновление Nodera Test уже выполняется.") from exc
        try:
            for _ in range(2):
                fingerprint = source_fingerprint()
                executable = current_build(fingerprint)
                if executable:
                    break
                build(progress=not arguments.no_launch)
            executable = current_build(source_fingerprint())
            if executable is None:
                raise RuntimeError("Исходники менялись во время сборки. Повторите запуск.")
            if arguments.no_launch:
                print(executable)
            else:
                os.startfile(str(executable))
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        if sys.stdout is not None:
            print(str(error), file=sys.stderr)
        if os.name == "nt" and sys.stdout is None:
            ctypes.windll.user32.MessageBoxW(None, str(error), "Nodera Test", 0x10)
        raise SystemExit(1)
