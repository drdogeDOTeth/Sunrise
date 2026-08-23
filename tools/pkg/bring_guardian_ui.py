"""Host Intake — desktop front door for bring_guardian.py.

Fallout-terminal / Westworld-host desk: pick a GLB, assign materials to the
five carrier slots, dry-run or inject. The engine stays in bring_guardian.py.
"""
from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from bring_guardian import (
    ART,
    CANON,
    find_blender,
    inspect_glb,
    recalled_glb,
    remember_glb,
    run_pipeline,
)

INK = "#e8d5a3"
DIM = "#9a8458"
AMBER = "#e3b341"
RUST = "#c45c26"
HOST = "#8fbc5a"
BG = "#14110d"
PANEL = "#221b14"
FIELD = "#2c241a"
LINE = "#5a4630"
FONT = ("Cascadia Mono", 10)
DISPLAY = ("Cascadia Mono", 18, "bold")
SMALL = ("Cascadia Mono", 8)


class Intake(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("SUNRISE  ·  HOST INTAKE")
        self.configure(bg=BG)
        self.geometry("980x720")
        self.minsize(860, 640)
        last = recalled_glb()
        self.glb = tk.StringVar(value=str(last) if last else "")
        self.status = tk.StringVar(
            value="Pick your GLB. This desk does not ship a character. Destiny stays closed for an inject."
        )
        self.material_vars: dict[str, tk.StringVar] = {}
        self.log_queue: queue.Queue[str] = queue.Queue()
        self._build()
        self.after(120, self._pump)
        if self.glb.get():
            self.refresh_inspect()

    def _build(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=INK, font=FONT)
        style.configure("Dim.TLabel", background=BG, foreground=DIM, font=SMALL)
        style.configure("Head.TLabel", background=BG, foreground=AMBER, font=DISPLAY)
        style.configure("Card.TLabel", background=PANEL, foreground=INK, font=FONT)
        style.configure("TButton", background=FIELD, foreground=AMBER, font=FONT, padding=6)
        style.map("TButton", background=[("active", RUST)], foreground=[("active", INK)])
        style.configure("Go.TButton", background=RUST, foreground=INK, font=FONT, padding=8)
        style.configure("TEntry", fieldbackground=FIELD, foreground=INK, insertcolor=AMBER)
        style.configure("TCombobox", fieldbackground=FIELD, foreground=INK, background=FIELD)
        style.configure("TLabelframe", background=PANEL, foreground=AMBER)
        style.configure("TLabelframe.Label", background=PANEL, foreground=AMBER, font=FONT)

        pad = {"padx": 16, "pady": 6}
        ttk.Label(self, text="PROJECT SUNRISE", style="Head.TLabel").pack(anchor="w", **pad)
        ttk.Label(
            self,
            text="Host intake  ·  your GLB → playable Warlock  ·  retarget / cut / inject",
            style="Dim.TLabel",
        ).pack(anchor="w", padx=16)

        row = ttk.Frame(self)
        row.pack(fill="x", padx=16, pady=8)
        ttk.Label(row, text="GLB").pack(side="left")
        ttk.Entry(row, textvariable=self.glb, width=78).pack(side="left", padx=8, fill="x", expand=True)
        ttk.Button(row, text="BROWSE", command=self.browse).pack(side="left", padx=4)
        ttk.Button(row, text="SCAN", command=self.refresh_inspect).pack(side="left")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=16, pady=4)
        left = ttk.LabelFrame(body, text=" MATERIALS  →  CARRIER SLOTS ")
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self.material_box = ttk.Frame(left, style="Card.TFrame")
        self.material_box.pack(fill="both", expand=True, padx=8, pady=8)

        right = ttk.LabelFrame(body, text=" JOINTS / NOTES ")
        right.pack(side="right", fill="both", expand=True)
        self.notes = tk.Text(
            right, height=16, bg=FIELD, fg=INK, insertbackground=AMBER,
            relief="flat", font=SMALL, wrap="word",
        )
        self.notes.pack(fill="both", expand=True, padx=8, pady=8)

        log_frame = ttk.LabelFrame(self, text=" TERMINAL ")
        log_frame.pack(fill="both", expand=True, padx=16, pady=8)
        self.console = tk.Text(
            log_frame, height=12, bg="#0d0b09", fg=HOST, insertbackground=HOST,
            relief="flat", font=SMALL, wrap="word",
        )
        self.console.pack(fill="both", expand=True, padx=8, pady=8)

        actions = ttk.Frame(self)
        actions.pack(fill="x", padx=16, pady=(0, 12))
        ttk.Label(actions, textvariable=self.status, style="Dim.TLabel").pack(side="left")
        ttk.Button(actions, text="DRY RUN", command=lambda: self.go(inject=False, dry=True)).pack(
            side="right", padx=4
        )
        ttk.Button(
            actions, text="INJECT  (DESTINY CLOSED)", style="Go.TButton",
            command=lambda: self.go(inject=True, dry=False),
        ).pack(side="right", padx=4)

    def browse(self) -> None:
        picked = filedialog.askopenfilename(
            title="Custom character GLB",
            filetypes=[("glTF binary", "*.glb"), ("All files", "*.*")],
        )
        if picked:
            self.glb.set(picked)
            remember_glb(Path(picked))
            self.refresh_inspect()

    def refresh_inspect(self) -> None:
        path = Path(self.glb.get())
        for child in self.material_box.winfo_children():
            child.destroy()
        self.material_vars.clear()
        self.notes.delete("1.0", "end")
        if not path.is_file():
            self.notes.insert("end", "No GLB loaded.\n")
            return
        try:
            info = inspect_glb(path)
        except Exception as error:  # noqa: BLE001 — show the host the real hitch
            self.notes.insert("end", f"Scan failed: {error}\n")
            return
        slots = list(CANON.keys()) + ["skip"]
        ttk.Label(
            self.material_box,
            text="Each source material rides one chest carrier. Two on the same slot share one atlas.",
            style="Card.TLabel",
        ).pack(anchor="w", pady=(0, 8))
        for material in info["materials"]:
            row = ttk.Frame(self.material_box, style="Card.TFrame")
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=material["name"], style="Card.TLabel", width=28).pack(side="left")
            var = tk.StringVar(value=material["slot"])
            self.material_vars[material["name"]] = var
            box = ttk.Combobox(row, textvariable=var, values=slots, width=12, state="readonly")
            box.pack(side="left", padx=8)
            base = material["base"]
            hint = f"{base['width']}x{base['height']}" if base else "no albedo"
            ttk.Label(row, text=hint, style="Card.TLabel").pack(side="left")
        self.notes.insert("end", f"meshes: {', '.join(info['meshes'])}\n")
        self.notes.insert("end", f"joints: {len(info['joints'])}\n\n")
        unmapped = []
        for name in info["joints"]:
            from bring_guardian import guess_bone
            if guess_bone(name) is None:
                unmapped.append(name)
        if unmapped:
            self.notes.insert("end", "Unmapped joints (ignored unless you add a bone-map JSON):\n")
            for name in unmapped:
                self.notes.insert("end", f"  {name}\n")
        else:
            self.notes.insert("end", "Every joint name matched the Guardian map.\n")
        try:
            blender = find_blender()
            self.notes.insert("end", f"\nBlender: {blender}\n")
        except SystemExit as error:
            self.notes.insert("end", f"\n{error}\n")
        self.notes.insert("end", f"Hook atlases land in:\n  {ART}\n")
        self.status.set(f"Scanned {path.name}. Dry-run first if this is a new host.")

    def go(self, *, inject: bool, dry: bool) -> None:
        path = Path(self.glb.get())
        if not path.is_file():
            messagebox.showerror("Host intake", "Pick a GLB first.")
            return
        if inject and not messagebox.askyesno(
            "Write packages",
            "This writes the live Scatterhorn chest / gauntlets.\n"
            "Destiny 2 must be closed.\n\nProceed?",
        ):
            return
        overrides = {
            name: var.get()
            for name, var in self.material_vars.items()
            if var.get() and var.get() != "skip"
        }
        self.status.set("Running the intake line…")
        self._echo("— intake start —")

        def work() -> None:
            writer = _QueueWriter(self.log_queue)
            old = sys.stdout
            sys.stdout = writer
            try:
                run_pipeline(
                    path,
                    inject=inject,
                    dry_run=dry,
                    material_overrides=overrides,
                )
                self.log_queue.put("— intake finished —")
            except Exception as error:  # noqa: BLE001
                self.log_queue.put(f"FAIL: {error}")
            finally:
                sys.stdout = old

        threading.Thread(target=work, daemon=True).start()

    def _echo(self, line: str) -> None:
        self.console.insert("end", line + "\n")
        self.console.see("end")

    def _pump(self) -> None:
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self._echo(line)
            if line.startswith("FAIL"):
                self.status.set("Intake failed. Read the terminal.")
            elif "finished" in line:
                self.status.set("Done. Launch Destiny and look at select, world, and first-person.")
        self.after(150, self._pump)


class _QueueWriter:
    def __init__(self, bucket: queue.Queue[str]) -> None:
        self.bucket = bucket

    def write(self, text: str) -> int:
        for line in text.splitlines():
            if line:
                self.bucket.put(line)
        return len(text)

    def flush(self) -> None:
        return None


def main() -> None:
    app = Intake()
    app.mainloop()


if __name__ == "__main__":
    main()
