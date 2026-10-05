#!/usr/bin/env python3
"""ADPCM Log Studio: merge, trim and export long audio logs."""
import collections
import math
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.ticker import FuncFormatter

try:                                  # audio playback is optional: pip install sounddevice
    import sounddevice as sd
except Exception:
    sd = None

# ----------------------------------------------------------------- settings
AUDIO_EXTS = {".wav", ".wave", ".bwf", ".adpcm", ".au", ".aif", ".aiff", ".flac",
              ".mp3", ".ogg", ".opus", ".m4a", ".aac", ".wma"}
AUDIO_PART_SECONDS = 60 * 60          # max length of each .ogg part
VIDEO_PART_SECONDS = 10 * 60 * 60     # max length of each .mp4 part
FPS = 25
WAVE_COLORS = ["0x00E5FF", "0xFF4081"]
OVERVIEW_BINS = 4000
ZOOM_BINS = 1400
CHUNK = 1 << 20
ZOOMS = {"5 s": 5, "30 s": 30, "2 min": 120, "10 min": 600, "1 hour": 3600}
RESOLUTIONS = ["1280x720", "1920x1080", "854x480"]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

BG, PLOT_BG, WAVE, SEG_COL = "#1e1e1e", "#111111", "#4FC3F7", "#FFB300"


class Cancelled(Exception):
    pass


# ------------------------------------------------------------------ helpers
def fmt_time(t, dec=3):
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    out = f"{h}:{m:02d}:{s:02d}"
    return out + "." + f"{ms:03d}"[:dec] if dec else out


def parse_time(text):
    total = 0.0
    for p in text.strip().split(":"):
        total = total * 60 + float(p)
    return total


def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def find_ffmpeg():
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def file_time(path, mode):
    st = os.stat(path)
    if mode == "Modified time":
        return st.st_mtime
    birth = getattr(st, "st_birthtime", None)
    if birth:
        return birth
    if os.name == "nt":
        return st.st_ctime          # creation time on Windows
    return st.st_mtime              # Linux has no portable creation time


def drain(proc):
    """Collect the tail of ffmpeg's stderr without blocking the pipe."""
    tail = collections.deque(maxlen=20)

    def run():
        for line in iter(proc.stderr.readline, b""):
            tail.append(line.decode(errors="replace").rstrip())
    threading.Thread(target=run, daemon=True).start()
    return tail


def compute_peaks(path, ch, f0, f1, nbins):
    """Min/max envelope of channel 0 between frames f0..f1 (normalised)."""
    total = f1 - f0
    nbins = max(1, min(nbins, total))
    edges = np.linspace(f0, f1, nbins + 1).astype(np.int64)
    mn = np.zeros(nbins, np.float32)
    mx = np.zeros(nbins, np.float32)
    mm = np.memmap(path, dtype="<i2", mode="r")
    try:
        for i in range(nbins):
            seg = mm[edges[i] * ch:edges[i + 1] * ch:ch]
            if seg.size:
                mn[i] = seg.min()
                mx[i] = seg.max()
    finally:
        del mm
    return mn / 32768.0, mx / 32768.0, edges


def split_parts(segments, part_frames):
    parts, cur, room = [], [], part_frames
    for s, e in segments:
        while s < e:
            take = min(e - s, room)
            cur.append((s, s + take))
            s += take
            room -= take
            if room == 0:
                parts.append(cur)
                cur, room = [], part_frames
    if cur:
        parts.append(cur)
    return parts


def enable(widget, ok):
    widget.state(["!disabled"] if ok else ["disabled"])


# ---------------------------------------------------------------------- app
class App:
    def __init__(self, root):
        self.root = root
        self.ff = find_ffmpeg()
        self.q = queue.Queue()
        self.cancel_evt = threading.Event()
        self.proc = None
        self.busy = False
        self.master_ready = False
        self.tmpdir = None
        self.master_path = None
        self.rate, self.ch = 44100, 2
        self.total_frames = 0
        self.duration = 0.0
        self.boundaries = []
        self.ov = None
        self.peak = 1.0
        self.pos = 0.0
        self.start_f = None
        self.end_f = None
        self.segments = []
        self.out_dir = None
        self.bg_path = None
        self.ph_o = self.ph_z = None
        self._zoom_job = None
        self._zoom_gen = 0
        self._guard = False
        self._encoders = None
        self.playing = False
        self.stream = None
        self._pcm = None
        self._play_frame = 0
        self._play_lat = 0.0
        self._play_job = None
        self._plock = threading.Lock()
        self._last_recenter = 0.0
        self._zoom_win = (0.0, 0.0)
        self._build_ui()
        self.refresh_buttons()
        self.root.bind_all("<space>", self.on_space)
        self.root.bind_all("<KeyPress-c>", self.on_key_c)
        self.root.bind_all("<KeyPress-C>", self.on_key_c)
        self.root.bind_all("<ButtonRelease-1>", self._after_click)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(80, self.poll)
        if not self.ff:
            self.log("ffmpeg not found. Install it or run: pip install imageio-ffmpeg")

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        r = self.root
        r.title("ADPCM Log Studio")
        r.geometry(f"1180x{min(960, max(600, r.winfo_screenheight() - 90))}")
        r.minsize(980, 560)
        pad = dict(padx=6, pady=3)

        top = ttk.Frame(r)
        top.pack(fill="x", **pad)
        self.btn_folder = ttk.Button(top, text="Select Folder…", command=self.choose_folder)
        self.btn_folder.pack(side="left")
        ttk.Label(top, text="Sort by:").pack(side="left", padx=(10, 2))
        self.sort_var = tk.StringVar(value="Creation time")
        self.cmb_sort = ttk.Combobox(top, textvariable=self.sort_var, state="readonly", width=14,
                                     values=["Creation time", "Modified time"])
        self.cmb_sort.pack(side="left")
        self.btn_cancel = ttk.Button(top, text="Cancel", command=self.cancel)
        self.btn_cancel.pack(side="right")
        self.folder_lbl = ttk.Label(top, text="No folder selected")
        self.folder_lbl.pack(side="left", padx=10)

        bottom = ttk.Frame(r)                     # export controls: always stay visible
        bottom.pack(side="bottom", fill="x")

        lf = ttk.LabelFrame(r, text="Files in chronological order (oldest → newest)")
        lf.pack(fill="x", **pad)
        cols = (("n", "#", 50), ("name", "File", 380), ("time", "Timestamp", 160),
                ("size", "Size", 90), ("start", "Starts at (master)", 140))
        self.files_tv = ttk.Treeview(lf, columns=[c[0] for c in cols], show="headings", height=4)
        for cid, title, w in cols:
            self.files_tv.heading(cid, text=title)
            self.files_tv.column(cid, width=w, anchor="w" if cid == "name" else "center")
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.files_tv.yview)
        self.files_tv.configure(yscrollcommand=sb.set)
        self.files_tv.pack(side="left", fill="x", expand=True)
        sb.pack(side="right", fill="y")

        self.fig = Figure(figsize=(10, 4.4), dpi=100, facecolor=BG)
        self.ax_o = self.fig.add_subplot(2, 1, 1)
        self.ax_z = self.fig.add_subplot(2, 1, 2)
        self.fig.subplots_adjust(left=0.05, right=0.99, top=0.93, bottom=0.09, hspace=0.55)
        self.canvas = FigureCanvasTkAgg(self.fig, master=r)
        self.canvas.get_tk_widget().configure(height=140)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, **pad)
        self.canvas.mpl_connect("button_press_event", self.on_plot_click)
        self.draw_overview()
        self._clear_zoom()

        self.slider = ttk.Scale(r, from_=0, to=1, orient="horizontal", command=self.on_slide)
        self.slider.pack(fill="x", **pad)

        nav = ttk.Frame(r)
        nav.pack(fill="x", **pad)
        self.nav_widgets = [self.slider]
        self.btn_play = ttk.Button(nav, text="▶ Play", width=9, command=self.toggle_play)
        self.btn_play.pack(side="left", padx=(0, 8))
        self.nav_widgets.append(self.btn_play)
        self.time_var = tk.StringVar(value=fmt_time(0))
        self.ent_time = ttk.Entry(nav, textvariable=self.time_var, width=14, justify="center")
        self.ent_time.bind("<Return>", self.goto_typed)
        self.ent_time.pack(side="left")
        self.total_lbl = ttk.Label(nav, text="/ 0:00:00.000")
        self.total_lbl.pack(side="left", padx=(4, 12))
        self.nav_widgets.append(self.ent_time)
        for label, d in [("−1m", -60), ("−10s", -10), ("−1s", -1), ("−0.1s", -0.1),
                         ("+0.1s", 0.1), ("+1s", 1), ("+10s", 10), ("+1m", 60)]:
            b = ttk.Button(nav, text=label, width=6, command=lambda d=d: self.set_pos(self.pos + d))
            b.pack(side="left", padx=1)
            self.nav_widgets.append(b)
        ttk.Label(nav, text="Zoom window:").pack(side="left", padx=(14, 2))
        self.zoom_var = tk.StringVar(value="30 s")
        cz = ttk.Combobox(nav, textvariable=self.zoom_var, values=list(ZOOMS), state="readonly", width=7)
        cz.bind("<<ComboboxSelected>>", self.request_zoom)
        cz.pack(side="left")

        mk = ttk.Frame(r)
        mk.pack(fill="x", **pad)
        self.btn_start = ttk.Button(mk, text="Set Start Point", command=self.set_start)
        self.start_lbl = ttk.Label(mk, text="Start: —", width=22)
        self.btn_end = ttk.Button(mk, text="Set End Point", command=self.set_end)
        self.end_lbl = ttk.Label(mk, text="End: —", width=22)
        self.btn_save = ttk.Button(mk, text="Save Segment", command=self.save_segment)
        self.btn_remove = ttk.Button(mk, text="Remove Selected", command=self.remove_segments)
        self.btn_clear = ttk.Button(mk, text="Clear Queue", command=self.clear_segments)
        for w in (self.btn_start, self.start_lbl, self.btn_end, self.end_lbl, self.btn_save):
            w.pack(side="left", padx=3)
        self.btn_clear.pack(side="right", padx=3)
        ttk.Label(mk, text="Space: play/pause   C: start/end point",
                  foreground="#888888").pack(side="left", padx=14)
        self.btn_remove.pack(side="right", padx=3)
        self.nav_widgets += [self.btn_start, self.btn_end, self.btn_save, self.btn_remove, self.btn_clear]

        sf = ttk.LabelFrame(r, text="Segment queue (exported back-to-back, in this order)")
        sf.pack(fill="x", **pad)
        self.seg_tv = ttk.Treeview(sf, columns=("n", "start", "end", "len"), show="headings",
                                   height=3, selectmode="extended")
        for cid, title, w in (("n", "#", 50), ("start", "Start", 160), ("end", "End", 160), ("len", "Length", 160)):
            self.seg_tv.heading(cid, text=title)
            self.seg_tv.column(cid, width=w, anchor="center")
        sb2 = ttk.Scrollbar(sf, orient="vertical", command=self.seg_tv.yview)
        self.seg_tv.configure(yscrollcommand=sb2.set)
        self.seg_tv.pack(side="left", fill="x", expand=True)
        sb2.pack(side="right", fill="y")
        self.queue_lbl = ttk.Label(r, text="Queue empty")
        self.queue_lbl.pack(anchor="w", padx=8)

        ex = ttk.Frame(bottom)
        ex.pack(fill="x", **pad)
        self.btn_outdir = ttk.Button(ex, text="Output Folder…", command=self.choose_outdir)
        self.btn_outdir.pack(side="left")
        self.out_lbl = ttk.Label(ex, text="(not set)")
        self.out_lbl.pack(side="left", padx=8)

        ex2 = ttk.Frame(bottom)
        ex2.pack(fill="x", **pad)
        self.aprefix = tk.StringVar(value="log")
        self.vprefix = tk.StringVar(value="visualizer")
        self.q_var = tk.StringVar(value="10")
        self.res_var = tk.StringVar(value=RESOLUTIONS[0])
        self.btn_audio = ttk.Button(ex2, text="Export Audio (.ogg)", command=lambda: self.export("audio"))
        self.btn_audio.pack(side="left")
        ttk.Label(ex2, text="prefix").pack(side="left", padx=(6, 2))
        ttk.Entry(ex2, textvariable=self.aprefix, width=10).pack(side="left")
        ttk.Label(ex2, text="Vorbis quality").pack(side="left", padx=(8, 2))
        ttk.Spinbox(ex2, from_=0, to=10, width=4, textvariable=self.q_var).pack(side="left")
        self.btn_video = ttk.Button(ex2, text="Export Video Visualizer (.mp4)", command=lambda: self.export("video"))
        self.btn_video.pack(side="left", padx=(24, 0))
        ttk.Label(ex2, text="prefix").pack(side="left", padx=(6, 2))
        ttk.Entry(ex2, textvariable=self.vprefix, width=12).pack(side="left")
        cmb_res = ttk.Combobox(ex2, textvariable=self.res_var, values=RESOLUTIONS, state="readonly", width=10)
        cmb_res.pack(side="left", padx=6)
        for cb in (self.cmb_sort, cz, cmb_res):
            cb.bind("<<ComboboxSelected>>", lambda e: self.root.focus_set(), add="+")
        ttk.Button(ex2, text="Background…", command=self.choose_bg).pack(side="left")
        ttk.Button(ex2, text="Clear", width=6, command=self.clear_bg).pack(side="left", padx=2)
        self.bg_lbl = ttk.Label(ex2, text="black")
        self.bg_lbl.pack(side="left", padx=4)
        self.export_widgets = [self.btn_audio, self.btn_video]

        self.progress = ttk.Progressbar(bottom, maximum=1000)
        self.progress.pack(fill="x", **pad)
        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(bottom, textvariable=self.status_var).pack(anchor="w", padx=8)
        lg = ttk.Frame(bottom)
        lg.pack(fill="both", **pad)
        self.log_txt = tk.Text(lg, height=4, bg="#111", fg="#ddd", wrap="word", state="disabled")
        sb3 = ttk.Scrollbar(lg, orient="vertical", command=self.log_txt.yview)
        self.log_txt.configure(yscrollcommand=sb3.set)
        self.log_txt.pack(side="left", fill="both", expand=True)
        sb3.pack(side="right", fill="y")

    def _style_ax(self, ax, title):
        ax.set_facecolor(PLOT_BG)
        ax.tick_params(colors="#bbbbbb", labelsize=8)
        for sp in ax.spines.values():
            sp.set_color("#555555")
        ax.set_title(title, color="#bbbbbb", fontsize=9, loc="left")
        ax.set_yticks([])

    def _clear_zoom(self):
        self.ax_z.clear()
        self._style_ax(self.ax_z, "Zoom view (centered on playhead, click to seek)")
        self.ph_z = None
        self._zoom_win = (0.0, 0.0)
        self.canvas.draw_idle()

    # ------------------------------------------------------ thread plumbing
    def ui(self, fn, *a, **kw):
        self.q.put((fn, a, kw))

    def poll(self):
        try:
            while True:
                fn, a, kw = self.q.get_nowait()
                fn(*a, **kw)
        except queue.Empty:
            pass
        self.root.after(80, self.poll)

    def _log(self, msg):
        self.log_txt.configure(state="normal")
        self.log_txt.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        self.log_txt.see("end")
        self.log_txt.configure(state="disabled")

    def log(self, msg):
        self.ui(self._log, msg)

    def _set_progress(self, frac, text=None):
        self.progress.configure(value=max(0.0, min(1.0, frac)) * 1000)
        if text is not None:
            self.status_var.set(text)

    def prog(self, frac, text=None):
        self.ui(self._set_progress, frac, text)

    def refresh_buttons(self):
        enable(self.btn_folder, not self.busy)
        enable(self.cmb_sort, not self.busy)
        enable(self.btn_cancel, self.busy)
        for w in self.nav_widgets:
            enable(w, self.master_ready)
        for w in self.export_widgets:
            enable(w, self.master_ready and not self.busy)

    def _finish_busy(self):
        self.busy = False
        self.proc = None
        self.progress.configure(value=0)
        self.refresh_buttons()

    def cancel(self):
        self.cancel_evt.set()
        p = self.proc
        if p and p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass
        self.status_var.set("Cancelling…")

    # ------------------------------------------------------------ ingestion
    def choose_folder(self):
        d = filedialog.askdirectory(title="Select folder with audio files")
        if d:
            self.start_ingest(d)

    def _cleanup_master(self):
        self.stop_play(update=False)
        self._pcm = None
        if self.tmpdir:
            shutil.rmtree(self.tmpdir, ignore_errors=True)
        self.tmpdir = self.master_path = None

    def start_ingest(self, folder):
        if not self.ff:
            messagebox.showerror("ffmpeg missing", "ffmpeg was not found. Install it or pip install imageio-ffmpeg.")
            return
        self.master_ready = False
        self._zoom_gen += 1
        self._cleanup_master()
        self.segments.clear()
        self.start_f = self.end_f = None
        self.start_lbl.configure(text="Start: —")
        self.end_lbl.configure(text="End: —")
        self.ov = None
        self.boundaries = []
        self.duration = 0.0
        self.pos = 0.0
        self.files_tv.delete(*self.files_tv.get_children())
        self._refresh_segments()
        self.draw_overview()
        self._clear_zoom()
        self.folder_lbl.configure(text=folder)
        self.cancel_evt.clear()
        self.busy = True
        self.refresh_buttons()
        threading.Thread(target=self._ingest_worker, args=(folder, self.sort_var.get()), daemon=True).start()

    def _fill_files(self, entries):
        for i, (ts, name, _p, size) in enumerate(entries):
            self.files_tv.insert("", "end", iid=str(i), values=(
                i + 1, name, datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),
                human_size(size), "—"))

    def _probe_format(self, paths):
        for p in paths[:10]:
            try:
                r = subprocess.run([self.ff, "-hide_banner", "-i", p], capture_output=True,
                                   creationflags=NO_WINDOW)
            except Exception:
                continue
            txt = r.stderr.decode(errors="replace")
            m = re.search(r"Audio:.*?,\s*(\d+) Hz,\s*([^,\n]+)", txt)
            if m:
                spec = m.group(2).lower()
                mc = re.search(r"(\d+) channels", spec)
                if mc:
                    ch = int(mc.group(1))
                elif "mono" in spec:
                    ch = 1
                else:
                    ch = 2
                return int(m.group(1)), max(1, min(ch, 2))
        return None

    def _ingest_worker(self, folder, mode):
        try:
            self.prog(0, "Scanning folder…")
            self.log(f"Scanning {folder}")
            entries = []
            for e in os.scandir(folder):
                if e.is_file() and os.path.splitext(e.name)[1].lower() in AUDIO_EXTS:
                    entries.append((file_time(e.path, mode), e.name, e.path, e.stat().st_size))
            if not entries:
                raise RuntimeError("No audio files found in that folder.")
            entries.sort(key=lambda x: (x[0], x[1].lower()))
            if len({e[0] for e in entries}) == 1:
                self.log("Warning: all files share one timestamp; order falls back to file name.")
            self.ui(self._fill_files, entries)
            self.log(f"{len(entries)} files sorted oldest → newest by {mode.lower()}:")
            for i, (ts, name, _p, _s) in enumerate(entries, 1):
                self.log(f"  {i:>4}. {datetime.fromtimestamp(ts):%Y-%m-%d %H:%M:%S}  {name}")

            fmt = self._probe_format([e[2] for e in entries])
            if fmt is None:
                fmt = (44100, 2)
                self.log("Could not probe sample format; using 44100 Hz stereo.")
            rate, ch = fmt
            self.log(f"Master format: {rate} Hz, {ch} ch, 16-bit PCM")

            tmpdir = tempfile.mkdtemp(prefix="logstudio_")
            self.tmpdir = tmpdir
            master = os.path.join(tmpdir, "master.pcm")
            fb = 2 * ch
            total_bytes = 0
            boundaries = []
            n = len(entries)
            with open(master, "wb") as out:
                for i, (ts, name, path, _s) in enumerate(entries):
                    if self.cancel_evt.is_set():
                        raise Cancelled()
                    self.prog(i / n, f"Decoding {i + 1}/{n}: {name}")
                    cmd = [self.ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", path,
                           "-vn", "-sn", "-dn", "-map", "0:a:0", "-f", "s16le", "-acodec", "pcm_s16le",
                           "-ar", str(rate), "-ac", str(ch), "pipe:1"]
                    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            creationflags=NO_WINDOW)
                    self.proc = proc
                    tail = drain(proc)
                    got = 0
                    while True:
                        buf = proc.stdout.read(CHUNK)
                        if not buf:
                            break
                        out.write(buf)
                        got += len(buf)
                    rc = proc.wait()
                    if self.cancel_evt.is_set():
                        raise Cancelled()
                    if rc != 0 or got == 0:
                        out.flush()
                        out.truncate(total_bytes)
                        out.seek(total_bytes)
                        self.log(f"Skipped (decode failed): {name} {' | '.join(list(tail)[-2:])}")
                        continue
                    boundaries.append(total_bytes // fb)
                    self.ui(self.files_tv.set, str(i), "start", fmt_time(total_bytes // fb / rate))
                    total_bytes += got
            total_frames = total_bytes // fb
            if total_frames == 0:
                raise RuntimeError("Nothing could be decoded.")
            self.master_path, self.rate, self.ch = master, rate, ch
            self.total_frames = total_frames
            self.duration = total_frames / rate
            self.boundaries = boundaries
            self.prog(0.99, "Rendering master waveform…")
            mn, mx, edges = compute_peaks(master, ch, 0, total_frames, OVERVIEW_BINS)
            xs = (edges[:-1] + edges[1:]) / 2.0 / rate
            self.ov = (xs, mn, mx)
            self.peak = float(max(np.abs(mn).max(), np.abs(mx).max(), 1e-4))
            self.ui(self._ingest_done, total_bytes)
        except Cancelled:
            self.log("Ingestion cancelled.")
        except Exception as ex:
            self.log(f"ERROR: {ex}")
        finally:
            self.ui(self._finish_busy)

    def _ingest_done(self, total_bytes):
        try:
            self._pcm = np.memmap(self.master_path, dtype="<i2", mode="r")
        except Exception as ex:
            self._pcm = None
            self.log(f"Playback unavailable: {ex}")
        self.master_ready = True
        self.slider.configure(to=max(self.duration, 0.001))
        self.total_lbl.configure(text=f"/ {fmt_time(self.duration)}")
        self.log(f"Master ready: {fmt_time(self.duration)} ({human_size(total_bytes)} uncompressed PCM)")
        self.status_var.set("Master ready")
        self.draw_overview()
        self.set_pos(0.0)

    # --------------------------------------------------------- waveform/UI
    def draw_overview(self):
        ax = self.ax_o
        ax.clear()
        self._style_ax(ax, "Full timeline (click to seek; grey lines = file boundaries)")
        self.ph_o = None
        if self.ov is None:
            ax.set_xlim(0, 1)
            self.canvas.draw_idle()
            return
        xs, mn, mx = self.ov
        lim = self.peak * 1.05
        ax.fill_between(xs, mn, mx, color=WAVE, linewidth=0)
        if len(self.boundaries) > 1:
            ax.vlines([b / self.rate for b in self.boundaries[1:]], -lim, lim,
                      color="#888888", linewidth=0.5, alpha=0.4)
        for a, b in self.segments:
            ax.axvspan(a / self.rate, b / self.rate, color=SEG_COL, alpha=0.35)
        if self.start_f is not None:
            ax.axvline(self.start_f / self.rate, color="#00E676", linewidth=1.5)
        if self.end_f is not None:
            ax.axvline(self.end_f / self.rate, color="#FF5252", linewidth=1.5)
        self.ph_o = ax.axvline(self.pos, color="white", linewidth=1.2)
        ax.set_xlim(0, self.duration)
        ax.set_ylim(-lim, lim)
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: fmt_time(v, 0)))
        self.canvas.draw_idle()

    def request_zoom(self, *_):
        if not self.master_ready:
            return
        if self._zoom_job:
            self.root.after_cancel(self._zoom_job)
        self._zoom_job = self.root.after(120, self._zoom_start)

    def _zoom_start(self):
        self._zoom_job = None
        if not self.master_ready:
            return
        self._zoom_gen += 1
        gen = self._zoom_gen
        W = ZOOMS[self.zoom_var.get()]
        dur = self.duration
        if dur <= W:
            w0, w1 = 0.0, dur
        else:
            w0 = min(max(0.0, self.pos - W / 2), dur - W)
            w1 = w0 + W
        f0, f1 = int(w0 * self.rate), int(w1 * self.rate)
        if f1 - f0 < 1:
            return
        threading.Thread(target=self._zoom_worker,
                         args=(gen, w0, w1, f0, f1, self.master_path, self.ch, self.rate),
                         daemon=True).start()

    def _zoom_worker(self, gen, w0, w1, f0, f1, path, ch, rate):
        try:
            mn, mx, edges = compute_peaks(path, ch, f0, f1, ZOOM_BINS)
        except Exception:
            return
        xs = (edges[:-1] + edges[1:]) / 2.0 / rate
        self.ui(self._draw_zoom, gen, w0, w1, xs, mn, mx)

    def _draw_zoom(self, gen, w0, w1, xs, mn, mx):
        if gen != self._zoom_gen or not self.master_ready:
            return
        self._zoom_win = (w0, w1)
        ax = self.ax_z
        ax.clear()
        self._style_ax(ax, "Zoom view (centered on playhead, click to seek)")
        lim = self.peak * 1.05
        ax.fill_between(xs, mn, mx, color=WAVE, linewidth=0)
        for a, b in self.segments:
            s, e = a / self.rate, b / self.rate
            if e > w0 and s < w1:
                ax.axvspan(max(s, w0), min(e, w1), color=SEG_COL, alpha=0.35)
        for f, col in ((self.start_f, "#00E676"), (self.end_f, "#FF5252")):
            if f is not None and w0 <= f / self.rate <= w1:
                ax.axvline(f / self.rate, color=col, linewidth=1.8)
        self.ph_z = ax.axvline(self.pos, color="white", linewidth=1.2)
        ax.set_xlim(w0, w1)
        ax.set_ylim(-lim, lim)
        dec = 1 if (w1 - w0) <= 120 else 0
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: fmt_time(v, dec)))
        self.canvas.draw_idle()

    # ----------------------------------------------------------- navigation
    def set_pos(self, t, from_slider=False, from_play=False):
        if not self.master_ready:
            return
        t = min(max(0.0, float(t)), self.duration)
        self.pos = t
        if self.playing and not from_play:        # user seeked while audio is playing
            with self._plock:
                self._play_frame = int(t * self.rate)
        if not from_slider:
            self._guard = True
            try:
                self.slider.set(t)
            finally:
                self._guard = False
        if not (from_play and self.root.focus_get() is self.ent_time):
            self.time_var.set(fmt_time(t))
        for ln in (self.ph_o, self.ph_z):
            if ln is not None:
                ln.set_xdata([t, t])
        self.canvas.draw_idle()
        if not from_play:
            self.request_zoom()

    def on_slide(self, v):
        if self._guard or not self.master_ready:
            return
        self.set_pos(float(v), from_slider=True)

    def goto_typed(self, _e=None):
        try:
            self.set_pos(parse_time(self.time_var.get()))
            self.root.focus_set()
        except ValueError:
            self.root.bell()
            self.time_var.set(fmt_time(self.pos))

    def on_plot_click(self, ev):
        if self.master_ready and ev.inaxes in (self.ax_o, self.ax_z) and ev.xdata is not None:
            self.set_pos(ev.xdata)

    # ------------------------------------------------- playback & shortcuts
    def _typing(self):
        return isinstance(self.root.focus_get(), (tk.Entry, ttk.Entry, tk.Text))

    def on_space(self, _e=None):
        if self._typing() or isinstance(self.root.focus_get(), ttk.Button) or not self.master_ready:
            return None
        self.toggle_play()
        return "break"

    def on_key_c(self, _e=None):
        if self._typing() or not self.master_ready:
            return None
        self.toggle_point()
        return "break"

    def _after_click(self, e):
        # buttons / slider / plot would otherwise keep keyboard focus and swallow Space
        if isinstance(e.widget, (ttk.Button, ttk.Scale)) or e.widget is self.canvas.get_tk_widget():
            self.root.after_idle(self.root.focus_set)

    def toggle_point(self):
        """C key: 1st press sets the Start point, 2nd press sets the End point, 3rd starts a new Start."""
        if self.start_f is None or self.end_f is not None:
            self.end_f = None
            self.end_lbl.configure(text="End: \u2014")
            self.set_start()
        elif int(round(self.pos * self.rate)) <= self.start_f:
            self.root.bell()
            self.status_var.set("End point must be after the start point")
        else:
            self.set_end()

    def toggle_play(self):
        if self.playing:
            self.stop_play()
        else:
            self.start_play()

    def start_play(self):
        if not self.master_ready or self.playing:
            return
        if sd is None:
            messagebox.showinfo("Playback", "Playback needs the 'sounddevice' package.\n\n"
                                "Install it with:   pip install sounddevice")
            return
        if self._pcm is None:
            return
        if self.pos >= self.duration - 0.05:
            self.set_pos(0.0)
        with self._plock:
            self._play_frame = int(self.pos * self.rate)
        try:
            self.stream = sd.RawOutputStream(
                samplerate=self.rate, channels=self.ch, dtype="int16", blocksize=2048,
                latency="low", callback=self._audio_cb,
                finished_callback=lambda: self.ui(self._play_finished))
            self.stream.start()
        except Exception as ex:
            self.stream = None
            self.log(f"Playback error: {ex}")
            return
        try:
            self._play_lat = float(self.stream.latency)
        except Exception:
            self._play_lat = 0.0
        self.playing = True
        self.btn_play.configure(text="\u2016 Pause")
        self._last_recenter = time.monotonic()
        self._play_tick()

    def _audio_cb(self, outdata, frames, time_info, status):
        """Runs on the audio thread: copy the next block of master PCM to the sound card."""
        pcm, ch = self._pcm, self.ch
        if pcm is None:
            outdata[:] = bytes(len(outdata))
            raise sd.CallbackStop
        with self._plock:
            f = self._play_frame
            n = max(0, min(frames, self.total_frames - f))
            self._play_frame = f + n
        nb = n * ch * 2
        if n:
            outdata[:nb] = pcm[f * ch:(f + n) * ch].tobytes()
        if n < frames:
            outdata[nb:] = bytes(len(outdata) - nb)
            raise sd.CallbackStop

    def _play_tick(self):
        if not self.playing:
            return
        with self._plock:
            f = self._play_frame
        t = min(max(0.0, f / self.rate - self._play_lat), self.duration)
        self.set_pos(t, from_play=True)
        w0, w1 = self._zoom_win
        W = w1 - w0
        if 0 < W < self.duration:                  # keep the zoom view following the playhead
            need = t < w0 or t > w1 or (t > w1 - 0.1 * W and w1 < self.duration - 1e-3)
            if need and time.monotonic() - self._last_recenter > 0.4:
                self._last_recenter = time.monotonic()
                self.request_zoom()
        self._play_job = self.root.after(100, self._play_tick)

    def _play_finished(self):
        if self.playing:                           # reached the end of the master
            self.stop_play(at_end=True)

    def stop_play(self, update=True, at_end=False):
        was = self.playing
        self.playing = False
        if self._play_job is not None:
            try:
                self.root.after_cancel(self._play_job)
            except Exception:
                pass
            self._play_job = None
        s, self.stream = self.stream, None
        if s is not None:
            try:
                s.abort()
                s.close()
            except Exception:
                pass
        self.btn_play.configure(text="\u25b6 Play")
        if was and update and self.master_ready:
            if at_end:
                t = self.duration
            else:
                with self._plock:
                    t = self._play_frame / self.rate - self._play_lat
            self.set_pos(max(0.0, t))

    # ------------------------------------------------------------- segments
    def _overlay_changed(self):
        self.draw_overview()
        self.request_zoom()

    def set_start(self):
        self.start_f = int(round(self.pos * self.rate))
        self.start_lbl.configure(text=f"Start: {fmt_time(self.start_f / self.rate)}")
        self._overlay_changed()

    def set_end(self):
        self.end_f = int(round(self.pos * self.rate))
        self.end_lbl.configure(text=f"End: {fmt_time(self.end_f / self.rate)}")
        self._overlay_changed()

    def save_segment(self):
        if self.start_f is None or self.end_f is None:
            messagebox.showwarning("Segment", "Set both a start and an end point first.")
            return
        if self.end_f <= self.start_f:
            messagebox.showwarning("Segment", "End point must be after the start point.")
            return
        self.segments.append((self.start_f, min(self.end_f, self.total_frames)))
        self.start_f = self.end_f = None
        self.start_lbl.configure(text="Start: —")
        self.end_lbl.configure(text="End: —")
        self._refresh_segments()
        self._overlay_changed()

    def remove_segments(self):
        idx = sorted((self.seg_tv.index(i) for i in self.seg_tv.selection()), reverse=True)
        for i in idx:
            del self.segments[i]
        self._refresh_segments()
        self._overlay_changed()

    def clear_segments(self):
        self.segments.clear()
        self._refresh_segments()
        self._overlay_changed()

    def _refresh_segments(self):
        self.seg_tv.delete(*self.seg_tv.get_children())
        total = 0
        for i, (a, b) in enumerate(self.segments, 1):
            total += b - a
            self.seg_tv.insert("", "end", values=(
                i, fmt_time(a / self.rate), fmt_time(b / self.rate), fmt_time((b - a) / self.rate)))
        if not self.segments:
            self.queue_lbl.configure(text="Queue empty")
            return
        secs = total / self.rate
        self.queue_lbl.configure(text=(
            f"{len(self.segments)} segment(s), total {fmt_time(secs)}  →  "
            f"{math.ceil(secs / AUDIO_PART_SECONDS)} audio part(s), "
            f"{math.ceil(secs / VIDEO_PART_SECONDS)} video part(s)"))

    # --------------------------------------------------------------- export
    def choose_outdir(self):
        d = filedialog.askdirectory(title="Select output folder")
        if d:
            self.out_dir = d
            self.out_lbl.configure(text=d)

    def choose_bg(self):
        p = filedialog.askopenfilename(title="Background image",
                                       filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.webp"), ("All", "*.*")])
        if p:
            self.bg_path = p
            self.bg_lbl.configure(text=os.path.basename(p))

    def clear_bg(self):
        self.bg_path = None
        self.bg_lbl.configure(text="black")

    def _has_encoder(self, name):
        if self._encoders is None:
            r = subprocess.run([self.ff, "-hide_banner", "-encoders"], capture_output=True,
                               creationflags=NO_WINDOW)
            self._encoders = r.stdout.decode(errors="replace")
        return name in self._encoders

    def export(self, kind):
        if not self.segments:
            messagebox.showinfo("Export", "Add at least one segment to the queue first.")
            return
        if not self.out_dir:
            self.choose_outdir()
            if not self.out_dir:
                return
        try:
            quality = min(10, max(0, int(float(self.q_var.get()))))
        except ValueError:
            quality = 10
        opts = dict(
            kind=kind, out_dir=self.out_dir, quality=quality,
            prefix=(self.aprefix.get() if kind == "audio" else self.vprefix.get()).strip()
            or ("log" if kind == "audio" else "visualizer"),
            res=self.res_var.get(), bg=self.bg_path)
        self.cancel_evt.clear()
        self.busy = True
        self.refresh_buttons()
        threading.Thread(target=self._export_worker, args=(list(self.segments), opts), daemon=True).start()

    def _build_cmd(self, opts, out):
        rate, ch = self.rate, self.ch
        base = [self.ff, "-y", "-hide_banner", "-loglevel", "error",
                "-f", "s16le", "-ar", str(rate), "-ac", str(ch), "-i", "pipe:0"]
        if opts["kind"] == "audio":
            return base + ["-c:a", "libvorbis", "-q:a", str(opts["quality"]), out]
        W, H = opts["res"].split("x")
        colors = "|".join(WAVE_COLORS[i % len(WAVE_COLORS)] for i in range(ch))
        if opts["bg"]:
            bg_in = ["-loop", "1", "-framerate", str(FPS), "-i", opts["bg"]]
        else:
            bg_in = ["-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS}"]
        fc = (f"[0:a]showwaves=s={W}x{H}:mode=cline:rate={FPS}:colors={colors},format=rgba[wv];"
              f"[1:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,format=rgba[bg];"
              f"[bg][wv]overlay=shortest=1:format=auto,format=yuv420p[v]")
        return base + bg_in + ["-filter_complex", fc, "-map", "[v]", "-map", "0:a",
                               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(FPS),
                               "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out]

    def _export_worker(self, segs, opts):
        kind = opts["kind"]
        ext = "ogg" if kind == "audio" else "mp4"
        try:
            need = "libvorbis" if kind == "audio" else "libx264"
            if not self._has_encoder(need):
                raise RuntimeError(f"This ffmpeg build has no {need} encoder.")
            fb = 2 * self.ch
            part_sec = AUDIO_PART_SECONDS if kind == "audio" else VIDEO_PART_SECONDS
            parts = split_parts(segs, part_sec * self.rate)
            grand = sum(b - a for p in parts for a, b in p)
            done = 0
            self.log(f"Export {kind}: {fmt_time(grand / self.rate)} in {len(parts)} part(s)")
            with open(self.master_path, "rb") as src:
                for pi, ranges in enumerate(parts, 1):
                    out = os.path.join(opts["out_dir"], f"{opts['prefix']}_part{pi}.{ext}")
                    self.log(f"Part {pi}/{len(parts)} → {out}")
                    proc = subprocess.Popen(self._build_cmd(opts, out), stdin=subprocess.PIPE,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                            creationflags=NO_WINDOW)
                    self.proc = proc
                    tail = drain(proc)
                    ok = False
                    last = 0.0
                    try:
                        for a, b in ranges:
                            src.seek(a * fb)
                            rem = (b - a) * fb
                            while rem > 0:
                                if self.cancel_evt.is_set():
                                    raise Cancelled()
                                buf = src.read(min(CHUNK, rem))
                                if not buf:
                                    break
                                try:
                                    proc.stdin.write(buf)
                                except (BrokenPipeError, OSError):
                                    if self.cancel_evt.is_set():
                                        raise Cancelled()
                                    time.sleep(0.2)
                                    raise RuntimeError("ffmpeg stopped: " + " | ".join(tail))
                                rem -= len(buf)
                                done += len(buf) // fb
                                now = time.monotonic()
                                if now - last > 0.3:
                                    last = now
                                    self.prog(done / grand,
                                              f"Part {pi}/{len(parts)}: {done / grand * 100:.1f}% of {kind} export")
                        proc.stdin.close()
                        rc = proc.wait()
                        if self.cancel_evt.is_set():
                            raise Cancelled()
                        if rc != 0:
                            raise RuntimeError(f"ffmpeg exit code {rc}: " + " | ".join(tail))
                        ok = True
                    finally:
                        if not ok:
                            if proc.poll() is None:
                                proc.kill()
                            try:
                                proc.stdin.close()
                            except Exception:
                                pass
                            proc.wait()
                            try:
                                os.remove(out)
                            except OSError:
                                pass
                    self.log(f"Saved {os.path.basename(out)} ({human_size(os.path.getsize(out))})")
            self.prog(1.0, "Export finished")
            self.log("Export finished.")
        except Cancelled:
            self.log("Export cancelled.")
        except Exception as ex:
            self.log(f"ERROR: {ex}")
        finally:
            self.ui(self._finish_busy)

    # ---------------------------------------------------------------- close
    def on_close(self):
        self.cancel_evt.set()
        p = self.proc
        if p and p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass
        self._cleanup_master()
        self.root.destroy()


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
