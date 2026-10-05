#!/usr/bin/env python3
"""
1. Merges the selected audio oldest -> newest (stream copy, same format;
   compressed WAV/ADPCM is decoded to PCM).
2. Saves combined_*.ext and a parts_*/ folder (default 60 min each).
3. Sends every part to Gemini and saves the transcripts (part_001.txt ... + transcript_*.txt).
4. Renders visualizer video(s) from the COMBINED audio, a new video every 10 h.

Needs ffmpeg (on PATH, or `pip install imageio-ffmpeg`).
Change the settings below, then run:  python audio_merger.py
"""
import os
import sys
import re
import json
import math
import shutil
import subprocess
import tempfile
import time
import urllib.request
import urllib.error

# ======================= SETTINGS =======================
PART_MINUTES = 60          # length of each part

TRANSCRIBE = True          # send each part to Gemini
GEMINI_API_KEY = ""        # paste key here, or env GEMINI_API_KEY, or a gemini_key.txt next to this script
GEMINI_MODELS = ["gemini-3.5-flash"]  # tried in order if a name isn't found
TRANSCRIBE_PROMPT = ("Transcribe this audio word for word. If there is more than one "
                     "speaker, label them (Speaker 1, Speaker 2). "
                     "Output only the transcript, no commentary.")

VISUALIZER = True          # make visualizer video(s) from the combined audio
VIDEO_HOURS = 10           # start a new video (part 2, 3, ...) every N hours
COVER_IMAGE = ""           # "" = none | "ask" = open a picker | or a path to a picture
W, H, FPS, CRF = 640, 360, 10, 38   # small on purpose (keeps video size down)
# ========================================================

AUDIO_EXT = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus", ".wma"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
PART_SEC = PART_MINUTES * 60
TAGS = {1: "PCM", 3: "float PCM", 2: "MS ADPCM", 17: "IMA ADPCM", 85: "MP3"}
API_BASE = "https://generativelanguage.googleapis.com"
MIME = {".mp3": "audio/mp3", ".wav": "audio/wav", ".aac": "audio/aac",
        ".ogg": "audio/ogg", ".flac": "audio/flac"}


# ---------------------------------------------------------------- basics
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
    print("WARNING: no birth time available for %s, using modified time for sort order." % os.path.basename(path))
    return st.st_mtime


def tk_dialog(multi, title, exts):
    """List/str if used, empty if cancelled, None if no GUI available."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        types = [("Files", " ".join("*" + e for e in sorted(exts))), ("All", "*.*")]
        fn = filedialog.askopenfilenames if multi else filedialog.askopenfilename
        res = fn(title=title, filetypes=types)
        root.destroy()
        return list(res) if multi else res
    except Exception:
        return None


def pick_files():
    given = [a for a in sys.argv[1:] if os.path.isfile(a)]
    if given:
        return given
    res = tk_dialog(True, "Select audio files", AUDIO_EXT)
    if res is None:
        folder = input("Folder with the audio files: ").strip().strip("\"'")
        if not os.path.isdir(folder):
            sys.exit("Not a folder.")
        res = [os.path.join(folder, f) for f in os.listdir(folder)
               if os.path.splitext(f)[1].lower() in AUDIO_EXT]
    return res


def resolve_cover():
    c = (COVER_IMAGE or "").strip()
    if c.lower() == "ask":
        c = tk_dialog(False, "Pick cover picture (Cancel = none)", IMG_EXT)
        if c is None:
            c = input("Cover image path (Enter = none): ").strip().strip("\"'")
    return c if c and os.path.isfile(c) else None


# ---------------------------------------------------------------- WAV info
def wav_info(path):
    """(format_tag, channels, rate, bits) of a WAV, or None.
    Looks inside WAVE_FORMAT_EXTENSIBLE to find the real format."""
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
                    d = f.read(min(size, 40))
                    tag = int.from_bytes(d[0:2], "little")
                    ch = int.from_bytes(d[2:4], "little")
                    rate = int.from_bytes(d[4:8], "little")
                    bits = int.from_bytes(d[14:16], "little")
                    if tag == 0xFFFE and len(d) >= 26:
                        tag = int.from_bytes(d[24:26], "little")
                    return tag, ch, rate, bits
                f.seek(size + (size & 1), 1)
    except (OSError, ValueError):
        return None


def describe(info):
    if not info:
        return "unknown"
    tag, ch, rate, bits = info
    return "%s, %d-bit, %d Hz, %d ch" % (TAGS.get(tag, "codec 0x%x" % tag), bits, rate, ch)


def needs_decode(infos):
    """Anything that is not plain PCM / float PCM gets decoded to PCM."""
    return any(i is None or i[0] not in (1, 3) for i in infos)


# ---------------------------------------------------------------- ffmpeg steps
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


def duration(ff, path):
    r = subprocess.run([ff, "-hide_banner", "-i", path], text=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", r.stderr)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None


def merge(ff, files, out, decode=False):
    lst = concat_list(files)
    try:
        extra = ["-rf64", "auto"] if out.lower().endswith(".wav") else []
        codec = ["-c:a", "pcm_s16le"] if decode else ["-c", "copy"]
        run([ff, "-y", "-loglevel", "error", "-stats",
             "-f", "concat", "-safe", "0", "-i", lst,
             "-map", "0:a"] + codec + extra + [out])
    finally:
        os.remove(lst)


def split(ff, src, out_dir, ext):
    run([ff, "-y", "-loglevel", "error", "-stats", "-i", src,
         "-map", "0:a", "-c", "copy",
         "-f", "segment", "-segment_time", str(PART_SEC),
         "-segment_start_number", "1", "-reset_timestamps", "1",
         os.path.join(out_dir, "part_%03d" + ext)])


def make_video(ff, src, start, length, cover, out):
    cmd = [ff, "-y", "-loglevel", "error", "-stats", "-ss", str(start)]
    if length:
        cmd += ["-t", str(length)]
    cmd += ["-i", src]
    if cover:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-i", cover]
        bg = ("[1:v]scale=%d:%d:force_original_aspect_ratio=increase,"
              "crop=%d:%d,setsar=1,fps=%d[bg];" % (W, H, W, H, FPS))
        tune = ["-tune", "stillimage"]
    else:
        bg = "color=c=0x14141c:s=%dx%d:r=%d[bg];" % (W, H, FPS)
        tune = []
    wave = ("[0:a]showwaves=s=%dx%d:mode=cline:scale=sqrt:draw=full:rate=%d:colors=0x4fc3f7,"
            "format=rgba,colorkey=0x000000:0.3:0.1[w];" % (W, H // 2, FPS))
    fc = bg + wave + "[bg][w]overlay=(W-w)/2:(H-h)/2:shortest=1,format=yuv420p[v]"
    cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "0:a",
            "-c:v", "libx264", "-preset", "veryfast"] + tune + [
            "-crf", str(CRF), "-g", str(FPS * 10),
            "-c:a", "copy", "-shortest", out]
    run(cmd)


def make_visualizers(ff, combined, folder, stamp, cover, ext):
    vext = ".mp4" if ext in {".mp3", ".m4a", ".aac"} else ".mkv"
    total = duration(ff, combined)
    chunk = VIDEO_HOURS * 3600
    n = max(1, math.ceil(total / chunk)) if total else 1
    vids = []
    for k in range(n):
        start = k * chunk
        length = min(chunk, total - start) if total else None
        out = os.path.join(folder, "visualizer_%s_part%d%s" % (stamp, k + 1, vext))
        print("Rendering video %d/%d ..." % (k + 1, n))
        make_video(ff, combined, start, length, cover, out)
        vids.append(out)
    print("Video: %d file(s), %.1f MB" % (len(vids), mb(vids)))
    for v in vids:
        print("Saved:", v)


# ---------------------------------------------------------------- Gemini
def get_key():
    key = GEMINI_API_KEY.strip() or os.environ.get("GEMINI_API_KEY", "") \
        or os.environ.get("GOOGLE_API_KEY", "")
    if not key:
        kf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gemini_key.txt")
        if os.path.isfile(kf):
            key = open(kf, encoding="utf-8").read().strip()
    return key.strip()


class ApiError(Exception):
    def __init__(self, code, body):
        Exception.__init__(self, "HTTP %s: %s" % (code, body[:400]))
        self.code = code


def call(key, url, data=None, headers=None, method=None, want_headers=False):
    h = {"x-goog-api-key": key}
    h.update(headers or {})
    for attempt in range(4):
        try:
            if hasattr(data, "seek"):
                data.seek(0)
            req = urllib.request.Request(url, data=data, headers=h, method=method)
            with urllib.request.urlopen(req, timeout=900) as r:
                body = r.read()
                return (r.headers, body) if want_headers else body
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(15 * (attempt + 1))
                continue
            raise ApiError(e.code, body)
        except (urllib.error.URLError, OSError) as e:
            if attempt < 3:
                time.sleep(10 * (attempt + 1))
                continue
            raise ApiError("network", str(e))


def upload(key, path, mime):
    size = os.path.getsize(path)
    meta = json.dumps({"file": {"display_name": os.path.basename(path)}}).encode()
    hdrs, _ = call(key, API_BASE + "/upload/v1beta/files", data=meta, method="POST",
                   want_headers=True,
                   headers={"X-Goog-Upload-Protocol": "resumable",
                            "X-Goog-Upload-Command": "start",
                            "X-Goog-Upload-Header-Content-Length": str(size),
                            "X-Goog-Upload-Header-Content-Type": mime,
                            "Content-Type": "application/json"})
    up_url = hdrs["X-Goog-Upload-URL"]
    with open(path, "rb") as f:
        body = call(key, up_url, data=f, method="POST",
                    headers={"Content-Length": str(size),
                             "Content-Type": "application/octet-stream",
                             "X-Goog-Upload-Offset": "0",
                             "X-Goog-Upload-Command": "upload, finalize"})
    info = json.loads(body)["file"]
    for _ in range(120):   # wait until processed
        if info.get("state", "ACTIVE") == "ACTIVE":
            return info
        if info.get("state") == "FAILED":
            raise ApiError("file", "Gemini could not process the upload")
        time.sleep(5)
        info = json.loads(call(key, API_BASE + "/v1beta/" + info["name"]))
    raise ApiError("file", "upload stayed in PROCESSING")


def generate(key, model, info, mime):
    body = json.dumps({
        "contents": [{"parts": [
            {"file_data": {"mime_type": mime, "file_uri": info["uri"]}},
            {"text": TRANSCRIBE_PROMPT}]}],
        "generationConfig": {"temperature": 0}}).encode()
    out = json.loads(call(key, "%s/v1beta/models/%s:generateContent" % (API_BASE, model),
                          data=body, method="POST",
                          headers={"Content-Type": "application/json"}))
    cands = out.get("candidates") or []
    parts = (cands[0].get("content", {}).get("parts", []) if cands else [])
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise ApiError("empty", "empty answer (finishReason: %s)" %
                       (cands[0].get("finishReason") if cands else "no candidates"))
    return text


def transcribe_one(ff, key, part):
    ext = os.path.splitext(part)[1].lower()
    tmp = None
    src, mime = part, MIME.get(ext)
    # Gemini downsamples to 16 kbps mono anyway: upload a small lossless mono copy
    # of big/unsupported parts. The saved part itself is never touched.
    if not mime or os.path.getsize(part) > 40 * 1048576:
        tmp = os.path.join(tempfile.gettempdir(), "gm_%d.flac" % os.getpid())
        run([ff, "-y", "-loglevel", "error", "-i", part, "-map", "0:a",
             "-ac", "1", "-ar", "16000", "-c:a", "flac", tmp])
        src, mime = tmp, "audio/flac"
    info = None
    try:
        info = upload(key, src, mime)
        last = None
        for model in GEMINI_MODELS:
            try:
                return generate(key, model, info, mime)
            except ApiError as e:
                last = e
                if e.code not in (404, "empty"):
                    raise
        raise last
    finally:
        if info:
            try:
                call(key, API_BASE + "/v1beta/" + info["name"], method="DELETE")
            except Exception:
                pass
        if tmp and os.path.exists(tmp):
            os.remove(tmp)


def hms(sec):
    return "%02d:%02d:%02d" % (sec // 3600, sec % 3600 // 60, sec % 60)


def transcribe_parts(ff, key, parts, folder, stamp):
    texts = []
    for i, part in enumerate(parts):
        txt = os.path.splitext(part)[0] + ".txt"
        label = os.path.basename(part)
        if os.path.isfile(txt):
            text = open(txt, encoding="utf-8").read()
        else:
            print("Transcribing %s (%d/%d) ..." % (label, i + 1, len(parts)))
            try:
                text = transcribe_one(ff, key, part)
                with open(txt, "w", encoding="utf-8") as f:
                    f.write(text)
            except ApiError as e:
                print("  failed:", e)
                text = "[transcription failed]"
        texts.append("=== %s (starts at %s) ===\n%s\n" % (label, hms(i * PART_SEC), text))
    out = os.path.join(folder, "transcript_%s.txt" % stamp)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(texts))
    print("Transcript saved:", out)


# ---------------------------------------------------------------- main
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

    cover = resolve_cover() if VISUALIZER else None
    key = get_key() if TRANSCRIBE else ""
    if TRANSCRIBE and not key:
        print("No Gemini API key -> transcription skipped "
              "(set GEMINI_API_KEY at the top of the script).")

    infos = [wav_info(f) for f in files] if ext == ".wav" else []
    print("Order (oldest -> newest):")
    for i, f in enumerate(files, 1):
        print(" %d. %s%s" % (i, os.path.basename(f),
                             "  [%s]" % describe(infos[i - 1]) if infos else ""))

    decode = ext == ".wav" and needs_decode(infos)
    if decode:
        print("Compressed WAV found -> decoding combined + parts to PCM 16-bit.")
    if ext == ".wav" and len({(i[1], i[2]) for i in infos if i}) > 1:
        print("WARNING: files differ in sample rate / channels, result may be wrong.")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    folder = os.path.dirname(os.path.abspath(files[0]))
    combined = os.path.join(folder, "combined_" + stamp + ext)
    merge(ff, files, combined, decode)
    print("Combined: %.1f MB (input %.1f MB)" % (mb([combined]), mb(files)))
    if ext == ".wav":
        print("Combined format: " + describe(wav_info(combined)))
    print("Saved:", combined)

    parts_dir = os.path.join(folder, "parts_" + stamp)
    os.makedirs(parts_dir)
    split(ff, combined, parts_dir, ext)
    parts = sorted(os.path.join(parts_dir, f) for f in os.listdir(parts_dir))
    if not parts:
        sys.exit("No parts were created — the combined file may be corrupt.")
    print("Parts: %d, %.1f MB -> %s" % (len(parts), mb(parts), parts_dir))

    if key:
        transcribe_parts(ff, key, parts, folder, stamp)
    if VISUALIZER:
        make_visualizers(ff, combined, folder, stamp, cover, ext)
    print("Done.")


if __name__ == "__main__":
    main()
