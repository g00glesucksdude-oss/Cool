import json
import os
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# ---- Settings ----
AUDIO_CHUNK_SECONDS = 3600                     # 1 hour audio chunks
MAX_VIDEO_SECONDS = 11 * 3600 + 30 * 60        # 11h30m = 41400 seconds
VIDEO_SIZE = (1280, 720)                       # video resolution
VIDEO_FPS = 1                                  # 1 fps keeps encoding near-instant
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "splitter_config.json")

AUDIO_EXTS = [".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".oga", ".opus",
              ".wma", ".aiff", ".aif", ".ac3", ".amr", ".mka", ".wv", ".ape", ".caf"]
VIDEO_EXTS = [".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".m4v",
              ".ts", ".3gp", ".mpg", ".mpeg"]

# Audio formats that can be copied straight into an MP4 without re-encoding
MP4_COPYABLE = {".mp3", ".m4a", ".aac", ".ac3"}

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------------- Config (remembers default PNG) ----------------
def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


# ---------------- FFmpeg work ----------------
def run_ffmpeg(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True, creationflags=NO_WINDOW)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-1500:] or "FFmpeg failed.")


def split_audio_by_hour(input_path, out_dir):
    """Splits audio into 1-hour chunks without re-encoding."""
    base, ext = os.path.splitext(os.path.basename(input_path))
    is_video_input = ext.lower() in VIDEO_EXTS
    # Video containers -> keep just the audio track in Matroska audio (.mka)
    out_ext = ".mka" if is_video_input else ext
    pattern = os.path.join(out_dir, f"{base}_part_%02d{out_ext}")

    cmd = ["ffmpeg", "-y", "-i", input_path, "-vn", "-map", "0:a:0",
           "-f", "segment", "-segment_time", str(AUDIO_CHUNK_SECONDS),
           "-reset_timestamps", "1", "-c", "copy", pattern]
    run_ffmpeg(cmd)


def audio_to_video(input_path, out_dir, png_path=None):
    """
    Makes an MP4 (PNG image or black screen) with the audio.
    Cuts to a new file every 11h30m.
    """
    base, ext = os.path.splitext(os.path.basename(input_path))
    pattern = os.path.join(out_dir, f"{base}_video_part_%02d.mp4")
    w, h = VIDEO_SIZE

    if png_path:
        video_input = ["-loop", "1", "-framerate", str(VIDEO_FPS), "-i", png_path]
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
              f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,format=yuv420p")
    else:
        video_input = ["-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r={VIDEO_FPS}"]
        vf = "format=yuv420p"

    if ext.lower() in MP4_COPYABLE:
        audio_args = ["-c:a", "copy"]
    else:
        audio_args = ["-c:a", "aac", "-b:a", "192k"]

    cmd = ["ffmpeg", "-y", *video_input, "-i", input_path,
           "-map", "0:v", "-map", "1:a:0",
           "-vf", vf,
           "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
           "-force_key_frames", f"expr:gte(t,n_forced*{MAX_VIDEO_SECONDS})",
           *audio_args,
           "-shortest",
           "-f", "segment", "-segment_time", str(MAX_VIDEO_SECONDS),
           "-segment_format", "mp4",
           "-segment_format_options", "movflags=+faststart",
           "-reset_timestamps", "1",
           pattern]
    run_ffmpeg(cmd)


# ---------------- GUI ----------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Audio Splitter & Video Maker")
        self.geometry("660x440")
        self.resizable(False, False)
        self.cfg = load_config()

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar(value=self.cfg.get("output_dir", ""))
        self.png_var = tk.StringVar(value=self.cfg.get("default_png", ""))
        self.make_chunks = tk.BooleanVar(value=True)
        self.make_video = tk.BooleanVar(value=True)
        self.use_png = tk.BooleanVar(value=bool(self.cfg.get("default_png")))

        pad = {"padx": 10, "pady": 6}
        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True, padx=10, pady=10)

        # Input file
        ttk.Label(frm, text="Audio / video file:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.input_var, width=48).grid(row=0, column=1, **pad)
        ttk.Button(frm, text="Browse...", command=self.pick_input).grid(row=0, column=2, **pad)

        # Output folder
        ttk.Label(frm, text="Output folder:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.output_var, width=48).grid(row=1, column=1, **pad)
        ttk.Button(frm, text="Browse...", command=self.pick_output).grid(row=1, column=2, **pad)

        # PNG
        ttk.Label(frm, text="Video image (PNG):").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.png_var, width=48).grid(row=2, column=1, **pad)
        ttk.Button(frm, text="Browse...", command=self.pick_png).grid(row=2, column=2, **pad)

        png_row = ttk.Frame(frm)
        png_row.grid(row=3, column=0, columnspan=3, sticky="w", padx=10)
        ttk.Checkbutton(png_row, text="Use this PNG (otherwise black screen)",
                        variable=self.use_png).pack(side="left")
        ttk.Button(png_row, text="Set as default", command=self.set_default_png).pack(side="left", padx=8)
        ttk.Button(png_row, text="Clear default", command=self.clear_default_png).pack(side="left")

        # Options
        opts = ttk.LabelFrame(frm, text="What to make")
        opts.grid(row=4, column=0, columnspan=3, sticky="we", padx=10, pady=12)
        ttk.Checkbutton(opts, text="Hourly audio chunks (no re-encoding)",
                        variable=self.make_chunks).pack(anchor="w", padx=8, pady=3)
        ttk.Checkbutton(opts, text="Video version (auto-splits every 11h30m)",
                        variable=self.make_video).pack(anchor="w", padx=8, pady=3)

        # Start + status
        self.start_btn = ttk.Button(frm, text="Start", command=self.start)
        self.start_btn.grid(row=5, column=0, columnspan=3, pady=6)
        self.status = tk.StringVar(value="Ready.")
        ttk.Label(frm, textvariable=self.status, wraplength=610, foreground="#444").grid(
            row=6, column=0, columnspan=3, sticky="w", padx=10)

    # --- pickers ---
    def pick_input(self):
        audio = " ".join(f"*{e}" for e in AUDIO_EXTS)
        video = " ".join(f"*{e}" for e in VIDEO_EXTS)
        path = filedialog.askopenfilename(
            title="Select audio or video file",
            filetypes=[("Audio & video", f"{audio} {video}"),
                       ("Audio", audio), ("Video", video), ("All files", "*.*")])
        if path:
            self.input_var.set(path)
            if not self.output_var.get():
                self.output_var.set(os.path.join(os.path.dirname(path), "output"))

    def pick_output(self):
        path = filedialog.askdirectory(title="Select output folder")
        if path:
            self.output_var.set(path)
            self.cfg["output_dir"] = path
            save_config(self.cfg)

    def pick_png(self):
        path = filedialog.askopenfilename(title="Select PNG image",
                                          filetypes=[("PNG image", "*.png"), ("All files", "*.*")])
        if path:
            self.png_var.set(path)
            self.use_png.set(True)

    def set_default_png(self):
        path = self.png_var.get()
        if not path or not os.path.isfile(path):
            messagebox.showwarning("No PNG", "Select a PNG first.")
            return
        self.cfg["default_png"] = path
        save_config(self.cfg)
        self.status.set("Default PNG saved.")

    def clear_default_png(self):
        self.cfg.pop("default_png", None)
        save_config(self.cfg)
        self.png_var.set("")
        self.use_png.set(False)
        self.status.set("Default PNG cleared.")

    # --- run ---
    def start(self):
        if not shutil.which("ffmpeg"):
            messagebox.showerror("FFmpeg missing", "FFmpeg was not found. Install it and add it to PATH.")
            return
        input_path = self.input_var.get()
        out_dir = self.output_var.get()
        if not os.path.isfile(input_path):
            messagebox.showwarning("No file", "Select an audio or video file first.")
            return
        if not out_dir:
            messagebox.showwarning("No folder", "Select an output folder.")
            return
        if not (self.make_chunks.get() or self.make_video.get()):
            messagebox.showwarning("Nothing to do", "Tick at least one option.")
            return
        png = self.png_var.get() if self.use_png.get() else None
        if png and not os.path.isfile(png):
            messagebox.showwarning("PNG not found", "The selected PNG doesn't exist.")
            return

        self.start_btn.config(state="disabled")
        threading.Thread(target=self.worker, args=(input_path, out_dir, png), daemon=True).start()

    def worker(self, input_path, out_dir, png):
        try:
            os.makedirs(out_dir, exist_ok=True)
            if self.make_chunks.get():
                self.set_status("Splitting audio into hourly chunks...")
                chunk_dir = os.path.join(out_dir, "output_chunks")
                os.makedirs(chunk_dir, exist_ok=True)
                split_audio_by_hour(input_path, chunk_dir)
            if self.make_video.get():
                self.set_status("Creating video version...")
                video_dir = os.path.join(out_dir, "output_videos")
                os.makedirs(video_dir, exist_ok=True)
                audio_to_video(input_path, video_dir, png)
            self.set_status(f"Done! Files saved in: {out_dir}")
            self.after(0, lambda: messagebox.showinfo("Done", "All finished."))
        except Exception as e:
            err = str(e)
            self.set_status("Error - see message.")
            self.after(0, lambda: messagebox.showerror("FFmpeg error", err))
        finally:
            self.after(0, lambda: self.start_btn.config(state="normal"))

    def set_status(self, text):
        self.after(0, lambda: self.status.set(text))


if __name__ == "__main__":
    App().mainloop()
