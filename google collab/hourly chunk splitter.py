#!/usr/bin/env python3
"""
Audio Merger GUI
Merge audio files oldest->newest, split into 60-min parts, optionally make video.
Needs ffmpeg (on PATH, or pip install imageio-ffmpeg).
"""
import os
import sys
import shutil
import subprocess
import tempfile
import time
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

AUDIO_EXT = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus", ".wma"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"}
PART_SEC = 3600


# ── ffmpeg helpers ────────────────────────────────────────────────────────────

def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def created(path):
    st = os.stat(path)
    t = getattr(st, "st_birthtime", None)
    if t:
        return t
    return st.st_ctime if os.name == "nt" else st.st_mtime


def wav_tag(path):
    try:
        with open(path, "rb") as f:
            if f.read(4) not in (b"RIFF", b"RF64"):
                return None
            f.read(4)
            if f.read(4) != b"WAVE":
                return None
            while True:
                hdr = f.read(8)
                if len(hdr) < 8:
                    return None
                cid, size = hdr[:4], int.from_bytes(hdr[4:], "little")
                if cid == b"fmt ":
                    return int.from_bytes(f.read(2), "little")
                f.seek(size + (size & 1), 1)
    except OSError:
        return None


def needs_decode(files):
    return any(t not in (None, 1, 3, 0xFFFE) for t in map(wav_tag, files))


def concat_list(paths):
    f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    for p in paths:
        p = os.path.abspath(p).replace("\\", "/").replace("'", "'\\''")
        f.write("file '%s'\n" % p)
    f.close()
    return f.name


def run_ff(cmd, log_fn):
    proc = subprocess.Popen(
        cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
        universal_newlines=True, bufsize=1
    )
    for line in proc.stderr:
        log_fn(line.rstrip())
    proc.wait()
    return proc.returncode


def mb(paths):
    return sum(os.path.getsize(p) for p in paths if os.path.exists(p)) / 1048576


# ── GUI ───────────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Audio Merger")
        self.resizable(True, True)
        self.minsize(600, 520)

        self.ff = find_ffmpeg()
        self.files = []          # ordered list of audio paths
        self.image_path = tk.StringVar()
        self.make_video = tk.BooleanVar(value=False)
        self.running = False

        self._build_ui()
        self._check_ffmpeg()

    # ── layout ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        PAD = dict(padx=10, pady=5)

        # ── File list ──
        lf = ttk.LabelFrame(self, text="Audio files  (drag to reorder  •  sorted oldest→newest on add)")
        lf.pack(fill="both", expand=True, **PAD)

        fm = tk.Frame(lf)
        fm.pack(fill="both", expand=True, padx=5, pady=5)

        self.lb = tk.Listbox(fm, selectmode="extended", activestyle="dotbox",
                             height=10, font=("TkFixedFont", 10))
        sb = ttk.Scrollbar(fm, orient="vertical", command=self.lb.yview)
        self.lb.configure(yscrollcommand=sb.set)
        self.lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")

        # drag-to-reorder
        self.lb.bind("<ButtonPress-1>",   self._drag_start)
        self.lb.bind("<B1-Motion>",       self._drag_motion)
        self.lb.bind("<ButtonRelease-1>", self._drag_end)
        self._drag_data = {"idx": None}

        btn_row = tk.Frame(lf)
        btn_row.pack(fill="x", padx=5, pady=(0, 5))
        ttk.Button(btn_row, text="➕ Add files",    command=self._add_files).pack(side="left", padx=2)
        ttk.Button(btn_row, text="➕ Add folder",   command=self._add_folder).pack(side="left", padx=2)
        ttk.Button(btn_row, text="🗑 Remove",       command=self._remove_sel).pack(side="left", padx=2)
        ttk.Button(btn_row, text="⬆ Up",           command=lambda: self._move(-1)).pack(side="left", padx=2)
        ttk.Button(btn_row, text="⬇ Down",         command=lambda: self._move(1)).pack(side="left", padx=2)
        ttk.Button(btn_row, text="🗂 Sort by date", command=self._sort_date).pack(side="left", padx=2)
        ttk.Button(btn_row, text="❌ Clear all",    command=self._clear).pack(side="right", padx=2)

        # ── Output folder ──
        of = ttk.LabelFrame(self, text="Output folder")
        of.pack(fill="x", **PAD)
        self.out_var = tk.StringVar()
        ttk.Entry(of, textvariable=self.out_var, width=60).pack(side="left", fill="x",
                                                                  expand=True, padx=5, pady=5)
        ttk.Button(of, text="Browse…", command=self._pick_outdir).pack(side="left", padx=5)

        # ── Video option ──
        vf = ttk.LabelFrame(self, text="Video export (optional)")
        vf.pack(fill="x", **PAD)
        ttk.Checkbutton(vf, text="Also create .mp4 video",
                        variable=self.make_video,
                        command=self._toggle_video).pack(anchor="w", padx=5, pady=(5, 0))

        self.img_frame = tk.Frame(vf)
        self.img_frame.pack(fill="x", padx=5, pady=5)
        ttk.Label(self.img_frame, text="Background image (leave blank for black):").pack(side="left")
        self.img_entry = ttk.Entry(self.img_frame, textvariable=self.image_path, width=35, state="disabled")
        self.img_entry.pack(side="left", padx=4)
        self.img_btn = ttk.Button(self.img_frame, text="Browse…",
                                  command=self._pick_image, state="disabled")
        self.img_btn.pack(side="left")

        # ── Run / progress ──
        self.run_btn = ttk.Button(self, text="▶  Run", command=self._run, style="Accent.TButton")
        self.run_btn.pack(pady=(6, 2))

        self.progress = ttk.Progressbar(self, mode="indeterminate", length=560)
        self.progress.pack(**PAD)

        # ── Log ──
        lof = ttk.LabelFrame(self, text="Log")
        lof.pack(fill="both", expand=False, **PAD)
        self.log = tk.Text(lof, height=7, state="disabled",
                           font=("TkFixedFont", 9), wrap="word",
                           bg="#1e1e1e", fg="#d4d4d4", insertbackground="white")
        ls = ttk.Scrollbar(lof, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=ls.set)
        self.log.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        ls.pack(side="left", fill="y")

    # ── drag-to-reorder ───────────────────────────────────────────────────────

    def _drag_start(self, e):
        self._drag_data["idx"] = self.lb.nearest(e.y)

    def _drag_motion(self, e):
        idx = self.lb.nearest(e.y)
        src = self._drag_data["idx"]
        if src is None or idx == src:
            return
        self.files[src], self.files[idx] = self.files[idx], self.files[src]
        self._drag_data["idx"] = idx
        self._refresh_lb()
        self.lb.selection_clear(0, "end")
        self.lb.selection_set(idx)

    def _drag_end(self, e):
        self._drag_data["idx"] = None

    # ── file management ───────────────────────────────────────────────────────

    def _add_files(self):
        pat = " ".join("*" + e for e in sorted(AUDIO_EXT))
        paths = filedialog.askopenfilenames(
            title="Select audio files",
            filetypes=[("Audio", pat), ("All", "*.*")])
        self._insert(list(paths))

    def _add_folder(self):
        folder = filedialog.askdirectory(title="Select folder with audio files")
        if not folder:
            return
        paths = [os.path.join(folder, f) for f in os.listdir(folder)
                 if os.path.splitext(f)[1].lower() in AUDIO_EXT]
        self._insert(sorted(paths, key=created))

    def _insert(self, paths):
        existing = set(self.files)
        new = [p for p in paths if p not in existing]
        if not new:
            return
        self.files.extend(new)
        if not self.out_var.get():
            self.out_var.set(os.path.dirname(os.path.abspath(self.files[0])))
        self._refresh_lb()

    def _remove_sel(self):
        sel = list(self.lb.curselection())
        for i in reversed(sel):
            del self.files[i]
        self._refresh_lb()

    def _move(self, direction):
        sel = list(self.lb.curselection())
        if not sel:
            return
        if direction == -1 and sel[0] == 0:
            return
        if direction == 1 and sel[-1] == len(self.files) - 1:
            return
        for i in (sel if direction == 1 else reversed(sel)):
            j = i + direction
            self.files[i], self.files[j] = self.files[j], self.files[i]
        self._refresh_lb()
        self.lb.selection_clear(0, "end")
        for i in sel:
            self.lb.selection_set(i + direction)

    def _sort_date(self):
        self.files.sort(key=created)
        self._refresh_lb()

    def _clear(self):
        self.files.clear()
        self._refresh_lb()

    def _refresh_lb(self):
        self.lb.delete(0, "end")
        for i, p in enumerate(self.files, 1):
            self.lb.insert("end", f"  {i:>3}.  {os.path.basename(p)}")

    def _pick_outdir(self):
        d = filedialog.askdirectory(title="Choose output folder")
        if d:
            self.out_var.set(d)

    def _pick_image(self):
        pat = " ".join("*" + e for e in sorted(IMAGE_EXT))
        p = filedialog.askopenfilename(
            title="Select background image",
            filetypes=[("Image", pat), ("All", "*.*")])
        if p:
            self.image_path.set(p)

    def _toggle_video(self):
        state = "normal" if self.make_video.get() else "disabled"
        self.img_entry.configure(state=state)
        self.img_btn.configure(state=state)

    # ── ffmpeg check ──────────────────────────────────────────────────────────

    def _check_ffmpeg(self):
        if not self.ff:
            self._log("⚠  ffmpeg not found. Install it or run: pip install imageio-ffmpeg", "warn")

    # ── logging ───────────────────────────────────────────────────────────────

    def _log(self, msg, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n", tag or "")
        self.log.tag_config("warn",  foreground="#f1c40f")
        self.log.tag_config("error", foreground="#e74c3c")
        self.log.tag_config("ok",    foreground="#2ecc71")
        self.log.see("end")
        self.log.configure(state="disabled")

    # ── run ───────────────────────────────────────────────────────────────────

    def _run(self):
        if self.running:
            return
        if not self.ff:
            messagebox.showerror("ffmpeg missing",
                                 "ffmpeg not found.\nInstall it or run: pip install imageio-ffmpeg")
            return
        if not self.files:
            messagebox.showwarning("No files", "Add some audio files first.")
            return
        exts = {os.path.splitext(f)[1].lower() for f in self.files}
        if len(exts) > 1:
            messagebox.showerror("Mixed formats",
                                 "All files must be the same format for stream copy.\n"
                                 "Found: " + ", ".join(exts))
            return
        out_dir = self.out_var.get().strip()
        if not out_dir:
            messagebox.showwarning("No output folder", "Please select an output folder.")
            return

        self.running = True
        self.run_btn.configure(state="disabled")
        self.progress.start(12)
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

        threading.Thread(target=self._worker,
                         args=(list(self.files), out_dir,
                               self.make_video.get(),
                               self.image_path.get().strip()),
                         daemon=True).start()

    def _worker(self, files, out_dir, make_video, image_path):
        def log(msg, tag=None):
            self.after(0, self._log, msg, tag)

        try:
            ext = os.path.splitext(files[0])[1].lower()
            stamp = time.strftime("%Y%m%d_%H%M%S")
            os.makedirs(out_dir, exist_ok=True)
            combined = os.path.join(out_dir, "combined_" + stamp + ext)

            decode = ext == ".wav" and needs_decode(files)
            if decode:
                log("⚙  Compressed WAV detected — decoding to PCM 16-bit.")

            # ── merge ──
            log("⚙  Merging %d files…" % len(files))
            lst = concat_list(files)
            try:
                extra = ["-rf64", "auto"] if ext == ".wav" else []
                codec = ["-c:a", "pcm_s16le"] if decode else ["-c", "copy"]
                cmd = [self.ff, "-y", "-loglevel", "error", "-stats",
                       "-f", "concat", "-safe", "0", "-i", lst,
                       "-map", "0:a"] + codec + extra + [combined]
                rc = run_ff(cmd, log)
            finally:
                os.remove(lst)
            if rc != 0:
                log("✗  Merge failed.", "error"); return

            log("✔  Combined: %.1f MB  (input %.1f MB)" % (mb([combined]), mb(files)), "ok")

            # ── split ──
            log("⚙  Splitting into 60-min parts…")
            parts_dir = os.path.join(out_dir, "parts_" + stamp)
            os.makedirs(parts_dir)
            rc = run_ff([self.ff, "-y", "-loglevel", "error", "-stats",
                         "-i", combined,
                         "-map", "0:a", "-c", "copy",
                         "-f", "segment", "-segment_time", str(PART_SEC),
                         "-segment_start_number", "1", "-reset_timestamps", "1",
                         os.path.join(parts_dir, "part_%03d" + ext)], log)
            if rc != 0:
                log("✗  Split failed.", "error"); return
            parts = [os.path.join(parts_dir, f) for f in os.listdir(parts_dir)]
            log("✔  %d parts, %.1f MB  →  %s" % (len(parts), mb(parts), parts_dir), "ok")

            # ── video ──
            # Strategy: encode exactly 1 frame, then use -vf tpad to pad the
            # video stream to match the audio duration using that single frame
            # held for the entire duration. x264 only ever encodes one frame,
            # so this is as fast as it can possibly get regardless of audio length.
            if make_video:
                log("⚙  Creating video (1 frame total — fastest possible)…")
                video_out = os.path.join(out_dir, "combined_" + stamp + ".mp4")
                if ext in (".mp3", ".aac", ".m4a"):
                    a_codec = ["-c:a", "copy"]
                else:
                    a_codec = ["-c:a", "aac", "-b:a", "192k"]

                # Get audio duration in seconds via ffprobe
                # Try: same dir as ffmpeg, then PATH
                _ext = ".exe" if os.name == "nt" else ""
                _dir = os.path.dirname(os.path.abspath(self.ff))
                ffprobe = (shutil.which("ffprobe") or
                           os.path.join(_dir, "ffprobe" + _ext))
                probe = subprocess.run(
                    [ffprobe, "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", combined],
                    capture_output=True, text=True
                )
                try:
                    duration = float(probe.stdout.strip())
                except (ValueError, AttributeError):
                    duration = None

                if duration:
                    # tpad holds the last (only) frame for the remaining duration
                    tpad = "tpad=stop_mode=clone:stop_duration=%f" % duration
                else:
                    tpad = "tpad=stop_mode=clone:stop_duration=86400"  # 24h fallback
                    log("⚠  Could not probe duration, padding to 24 h", "warn")

                if image_path and os.path.isfile(image_path):
                    vid_input = ["-i", image_path]
                    scale = ("scale=1280:720:force_original_aspect_ratio=decrease,"
                             "pad=1280:720:(ow-iw)/2:(oh-ih)/2,")
                else:
                    vid_input = ["-f", "lavfi", "-i", "color=c=black:size=2x2:rate=1"]
                    scale = ""

                cmd = [self.ff, "-y", "-loglevel", "error", "-stats",
                       ] + vid_input + [
                       "-i", combined,
                       "-vf", scale + tpad,
                       "-c:v", "libx264", "-tune", "stillimage",
                       "-preset", "ultrafast", "-crf", "51",
                       "-pix_fmt", "yuv420p",
                       "-shortest"] + a_codec + [video_out]

                rc = run_ff(cmd, log)
                if rc != 0:
                    log("✗  Video export failed.", "error"); return
                log("✔  Video: %.1f MB  →  %s" % (mb([video_out]), video_out), "ok")

            log("🎉  All done!", "ok")

        except Exception as e:
            self.after(0, self._log, "✗  Error: %s" % e, "error")
        finally:
            self.after(0, self._finish)

    def _finish(self):
        self.progress.stop()
        self.run_btn.configure(state="normal")
        self.running = False


if __name__ == "__main__":
    app = App()
    app.mainloop()
