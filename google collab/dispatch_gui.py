"""
dispatch_gui.py
----------------
GUI version - no command line needed. One window, four tabs:

  Allocate     - pick your audio folder, type Groq hours, split remaining
                 untranscribed work into dispatch/groq/ and dispatch/colab_batch.zip
  Import Colab - after a Colab run (finished or crashed), pick its
                 batch_state.json and merge what it finished into coverage.json
  Import Local - pick your local GUI's renamed 30-sec transcript chunks,
                 merge into coverage.json, auto-export the remaining gap
                 for re-upload to Drive
  Status       - pick your audio folder, see a done/remaining table

Everything lives under ./dispatch/ next to this script (coverage.json,
exported audio, zips) so it's self-contained wherever you run it from.

Requires: ffmpeg + ffprobe on PATH, tkinter (usually built in to Python).
"""

import json
import re
import shutil
import subprocess
import zipfile
from datetime import date
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

# ============================================================
# SHARED CONFIG / HELPERS
# ============================================================

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".oga", ".webm", ".mp4"}
DISPATCH_DIR = Path("./dispatch")
MANIFEST_PATH = DISPATCH_DIR / "coverage.json"
EXPORT_NAME_RE = re.compile(r"^(?P<stem>.+)__(?P<start>[\d.]+)-(?P<end>[\d.]+)(?P<ext>\.[^.]+)$")
CHUNK_NAME_RE = re.compile(r"^(?P<stem>.+)__(?P<start>[\d.]+)-(?P<end>[\d.]+)\.txt$")


def get_audio_duration(file_path: Path) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", str(file_path)]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return float(res.stdout.strip())
    except Exception:
        return file_path.stat().st_size * 8 / (64 * 1000)


def export_range(src: Path, start: float, end: float, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-i", str(src), "-ss", str(start), "-to", str(end), dest.as_posix()]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


# ============================================================
# COVERAGE MANIFEST
# ============================================================

def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"files": {}}


def save_manifest(manifest: dict):
    DISPATCH_DIR.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


def ensure_file(manifest: dict, filename: str, duration_sec: float):
    if filename not in manifest["files"]:
        manifest["files"][filename] = {"duration_sec": duration_sec, "ranges": []}
    else:
        manifest["files"][filename]["duration_sec"] = duration_sec


def add_range(manifest: dict, filename: str, start: float, end: float, source: str, when: str = None):
    if end <= start:
        return
    when = when or date.today().isoformat()
    entry = manifest["files"].setdefault(filename, {"duration_sec": end, "ranges": []})
    entry["ranges"].append({"start": start, "end": end, "source": source, "date": when})
    entry["ranges"] = _merge_ranges(entry["ranges"])


def _merge_ranges(ranges: list[dict]) -> list[dict]:
    if not ranges:
        return []
    ordered = sorted(ranges, key=lambda r: r["start"])
    merged = [dict(ordered[0])]
    for r in ordered[1:]:
        last = merged[-1]
        if r["start"] <= last["end"] + 0.5:
            last["end"] = max(last["end"], r["end"])
        else:
            merged.append(dict(r))
    return merged


def get_gaps(manifest: dict, filename: str, duration_sec: float) -> list[dict]:
    entry = manifest["files"].get(filename)
    covered = sorted(entry["ranges"], key=lambda r: r["start"]) if entry else []
    gaps, cursor = [], 0.0
    for r in covered:
        if r["start"] > cursor:
            gaps.append({"start": cursor, "end": r["start"]})
        cursor = max(cursor, r["end"])
    if cursor < duration_sec:
        gaps.append({"start": cursor, "end": duration_sec})
    return gaps


def coverage_summary(manifest: dict, filename: str, duration_sec: float) -> dict:
    gaps = get_gaps(manifest, filename, duration_sec)
    remaining = sum(g["end"] - g["start"] for g in gaps)
    return {"duration_sec": duration_sec, "covered_sec": duration_sec - remaining,
            "remaining_sec": remaining, "gaps": gaps, "complete": remaining < 1.0}


def build_gap_pool(source_folder: Path, manifest: dict) -> list[dict]:
    pool = []
    for f in sorted(source_folder.iterdir()):
        if not (f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS):
            continue
        duration = get_audio_duration(f)
        ensure_file(manifest, f.name, duration)
        for g in get_gaps(manifest, f.name, duration):
            span = g["end"] - g["start"]
            if span >= 1.0:
                pool.append({"file": f, "filename": f.name, "start": g["start"], "end": g["end"], "duration_sec": span})
    return pool


def allocate_groq_first(pool: list[dict], groq_cap_sec: float) -> dict:
    ordered = sorted(pool, key=lambda g: g["duration_sec"], reverse=True)
    groq, colab, remaining = [], [], groq_cap_sec
    for g in ordered:
        if g["duration_sec"] <= remaining:
            groq.append(g)
            remaining -= g["duration_sec"]
        else:
            colab.append(g)
    return {"groq": groq, "colab": colab}


def resolve_source_and_offset(exported_filename: str) -> tuple[str, float]:
    m = EXPORT_NAME_RE.match(exported_filename)
    if m:
        return f"{m.group('stem')}{m.group('ext')}", float(m.group("start"))
    return exported_filename, 0.0


def find_source_file(stem: str, search_dirs: list[Path]) -> Path | None:
    for d in search_dirs:
        if not d.exists():
            continue
        for f in d.iterdir():
            if f.is_file() and f.stem == stem:
                return f
    return None


# ============================================================
# GUI
# ============================================================

class DispatchApp:
    def __init__(self, root):
        self.root = root
        root.title("Audio Dispatch Manager")
        root.geometry("760x600")

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        self.allocate_tab = ttk.Frame(notebook)
        self.colab_tab = ttk.Frame(notebook)
        self.local_tab = ttk.Frame(notebook)
        self.status_tab = ttk.Frame(notebook)

        notebook.add(self.allocate_tab, text="Allocate")
        notebook.add(self.colab_tab, text="Import Colab")
        notebook.add(self.local_tab, text="Import Local")
        notebook.add(self.status_tab, text="Status")

        self._build_allocate_tab()
        self._build_colab_tab()
        self._build_local_tab()
        self._build_status_tab()
        self._refresh_source_list()

    # ---------- shared log widget helper ----------
    def _make_log(self, parent):
        log = scrolledtext.ScrolledText(parent, height=20, font=("Consolas", 9))
        log.pack(fill="both", expand=True, padx=10, pady=10)
        return log

    def _log(self, widget, text):
        widget.insert(tk.END, text + "\n")
        widget.see(tk.END)
        self.root.update()

    # ---------- ALLOCATE TAB ----------
    def _build_allocate_tab(self):
        f = self.allocate_tab
        row = ttk.Frame(f)
        row.pack(fill="x", padx=10, pady=10)

        self.source_folder_var = tk.StringVar(value="No folder selected")
        ttk.Button(row, text="Choose Audio Folder...", command=self._choose_source_folder).pack(side="left")
        ttk.Label(row, textvariable=self.source_folder_var).pack(side="left", padx=10)

        row2 = ttk.Frame(f)
        row2.pack(fill="x", padx=10, pady=5)
        ttk.Label(row2, text="Groq hours (exact, from your key cycler):").pack(side="left")
        self.groq_hours_var = tk.StringVar(value="16")
        ttk.Entry(row2, textvariable=self.groq_hours_var, width=8).pack(side="left", padx=8)
        ttk.Button(row2, text="Run Allocation", command=self._run_allocate).pack(side="left", padx=10)

        self.allocate_log = self._make_log(f)
        self._source_folder = None

    def _choose_source_folder(self):
        folder = filedialog.askdirectory(title="Select folder of source audio files")
        if folder:
            self._source_folder = Path(folder)
            self.source_folder_var.set(str(self._source_folder))

    def _run_allocate(self):
        if not self._source_folder:
            messagebox.showwarning("No folder", "Choose an audio folder first.")
            return
        try:
            groq_hours = float(self.groq_hours_var.get())
        except ValueError:
            messagebox.showwarning("Invalid input", "Groq hours must be a number.")
            return

        self.allocate_log.delete("1.0", tk.END)
        manifest = load_manifest()
        pool = build_gap_pool(self._source_folder, manifest)
        save_manifest(manifest)

        if not pool:
            self._log(self.allocate_log, "[+] Nothing left to transcribe - every file is fully covered.")
            return

        total_hrs = sum(g["duration_sec"] for g in pool) / 3600.0
        self._log(self.allocate_log, f"[+] {len(pool)} untranscribed gap(s) - {total_hrs:.2f} hrs remaining total.")

        split = allocate_groq_first(pool, groq_hours * 3600.0)
        plan = {"groq": [], "colab": []}

        for bucket in ("groq", "colab"):
            bdir = DISPATCH_DIR / bucket
            if bdir.exists():
                shutil.rmtree(bdir)
            bdir.mkdir(parents=True)
            for g in split[bucket]:
                out_name = f"{Path(g['filename']).stem}__{g['start']:.1f}-{g['end']:.1f}{g['file'].suffix}"
                export_range(g["file"], g["start"], g["end"], bdir / out_name)
                plan[bucket].append({"source_file": g["filename"], "start": g["start"], "end": g["end"], "exported_as": out_name})
                self._log(self.allocate_log, f"    [{bucket}] {g['filename']} {g['start']:.1f}-{g['end']:.1f}s -> {out_name}")

        colab_dir = DISPATCH_DIR / "colab"
        if any(colab_dir.iterdir()):
            zip_path = DISPATCH_DIR / "colab_batch.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in colab_dir.iterdir():
                    zf.write(f, arcname=f.name)
            plan["colab_zip"] = str(zip_path.resolve())

        with open(DISPATCH_DIR / "plan.json", "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2)

        groq_hrs = sum(g["duration_sec"] for g in split["groq"]) / 3600.0
        colab_hrs = sum(g["duration_sec"] for g in split["colab"]) / 3600.0
        self._log(self.allocate_log, f"\n[+] Groq:  {groq_hrs:.2f} hrs -> {(DISPATCH_DIR/'groq').resolve()}")
        self._log(self.allocate_log, f"[+] Colab: {colab_hrs:.2f} hrs -> {(DISPATCH_DIR/'colab_batch.zip').resolve()}")
        self._log(self.allocate_log, "\nDone. Point the Groq script's file picker at dispatch/groq/, "
                                      "and upload colab_batch.zip to Drive for the Colab script.")

    # ---------- IMPORT COLAB TAB ----------
    def _build_colab_tab(self):
        f = self.colab_tab
        row = ttk.Frame(f)
        row.pack(fill="x", padx=10, pady=10)
        ttk.Button(row, text="Choose batch_state.json...", command=self._choose_colab_state).pack(side="left")
        self.colab_state_var = tk.StringVar(value="No file selected")
        ttk.Label(row, textvariable=self.colab_state_var).pack(side="left", padx=10)
        ttk.Button(f, text="Import", command=self._run_import_colab).pack(anchor="w", padx=10, pady=5)
        self.colab_log = self._make_log(f)
        self._colab_state_file = None

    def _choose_colab_state(self):
        path = filedialog.askopenfilename(title="Select Colab's batch_state.json", filetypes=[("JSON", "*.json")])
        if path:
            self._colab_state_file = Path(path)
            self.colab_state_var.set(str(self._colab_state_file))

    def _run_import_colab(self):
        if not self._colab_state_file or not self._colab_state_file.exists():
            messagebox.showwarning("No file", "Choose a valid batch_state.json first.")
            return
        self.colab_log.delete("1.0", tk.END)

        with open(self._colab_state_file, "r", encoding="utf-8") as f:
            colab_state = json.load(f)

        manifest = load_manifest()
        imported, skipped = 0, 0

        for key, entry in colab_state.items():
            m = re.match(r"^(?P<fname>.+)_(?P<fsize>\d+)$", key)
            if not m:
                self._log(self.colab_log, f"[!] Couldn't parse state key '{key}', skipping.")
                skipped += 1
                continue
            exported_filename = m.group("fname")
            last_end = float(entry.get("last_end", 0.0))
            done = bool(entry.get("done", False))
            if last_end <= 0 and not done:
                skipped += 1
                continue

            source_filename, offset = resolve_source_and_offset(exported_filename)
            covered_start, covered_end = offset, offset + last_end
            add_range(manifest, source_filename, covered_start, covered_end, source="colab")
            imported += 1
            status = "fully done" if done else f"partial, up to {last_end:.1f}s into this chunk"
            self._log(self.colab_log, f"[+] {source_filename}: covered {covered_start:.1f}s -> {covered_end:.1f}s ({status})")

        save_manifest(manifest)
        self._log(self.colab_log, f"\n[OK] Imported {imported}, skipped {skipped}. "
                                   f"Coverage updated - re-run Allocate and it'll skip what's now done.")

    # ---------- IMPORT LOCAL TAB ----------
    def _build_local_tab(self):
        f = self.local_tab

        # -- Auto-sequence mode (default): no manual renaming needed --
        auto_frame = ttk.LabelFrame(f, text=" Auto-Sequence (recommended - no renaming needed) ")
        auto_frame.pack(fill="x", padx=10, pady=10)

        row = ttk.Frame(auto_frame)
        row.pack(fill="x", padx=10, pady=5)
        ttk.Button(row, text="Choose Local Output Folder...", command=self._choose_local_chunks).pack(side="left")
        self.local_chunks_var = tk.StringVar(value="No folder selected")
        ttk.Label(row, textvariable=self.local_chunks_var).pack(side="left", padx=10)
        ttk.Label(auto_frame, text="Files are sorted by their dated filename/timestamp (oldest first) and "
                                    "stamped +30s each in order automatically.",
                  font=("Arial", 9, "italic")).pack(anchor="w", padx=10)

        row2 = ttk.Frame(auto_frame)
        row2.pack(fill="x", padx=10, pady=5)
        ttk.Label(row2, text="Source audio filename:").pack(side="left")
        self.local_source_var = tk.StringVar()
        self.local_source_combo = ttk.Combobox(row2, textvariable=self.local_source_var, width=30)
        self.local_source_combo.pack(side="left", padx=8)
        ttk.Button(row2, text="Refresh list", command=self._refresh_source_list).pack(side="left")

        row3 = ttk.Frame(auto_frame)
        row3.pack(fill="x", padx=10, pady=5)
        ttk.Label(row3, text="Starting offset (sec) - where local was fed from:").pack(side="left")
        self.local_offset_var = tk.StringVar(value="")
        ttk.Entry(row3, textvariable=self.local_offset_var, width=10).pack(side="left", padx=8)
        ttk.Button(row3, text="Use next gap start", command=self._fill_offset_from_gap).pack(side="left", padx=5)

        row4 = ttk.Frame(auto_frame)
        row4.pack(fill="x", padx=10, pady=5)
        ttk.Label(row4, text="Chunk length (sec):").pack(side="left")
        self.local_chunklen_var = tk.StringVar(value="30")
        ttk.Entry(row4, textvariable=self.local_chunklen_var, width=6).pack(side="left", padx=8)

        ttk.Button(auto_frame, text="Auto-Sequence & Import", command=self._run_import_local_auto).pack(
            anchor="w", padx=10, pady=8)

        # -- Originals folder, needed either way to export the remaining gap --
        row5 = ttk.Frame(f)
        row5.pack(fill="x", padx=10, pady=5)
        ttk.Button(row5, text="Choose Originals Folder(s)...", command=self._choose_originals).pack(side="left")
        self.originals_var = tk.StringVar(value=f"Default: {(DISPATCH_DIR/'colab').resolve()}, current dir")
        ttk.Label(row5, textvariable=self.originals_var, wraplength=550).pack(side="left", padx=10)

        # -- Manual mode, still available if you'd rather pre-rename yourself --
        manual_frame = ttk.LabelFrame(f, text=" Manual mode (pre-renamed files, if you prefer) ")
        manual_frame.pack(fill="x", padx=10, pady=5)
        ttk.Label(manual_frame, text="Chunk files named  originalstem__START-END.txt  (seconds)",
                  font=("Arial", 9, "italic")).pack(anchor="w", padx=10)
        ttk.Button(manual_frame, text="Import Pre-Renamed Folder", command=self._run_import_local_manual).pack(
            anchor="w", padx=10, pady=5)

        self.local_log = self._make_log(f)
        self._local_chunks_folder = None
        self._originals_dirs = [DISPATCH_DIR / "colab", Path(".")]

    def _refresh_source_list(self):
        manifest = load_manifest()
        names = sorted(manifest["files"].keys())
        self.local_source_combo["values"] = names
        if names and not self.local_source_var.get():
            self.local_source_var.set(names[0])

    def _fill_offset_from_gap(self):
        source_filename = self.local_source_var.get().strip()
        if not source_filename:
            messagebox.showwarning("No source selected", "Pick a source audio filename first (Refresh list).")
            return
        manifest = load_manifest()
        entry = manifest["files"].get(source_filename)
        if not entry:
            messagebox.showwarning("Unknown file", f"'{source_filename}' isn't tracked in coverage.json yet.")
            return
        gaps = get_gaps(manifest, source_filename, entry["duration_sec"])
        if not gaps:
            messagebox.showinfo("Fully covered", f"'{source_filename}' has no remaining gaps.")
            return
        self.local_offset_var.set(f"{gaps[0]['start']:.1f}")

    def _choose_local_chunks(self):
        folder = filedialog.askdirectory(title="Select folder of renamed local transcript chunks")
        if folder:
            self._local_chunks_folder = Path(folder)
            self.local_chunks_var.set(str(self._local_chunks_folder))

    def _choose_originals(self):
        folder = filedialog.askdirectory(title="Select folder containing original source audio")
        if folder:
            self._originals_dirs = [Path(folder)]
            self.originals_var.set(str(Path(folder)))

    def _export_remaining_gap(self, stem: str, source_filename: str, manifest: dict, resume_dir: Path):
        """Shared tail step for both import modes: export whatever's still left as fresh audio for Drive re-upload."""
        entry = manifest["files"].get(source_filename)
        if not entry:
            return
        duration = entry["duration_sec"]
        summary = coverage_summary(manifest, source_filename, duration)
        if summary["complete"]:
            self._log(self.local_log, f"[+] {source_filename}: fully transcribed now.")
            return
        src = find_source_file(stem, self._originals_dirs)
        if not src:
            self._log(self.local_log, f"[!] {source_filename}: {summary['remaining_sec']/60:.1f} min remaining, "
                                       f"but couldn't find the original audio. Set the originals folder above.")
            return
        for g in summary["gaps"]:
            out_name = f"{stem}__{g['start']:.1f}-{g['end']:.1f}{src.suffix}"
            export_range(src, g["start"], g["end"], resume_dir / out_name)
        self._log(self.local_log, f"[+] {source_filename}: {summary['remaining_sec']/60:.1f} min remaining, "
                                   f"exported to {resume_dir.resolve()} for re-upload to Drive.")

    def _fresh_resume_dir(self) -> Path:
        resume_dir = DISPATCH_DIR / "colab_resume"
        if resume_dir.exists():
            for f in resume_dir.iterdir():
                f.unlink()
        else:
            resume_dir.mkdir(parents=True)
        return resume_dir

    def _run_import_local_auto(self):
        """No renaming needed: sorts local output files by dated filename/mtime (oldest first)
        and stamps each one +chunk_len seconds after the last, starting from the offset you gave -
        i.e. does the '+30+30+30' math for you instead of you doing it by hand."""
        if not self._local_chunks_folder:
            messagebox.showwarning("No folder", "Choose your local output folder first.")
            return
        source_filename = self.local_source_var.get().strip()
        if not source_filename:
            messagebox.showwarning("No source", "Pick the source audio filename this batch came from.")
            return
        try:
            offset = float(self.local_offset_var.get())
            chunk_len = float(self.local_chunklen_var.get())
        except ValueError:
            messagebox.showwarning("Invalid input", "Starting offset and chunk length must be numbers.")
            return

        self.local_log.delete("1.0", tk.END)

        # Sort chronologically: local whisper's dated filenames sort correctly by
        # name in most cases, but fall back to modification time as a tiebreaker/
        # safety net so the sequence is always oldest-first regardless of naming.
        files = [f for f in self._local_chunks_folder.iterdir() if f.is_file()]
        files.sort(key=lambda f: (f.name, f.stat().st_mtime))

        if not files:
            self._log(self.local_log, "[!] No files found in that folder.")
            return

        manifest = load_manifest()
        cursor = offset
        for f in files:
            start, end = cursor, cursor + chunk_len
            add_range(manifest, source_filename, start, end, source="local")
            self._log(self.local_log, f"[+] {f.name} -> {start:.1f}s - {end:.1f}s")
            cursor = end

        save_manifest(manifest)
        self._log(self.local_log, f"\n[OK] Imported {len(files)} chunk(s), covering "
                                   f"{offset:.1f}s -> {cursor:.1f}s of '{source_filename}'.\n")

        stem = Path(source_filename).stem
        resume_dir = self._fresh_resume_dir()
        self._export_remaining_gap(stem, source_filename, manifest, resume_dir)

    def _run_import_local_manual(self):
        """Fallback: files pre-renamed as stem__start-end.txt by hand."""
        if not self._local_chunks_folder:
            messagebox.showwarning("No folder", "Choose your renamed local chunks folder first.")
            return
        self.local_log.delete("1.0", tk.END)

        manifest = load_manifest()
        imported, skipped = 0, 0
        touched = set()

        for txt_file in sorted(self._local_chunks_folder.iterdir()):
            if not txt_file.is_file():
                continue
            m = CHUNK_NAME_RE.match(txt_file.name)
            if not m:
                self._log(self.local_log, f"[!] '{txt_file.name}' doesn't match 'stem__start-end.txt', skipping.")
                skipped += 1
                continue
            stem = m.group("stem")
            start, end = float(m.group("start")), float(m.group("end"))
            matches = [fn for fn in manifest["files"] if Path(fn).stem == stem]
            source_filename = matches[0] if matches else f"{stem}.mp3"
            add_range(manifest, source_filename, start, end, source="local")
            touched.add((stem, source_filename))
            imported += 1

        save_manifest(manifest)
        self._log(self.local_log, f"[OK] Imported {imported}, skipped {skipped}.\n")

        resume_dir = self._fresh_resume_dir()
        for stem, source_filename in touched:
            self._export_remaining_gap(stem, source_filename, manifest, resume_dir)

    # ---------- STATUS TAB ----------
    def _build_status_tab(self):
        f = self.status_tab
        row = ttk.Frame(f)
        row.pack(fill="x", padx=10, pady=10)
        ttk.Button(row, text="Choose Audio Folder...", command=self._choose_status_folder).pack(side="left")
        self.status_folder_var = tk.StringVar(value="No folder selected")
        ttk.Label(row, textvariable=self.status_folder_var).pack(side="left", padx=10)
        ttk.Button(row, text="Refresh", command=self._run_status).pack(side="left", padx=10)

        columns = ("file", "done", "remaining", "pct")
        self.status_tree = ttk.Treeview(f, columns=columns, show="headings", height=20)
        for col, label, width in [("file", "File", 300), ("done", "Done (min)", 100),
                                   ("remaining", "Remaining (min)", 120), ("pct", "%", 60)]:
            self.status_tree.heading(col, text=label)
            self.status_tree.column(col, width=width)
        self.status_tree.pack(fill="both", expand=True, padx=10, pady=10)
        self._status_folder = None

    def _choose_status_folder(self):
        folder = filedialog.askdirectory(title="Select folder of source audio files")
        if folder:
            self._status_folder = Path(folder)
            self.status_folder_var.set(str(self._status_folder))
            self._run_status()

    def _run_status(self):
        if not self._status_folder:
            messagebox.showwarning("No folder", "Choose an audio folder first.")
            return
        for row in self.status_tree.get_children():
            self.status_tree.delete(row)

        manifest = load_manifest()
        for f in sorted(self._status_folder.iterdir()):
            if not (f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS):
                continue
            duration = get_audio_duration(f)
            ensure_file(manifest, f.name, duration)
            s = coverage_summary(manifest, f.name, duration)
            pct = 100 * s["covered_sec"] / duration if duration else 0
            self.status_tree.insert("", tk.END, values=(f.name, f"{s['covered_sec']/60:.1f}",
                                                          f"{s['remaining_sec']/60:.1f}", f"{pct:.1f}"))
        save_manifest(manifest)


def main():
    root = tk.Tk()
    DispatchApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
