#!/usr/bin/env python3
"""
Merge audio files oldest -> newest, NO re-encoding (stream copy),
same format as the originals. Optionally also cut into 60 min parts.
Needs ffmpeg (on PATH, or `pip install imageio-ffmpeg`).

Usage: python audio_merger.py            (opens file picker)
Saves combined_*.ext and a parts_*/ folder with 60 min parts.
       python audio_merger.py a.mp3 b.mp3 ...
"""
import os
import sys
import shutil
import subprocess
import tempfile
import time

AUDIO_EXT = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus", ".wma"}
PART_SEC = 3600  # 60 min per part


def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        sys.exit("ffmpeg not found. Install it, or run: pip install imageio-ffmpeg")


def created(path):
    """Creation time. Falls back to modified time where the OS has no birth time."""
    st = os.stat(path)
    t = getattr(st, "st_birthtime", None)
    if t:
        return t
    if os.name == "nt":
        return st.st_ctime
    return st.st_mtime


def pick_files():
    given = [a for a in sys.argv[1:] if os.path.isfile(a)]
    if given:
        return given
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        pat = " ".join("*" + e for e in sorted(AUDIO_EXT))
        res = list(filedialog.askopenfilenames(
            title="Select audio files",
            filetypes=[("Audio", pat), ("All", "*.*")]))
        root.destroy()
        return res
    except Exception:
        folder = input("Folder with the audio files: ").strip().strip("\"'")
        if not os.path.isdir(folder):
            sys.exit("Not a folder.")
        return [os.path.join(folder, f) for f in os.listdir(folder)
                if os.path.splitext(f)[1].lower() in AUDIO_EXT]


def concat_list(paths):
    f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    for p in paths:
        p = os.path.abspath(p).replace("\\", "/").replace("'", "'\\''")
        f.write("file '%s'\n" % p)
    f.close()
    return f.name


def run(cmd):
    if subprocess.run(cmd).returncode != 0:
        sys.exit("ffmpeg failed.")


def merge(ff, files, out):
    lst = concat_list(files)
    try:
        extra = ["-rf64", "auto"] if out.lower().endswith(".wav") else []
        run([ff, "-y", "-loglevel", "error", "-stats",
             "-f", "concat", "-safe", "0", "-i", lst,
             "-map", "0:a", "-c", "copy"] + extra + [out])
    finally:
        os.remove(lst)


def split(ff, src, out_dir, ext):
    run([ff, "-y", "-loglevel", "error", "-stats", "-i", src,
         "-map", "0:a", "-c", "copy",
         "-f", "segment", "-segment_time", str(PART_SEC),
         "-segment_start_number", "1", "-reset_timestamps", "1",
         os.path.join(out_dir, "part_%03d" + ext)])


def mb(paths):
    return sum(os.path.getsize(p) for p in paths) / 1048576


def main():
    ff = find_ffmpeg()
    files = pick_files()
    if not files:
        sys.exit("No files selected.")
    files.sort(key=created)

    exts = {os.path.splitext(f)[1].lower() for f in files}
    if len(exts) > 1:
        sys.exit("Mixed formats (%s). Stream copy needs the same format." % ", ".join(exts))
    ext = exts.pop()

    print("Order (oldest -> newest):")
    for i, f in enumerate(files, 1):
        print(" %d. %s" % (i, os.path.basename(f)))

    stamp = time.strftime("%Y%m%d_%H%M%S")
    folder = os.path.dirname(os.path.abspath(files[0]))
    combined = os.path.join(folder, "combined_" + stamp + ext)
    merge(ff, files, combined)
    print("Combined: %.1f MB (input %.1f MB)" % (mb([combined]), mb(files)))
    print("Saved:", combined)

    parts_dir = os.path.join(folder, "parts_" + stamp)
    os.makedirs(parts_dir)
    split(ff, combined, parts_dir, ext)
    parts = [os.path.join(parts_dir, f) for f in os.listdir(parts_dir)]
    print("Parts: %d, %.1f MB" % (len(parts), mb(parts)))
    print("Parts saved in:", parts_dir)


if __name__ == "__main__":
    main()
