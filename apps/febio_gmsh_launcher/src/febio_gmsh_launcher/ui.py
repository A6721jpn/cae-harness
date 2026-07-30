from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PreflightInfo:
    model: Path
    step: Path
    step_hash: str
    target_size_mm: float
    min_size_mm: float
    surfaces: tuple[str, ...]
    domains: tuple[str, ...]
    solver: Path
    run_dir: Path


def format_preflight(info: PreflightInfo) -> str:
    return "\n".join(
        [
            f"Model snapshot: {info.model}",
            f"STEP: {info.step}",
            f"STEP SHA-256: {info.step_hash}",
            f"Gmsh curved Tet10: {info.min_size_mm:g} .. "
            f"{info.target_size_mm:g} mm",
            f"CAD Surfaces: {', '.join(info.surfaces) or '(none)'}",
            f"Parts: {', '.join(info.domains) or '(none)'}",
            f"FEBio: {info.solver}",
            f"Run artifacts: {info.run_dir}",
        ]
    )


def confirm_preflight(info: PreflightInfo) -> bool:
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    try:
        return bool(
            messagebox.askokcancel(
                "FEBio Gmsh Tet10 preflight",
                format_preflight(info) + "\n\nFreeze this snapshot and run?",
                parent=root,
            )
        )
    finally:
        root.destroy()


class LogWindow:
    def __init__(self, title: str, log_path: Path) -> None:
        self.title = title
        self.log_path = log_path
        self.cancel_event = threading.Event()
        self._messages: queue.Queue[str | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._handle = None

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.log_path.open("a", encoding="utf-8", buffering=1)
        self._thread.start()

    def emit(self, message: str) -> None:
        print(message, flush=True)
        if self._handle is not None:
            self._handle.write(message + "\n")
        self._messages.put(message)

    def close(self) -> None:
        self._messages.put(None)
        self._thread.join(timeout=2)
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def _run(self) -> None:
        import tkinter as tk
        from tkinter.scrolledtext import ScrolledText

        root = tk.Tk()
        root.title(self.title)
        root.geometry("900x520")
        text = ScrolledText(root, wrap=tk.WORD, font=("Consolas", 10))
        text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        cancel = tk.Button(
            root,
            text="Cancel",
            command=lambda: (self.cancel_event.set(), cancel.config(state=tk.DISABLED)),
        )
        cancel.pack(pady=(0, 8))

        def poll() -> None:
            while True:
                try:
                    message = self._messages.get_nowait()
                except queue.Empty:
                    break
                if message is None:
                    root.destroy()
                    return
                text.insert(tk.END, message + "\n")
                text.see(tk.END)
            root.after(100, poll)

        root.after(100, poll)
        root.mainloop()
