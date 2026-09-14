"""
plain_transcribe.py
===================
Unified full-file transcription (no speaker diarization).

One script, two backends, works on Colab and on a laptop:

  - Groq API  (key rotation, rate-limit aware)
  - Local faster-whisper  (GPU/T4-aware quantization, CPU fallback)

Shared features pulled from your two previous scripts + the diarizer:
  - Silence-aware chunking (prefer cuts in quiet gaps)
  - Size-aware splits for Groq's 25 MB limit
  - One shared progress format so you can start on Colab and resume on
    the laptop (or the reverse) without losing work
  - Automatic cleanup of incomplete chunks when you switch backends
  - Timestamped output on both backends (consistent format)
  - Cross-chunk context continuity
  - Aggressive memory free between chunks on the local path
  - Download helpers (direct / Mediafire / Google Drive / ZIP)
  - Drive mount + save path when running in Colab

USAGE
-----
  python plain_transcribe.py

Then pick backend, model style, and how to supply audio.
Re-running the same file automatically resumes from the last finished chunk.
Switching backends mid-job drops only incomplete chunks from the old backend
so the new one redoes them cleanly.
"""

# --- 1. AUTO-INSTALL ---
import os
import sys
import subprocess

_ON_COLAB = os.path.exists("/content")
_RESTART_MARKER = "/content/.plain_transcribe_deps_ready"
_fresh_install = _ON_COLAB and not os.path.exists(_RESTART_MARKER)

print("[+] Checking and installing dependencies...")
reqs = ["faster-whisper", "groq", "pydub"]
subprocess.check_call([sys.executable, "-m", "pip", "install", "-q"] + reqs)

if _fresh_install:
    open(_RESTART_MARKER, "w").close()
    print("\n[!] Fresh install - Colab runtime needs a one-time restart so numpy/torch load cleanly.")
    print("[+] Restarting automatically. When it reconnects (~10-20s), just run this cell again.")
    os.kill(os.getpid(), 9)

# --- 2. IMPORTS ---
import re
import gc
import json
import time
import shutil
import zipfile
import tempfile
import http.cookiejar
import urllib.request
from urllib.parse import urlsplit, urlunsplit, quote, urlparse

IN_COLAB = "google.colab" in sys.modules or os.path.exists("/content")

if IN_COLAB:
    from google.colab import drive
    drive_mount_path = "/content/drive"
    if not os.path.exists(drive_mount_path):
        drive.mount(drive_mount_path)
    DRIVE_SAVE_DIR = "/content/drive/MyDrive/Transcripts"
    LOCAL_WORK_DIR = "/content/tmp_plain_transcribe"
else:
    DRIVE_SAVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Transcripts")
    LOCAL_WORK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp_plain_transcribe")

os.makedirs(DRIVE_SAVE_DIR, exist_ok=True)
os.makedirs(LOCAL_WORK_DIR, exist_ok=True)

TRANSCRIPTS_DIR = os.path.join(DRIVE_SAVE_DIR, "transcripts")
os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)

PROGRESS_DIR = os.path.join(DRIVE_SAVE_DIR, "plain_progress")
os.makedirs(PROGRESS_DIR, exist_ok=True)

GROQ_KEYS_FILE = os.path.join(DRIVE_SAVE_DIR, "groq_keys.json")

DOWNLOAD_DIR = os.path.join(LOCAL_WORK_DIR, "downloads")
CHUNK_DIR = os.path.join(LOCAL_WORK_DIR, "chunks")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(CHUNK_DIR, exist_ok=True)

# --- TUNABLES ---
TARGET_CHUNK_SEC = 10 * 60          # aim ~10 min (good for both backends)
SEARCH_WINDOW_SEC = 45              # look ± this many seconds for a silence gap
MIN_SILENCE_LEN = 0.4
SILENCE_THRESHOLD_DB = -35
SILENCE_PAD_SEC = 0.15
MAX_CHUNK_BYTES = 24 * 1024 * 1024  # Groq 25 MB limit with headroom
RATE_LIMIT_COOLDOWN_SEC = 3
CONTEXT_CHARS = 800
AUDIO_EXTS = (".ogg", ".oga", ".mp3", ".wav", ".m4a", ".flac", ".mp4", ".webm")

GROQ_MODEL_IDS = {
    "turbo": "whisper-large-v3-turbo",
    "large": "whisper-large-v3",
}

LINE_RE = re.compile(r"^\[(\d+\.\d+)s\s*->\s*(\d+\.\d+)s\]\s?(.*)$")


# =========================================================================
# MEMORY
# =========================================================================
def free_memory(aggressive: bool = False):
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            if aggressive:
                torch.cuda.ipc_collect()
                torch.cuda.synchronize()
    except Exception:
        pass


# =========================================================================
# GROQ KEYS (same pattern as diarizer / original Groq script)
# =========================================================================
def load_groq_keys() -> list:
    if not os.path.exists(GROQ_KEYS_FILE):
        return []
    try:
        with open(GROQ_KEYS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_groq_keys(keys: list):
    with open(GROQ_KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump(keys, f, indent=2)


def add_groq_key_prompt(keys: list) -> list:
    label = input("Label for this key (e.g. 'personal', 'work'): ").strip() or f"key{len(keys) + 1}"
    key = input("Paste your Groq API key: ").strip()
    keys.append({"label": label, "key": key})
    save_groq_keys(keys)
    print(f"[+] Saved '{label}'.")
    return keys


def select_groq_keys() -> list:
    keys = load_groq_keys()
    if not keys:
        print("[+] No saved Groq API keys found. Let's add one.")
        keys = add_groq_key_prompt(keys)

    while True:
        print("\nSaved Groq API keys:")
        for i, k in enumerate(keys, 1):
            print(f"  {i}. {k['label']}")
        print(f"  {len(keys) + 1}. Add a new key")
        raw = input("Select key(s) (e.g. '1' or '1,3' or 'all'): ").strip().lower()

        if raw == "all":
            return [k["key"] for k in keys]
        if raw == str(len(keys) + 1):
            keys = add_groq_key_prompt(keys)
            continue
        try:
            indices = [int(x.strip()) for x in raw.split(",") if x.strip()]
            selected = [keys[i - 1]["key"] for i in indices if 1 <= i <= len(keys)]
            if selected:
                return selected
        except ValueError:
            pass
        print("[!] Couldn't parse that. Try again.")


def is_quota_or_rate_limit_error(e: Exception) -> bool:
    status = getattr(e, "status_code", None)
    if status in (429, 401, 403):
        return True
    msg = str(e).lower()
    return any(term in msg for term in ["rate limit", "rate_limit", "quota", "too many requests"])


# =========================================================================
# DOWNLOAD HELPERS
# =========================================================================
def safe_encode_url(url: str) -> str:
    parts = urlsplit(url)
    path = quote(parts.path, safe="/%")
    query = quote(parts.query, safe="=&%")
    return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def guess_filename(url: str, headers) -> str:
    cd = headers.get("Content-Disposition", "")
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";\n]+)"?', cd)
    if match:
        return match.group(1).strip()
    return os.path.basename(urlparse(url).path) or "downloaded_file"


def resolve_mediafire(url: str) -> str:
    print("[+] Mediafire link detected - resolving direct download URL...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp:
        html = resp.read().decode("utf-8", errors="ignore")
    patterns = [
        r'href="(https?://download[^"]+)"',
        r'id="downloadButton"[^>]*href="([^"]+)"',
        r'"downloadUrl"\s*:\s*"([^"]+)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            return safe_encode_url(match.group(1).replace("\\/", "/"))
    raise ValueError(
        "Could not find a direct download link on that Mediafire page. "
        "Try downloading manually and using a direct link instead."
    )


def extract_gdrive_id(url: str):
    m = re.search(r"/file/d/([^/]+)", url) or re.search(r"[?&]id=([^&]+)", url)
    return m.group(1) if m else None


def download_from_gdrive(file_id: str, dest: str) -> str:
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    base = "https://drive.google.com/uc?export=download"

    resp = opener.open(f"{base}&id={file_id}")
    content_type = resp.headers.get("Content-Type", "")

    if "text/html" in content_type:
        html = resp.read().decode("utf-8", errors="ignore")
        token_match = re.search(r'confirm=([0-9A-Za-z_-]+)', html)
        uuid_match = re.search(r'name="uuid" value="([^"]+)"', html)
        if uuid_match:
            confirm_match = re.search(r'name="confirm" value="([^"]+)"', html)
            confirm_val = confirm_match.group(1) if confirm_match else "t"
            resp = opener.open(
                f"https://drive.usercontent.google.com/download?id={file_id}"
                f"&export=download&confirm={confirm_val}&uuid={uuid_match.group(1)}"
            )
        elif token_match:
            resp = opener.open(f"{base}&id={file_id}&confirm={token_match.group(1)}")

    fname = guess_filename(f"{base}&id={file_id}", resp.headers)
    content_type = resp.headers.get("Content-Type", "")
    if "text/html" in content_type:
        raise ValueError(
            "Google Drive returned a webpage instead of the file. "
            "Make sure the file is shared as 'Anyone with the link'."
        )

    with open(dest, "wb") as f_out:
        f_out.write(resp.read())
    return fname


def download_link(url: str, dest: str) -> str:
    url = safe_encode_url(url.strip())

    if "drive.google.com" in url:
        file_id = extract_gdrive_id(url)
        if not file_id:
            raise ValueError("Could not find a file ID in that Google Drive link.")
        print("[+] Google Drive link detected - resolving direct download...")
        return download_from_gdrive(file_id, dest)

    if "mediafire.com" in url and "/file/" in url:
        url = resolve_mediafire(url)

    print(f"[+] Downloading from: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp:
        suggested_name = guess_filename(url, resp.headers)
        with open(dest, "wb") as f_out:
            f_out.write(resp.read())
    return suggested_name


def resolve_link_to_files(url: str) -> list:
    dest = os.path.join(DOWNLOAD_DIR, "downloaded_input")
    suggested_name = download_link(url, dest)
    if not os.path.exists(dest) or os.path.getsize(dest) == 0:
        raise ValueError("Download failed or produced an empty file.")
    print(f"[OK] Downloaded {os.path.getsize(dest) / 1_000_000:.1f} MB (as '{suggested_name}')")

    if zipfile.is_zipfile(dest):
        print("[+] Detected a ZIP - extracting audio files...")
        extract_dir = os.path.join(DOWNLOAD_DIR, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(dest, "r") as zf:
            zf.extractall(extract_dir)
        audio_files = sorted(
            os.path.join(root, f)
            for root, _, files in os.walk(extract_dir)
            for f in files
            if f.lower().endswith(AUDIO_EXTS)
        )
        if not audio_files:
            raise ValueError(f"No supported audio files found inside that ZIP.")
        return audio_files

    ext = os.path.splitext(suggested_name)[1].lower()
    if ext not in AUDIO_EXTS:
        raise ValueError(
            f"Downloaded file '{suggested_name}' is not a supported audio format "
            f"or ZIP ({', '.join(AUDIO_EXTS)})."
        )
    single_name = os.path.splitext(os.path.basename(suggested_name))[0] + ext
    single_path = os.path.join(DOWNLOAD_DIR, single_name)
    shutil.move(dest, single_path)
    return [single_path]


def select_audio_files() -> list:
    print("\nHow do you want to provide audio?")
    print("  1. Upload file(s)" + (" (Colab upload dialog)" if IN_COLAB else " (not available outside Colab)"))
    print("  2. Type/paste local file path(s)")
    print("  3. Paste a download link (direct / Mediafire / Google Drive / ZIP)")
    choice = input("Choose 1-3 (default 2): ").strip()

    if choice == "1" and IN_COLAB:
        from google.colab import files as colab_files
        print("[+] Upload audio file(s):")
        uploaded = colab_files.upload()
        if uploaded:
            return list(uploaded.keys())
        print("[!] No files uploaded.")
        return []

    if choice == "3":
        url = input("Paste the link: ").strip()
        try:
            return resolve_link_to_files(url)
        except Exception as e:
            print(f"[!] Download failed: {e}")
            return []

    # Default: local paths
    if not IN_COLAB:
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            paths = filedialog.askopenfilenames(
                title="Select audio file(s)",
                filetypes=[
                    ("Audio", "*.mp3 *.wav *.m4a *.flac *.ogg *.mp4 *.webm"),
                    ("All", "*.*"),
                ],
            )
            root.destroy()
            if paths:
                return list(paths)
        except Exception:
            pass

    raw = input("Enter audio file path(s) (comma-separated): ").strip()
    return [p.strip().strip('"') for p in raw.split(",") if p.strip()]


# =========================================================================
# FFMPEG HELPERS (never load full file into Python RAM)
# =========================================================================
def get_duration(path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    out = subprocess.check_output(cmd, text=True).strip()
    return float(out)


def detect_silences(path: str, noise_db: float = SILENCE_THRESHOLD_DB, min_dur: float = MIN_SILENCE_LEN):
    cmd = [
        "ffmpeg", "-hide_banner", "-i", path,
        "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    text = result.stderr
    starts = [float(m) for m in re.findall(r"silence_start:\s*([\d.]+)", text)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([\d.]+)", text)]
    return [(s, e) for s, e in zip(starts, ends) if e > s]


def find_best_cut(ideal_sec: float, silences: list, search_window: float = SEARCH_WINDOW_SEC) -> float:
    best = None
    best_dist = float("inf")
    lo = ideal_sec - search_window
    hi = ideal_sec + search_window
    for s_start, s_end in silences:
        if s_end < lo or s_start > hi:
            continue
        mid = (s_start + s_end) / 2.0
        dist = abs(mid - ideal_sec)
        if dist < best_dist:
            best_dist = dist
            best = mid
    return best if best is not None else ideal_sec


def extract_chunk(src: str, start_sec: float, end_sec: float, dest: str, fmt: str = "wav"):
    """Extract [start, end] to mono 16 kHz. fmt = 'wav' or 'flac'."""
    length = max(0.01, end_sec - start_sec)
    codec = "pcm_s16le" if fmt == "wav" else "flac"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start_sec:.3f}",
        "-i", src,
        "-t", f"{length:.3f}",
        "-ar", "16000", "-ac", "1",
        "-c:a", codec,
        dest,
    ]
    subprocess.run(cmd, check=True)


def estimate_flac_size(src: str, start_sec: float, end_sec: float) -> int:
    fd, tmp = tempfile.mkstemp(suffix=".flac", dir=CHUNK_DIR)
    os.close(fd)
    try:
        extract_chunk(src, start_sec, end_sec, tmp, fmt="flac")
        return os.path.getsize(tmp)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# =========================================================================
# CHUNKING
# =========================================================================
def build_chunks(src: str, duration: float, silences: list) -> list:
    """Return list of (start_sec, end_sec) aiming for TARGET_CHUNK_SEC, cutting on silence."""
    chunks = []
    pos = 0.0
    while pos < duration - 1.0:
        ideal_end = min(pos + TARGET_CHUNK_SEC, duration)
        if ideal_end < duration - 5.0:
            end = find_best_cut(ideal_end, silences)
            end = min(duration, end + SILENCE_PAD_SEC)
        else:
            end = duration
        if end <= pos + 1.0:
            end = min(duration, pos + TARGET_CHUNK_SEC)
        chunks.append((pos, end))
        pos = end
    return chunks


def enforce_size_limit(src: str, start: float, end: float, silences: list) -> list:
    """If the FLAC would exceed MAX_CHUNK_BYTES, split at a silence gap."""
    size = estimate_flac_size(src, start, end)
    if size <= MAX_CHUNK_BYTES or (end - start) < 60.0:
        return [(start, end)]

    midpoint = start + (end - start) / 2.0
    cut = find_best_cut(midpoint, silences, search_window=SEARCH_WINDOW_SEC)
    if cut <= start + 5.0 or cut >= end - 5.0:
        cut = midpoint
    return enforce_size_limit(src, start, cut, silences) + enforce_size_limit(src, cut, end, silences)


def plan_chunks(src: str, for_groq: bool) -> list:
    duration = get_duration(src)
    print(f"[+] Audio length: {duration / 60:.2f} minutes")
    print("[+] Detecting silence gaps for clean cuts...")
    silences = detect_silences(src)
    print(f"[+] Found {len(silences)} silence gap(s)")

    raw = build_chunks(src, duration, silences)
    if for_groq:
        chunks = []
        for start, end in raw:
            chunks.extend(enforce_size_limit(src, start, end, silences))
        print(f"[+] Planned {len(chunks)} size-verified chunk(s) for Groq.")
    else:
        chunks = raw
        print(f"[+] Planned {len(chunks)} chunk(s) for local Whisper.")
    return chunks


# =========================================================================
# PROGRESS / RESUME (shared format — works across backends and machines)
# =========================================================================
def progress_path_for(audio_path: str) -> str:
    """Stable key based on basename + size so the same file resumes on any machine
    as long as Drive (or the Transcripts folder) is shared."""
    base = os.path.splitext(os.path.basename(audio_path))[0]
    try:
        size = os.path.getsize(audio_path)
    except OSError:
        size = 0
    safe = re.sub(r"[^\w\-.]+", "_", base)[:80]
    return os.path.join(PROGRESS_DIR, f"{safe}_{size}.progress.json")


def load_progress(audio_path: str):
    path = progress_path_for(audio_path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_progress(audio_path: str, data: dict):
    path = progress_path_for(audio_path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def clear_progress(audio_path: str):
    path = progress_path_for(audio_path)
    if os.path.exists(path):
        os.remove(path)


def output_path_for(audio_path: str) -> str:
    base = os.path.splitext(os.path.basename(audio_path))[0]
    return os.path.join(TRANSCRIPTS_DIR, f"{base}.txt")


def rebuild_output_from_progress(out_path: str, texts: list) -> int:
    """Rewrite the transcript file from saved progress only.
    Drops any orphan lines that may have been written before a crash
    (write-to-txt succeeded, save_progress did not). Returns line count written."""
    lines_written = 0
    with open(out_path, "w", encoding="utf-8") as f_out:
        for entry in texts:
            if entry is None:
                continue
            # entry is list of [start, end, text] (current format)
            if isinstance(entry, list):
                for item in entry:
                    if isinstance(item, (list, tuple)) and len(item) >= 3:
                        st, en, tx = item[0], item[1], item[2]
                        f_out.write(f"[{float(st):.2f}s -> {float(en):.2f}s] {tx}\n")
                        lines_written += 1
                    elif isinstance(item, str) and item.strip():
                        # very old / odd shape
                        f_out.write(item.rstrip() + "\n")
                        lines_written += 1
            elif isinstance(entry, str) and entry.strip():
                f_out.write(entry.rstrip() + "\n")
                lines_written += 1
        f_out.flush()
        os.fsync(f_out.fileno())
    return lines_written


# =========================================================================
# BACKEND: LOCAL FASTER-WHISPER
# =========================================================================
_local_model = None
_local_model_style = None


def get_local_model(style: str):
    global _local_model, _local_model_style
    if _local_model is not None and _local_model_style == style:
        return _local_model

    # unload previous if style changed
    if _local_model is not None:
        unload_local_model()

    import torch
    from faster_whisper import WhisperModel

    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        print(f"[+] GPU detected ({gpu_name}).")
        is_t4 = "T4" in gpu_name
        if style == "turbo":
            if is_t4:
                print("[+] T4 - medium + int8_float16 for speed/memory...")
                model = WhisperModel("medium", device="cuda", compute_type="int8_float16")
            else:
                print("[+] Loading large-v3-turbo (int8_float16)...")
                model = WhisperModel("large-v3-turbo", device="cuda", compute_type="int8_float16")
        else:
            if is_t4:
                print("[+] T4 - large-v3 + int8_float16 (full accuracy)...")
                model = WhisperModel("large-v3", device="cuda", compute_type="int8_float16")
            else:
                print("[+] Loading large-v3 (float16)...")
                model = WhisperModel("large-v3", device="cuda", compute_type="float16")
    else:
        print("[!] CPU only - loading 'small' (int8).")
        model = WhisperModel("small", device="cpu", compute_type="int8")

    _local_model = model
    _local_model_style = style
    return model


def unload_local_model():
    global _local_model, _local_model_style
    if _local_model is not None:
        try:
            if hasattr(_local_model, "model") and hasattr(_local_model.model, "unload_model"):
                _local_model.model.unload_model()
        except Exception:
            pass
        _local_model = None
        _local_model_style = None
        free_memory(aggressive=True)


def transcribe_chunk_local(model, chunk_path: str, chunk_start: float, initial_prompt: str):
    """Returns list of (abs_start, abs_end, text) and detected language."""
    segments, info = model.transcribe(
        chunk_path,
        beam_size=1,
        language=None,
        vad_filter=True,
        condition_on_previous_text=False,
        initial_prompt=initial_prompt[-CONTEXT_CHARS:] if initial_prompt else None,
    )
    lines = []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        lines.append((
            round(seg.start + chunk_start, 3),
            round(seg.end + chunk_start, 3),
            text,
        ))
    return lines, getattr(info, "language", None)


# =========================================================================
# BACKEND: GROQ
# =========================================================================
def transcribe_chunk_groq(client_holder: dict, api_keys: list, chunk_path: str,
                          chunk_start: float, model_id: str, initial_prompt: str):
    """Returns list of (abs_start, abs_end, text) — Groq text mode gives one blob,
    so we attach the whole chunk time range. client_holder mutated for key rotation."""
    while True:
        try:
            with open(chunk_path, "rb") as audio_file:
                response = client_holder["client"].audio.transcriptions.create(
                    file=(os.path.basename(chunk_path), audio_file.read()),
                    model=model_id,
                    response_format="verbose_json",
                    prompt=initial_prompt[-CONTEXT_CHARS:] if initial_prompt else None,
                )
            # Prefer segment-level timestamps when verbose_json provides them
            lines = []
            segs = getattr(response, "segments", None) or []
            if segs:
                for s in segs:
                    text = (s.get("text") if isinstance(s, dict) else getattr(s, "text", "") or "").strip()
                    if not text:
                        continue
                    st = s.get("start") if isinstance(s, dict) else getattr(s, "start", 0.0)
                    en = s.get("end") if isinstance(s, dict) else getattr(s, "end", 0.0)
                    lines.append((
                        round(float(st) + chunk_start, 3),
                        round(float(en) + chunk_start, 3),
                        text,
                    ))
            else:
                text = (getattr(response, "text", None) or str(response) or "").strip()
                if text:
                    # Fall back: one block spanning the whole chunk
                    # We don't know exact end without duration; caller can pass it
                    lines.append((round(chunk_start, 3), round(chunk_start, 3), text))
            lang = getattr(response, "language", None)
            return lines, lang

        except Exception as e:
            if is_quota_or_rate_limit_error(e):
                print(f"[!] Key {client_holder['key_index']+1} rate/quota error: {e}")
                time.sleep(RATE_LIMIT_COOLDOWN_SEC)
                client_holder["key_index"] += 1
                if client_holder["key_index"] >= len(api_keys):
                    raise RuntimeError("All Groq API keys exhausted for now. Progress is saved — rerun later.")
                print(f"[+] Switching to key {client_holder['key_index']+1}/{len(api_keys)}...")
                from groq import Groq
                client_holder["client"] = Groq(api_key=api_keys[client_holder["key_index"]])
                continue
            raise


# =========================================================================
# BACKEND SWITCH CLEANUP
# =========================================================================
def cleanup_incomplete_for_backend_switch(progress: dict, new_backend: str) -> int:
    """If a chunk was started by a different backend and is only partial / failed,
    clear it so the new backend redoes it. Complete chunks from either backend
    are kept (both write the same timestamped line format into the final file)."""
    removed = 0
    chunks_meta = progress.get("chunks", [])
    texts = progress.get("texts", [])
    backends = progress.get("backends", [None] * len(chunks_meta))

    # Ensure backends list is same length
    while len(backends) < len(chunks_meta):
        backends.append(None)
    while len(texts) < len(chunks_meta):
        texts.append(None)

    for i, text in enumerate(texts):
        if text is None:
            continue
        prev_backend = backends[i]
        # Only wipe if it was produced by a *different* backend and looks empty/broken
        if prev_backend and prev_backend != new_backend:
            # text is stored as list of [start, end, text] or as a string in older progress
            if isinstance(text, list) and len(text) == 0:
                texts[i] = None
                backends[i] = None
                removed += 1
            elif isinstance(text, str) and not text.strip():
                texts[i] = None
                backends[i] = None
                removed += 1
            # otherwise keep it — already finished by the other backend

    progress["texts"] = texts
    progress["backends"] = backends
    return removed


# =========================================================================
# CORE: TRANSCRIBE ONE FILE
# =========================================================================
def transcribe_file(audio_path: str, backend: str, style: str,
                    api_keys: list = None, local_model=None):
    if not os.path.exists(audio_path):
        print(f"[!] File not found: {audio_path}")
        return

    out_path = output_path_for(audio_path)
    progress = load_progress(audio_path)

    if progress and progress.get("done"):
        print(f"\n[=] Skipping '{os.path.basename(audio_path)}' — already fully transcribed.")
        print(f"    Output: {out_path}")
        return

    for_groq = backend == "groq"

    if progress and progress.get("chunks"):
        chunks = [tuple(c) for c in progress["chunks"]]
        texts = progress.get("texts") or [None] * len(chunks)
        backends = progress.get("backends") or [None] * len(chunks)
        running_context = progress.get("running_context") or ""
        # Pad lists if needed
        while len(texts) < len(chunks):
            texts.append(None)
        while len(backends) < len(chunks):
            backends.append(None)

        n_cleaned = cleanup_incomplete_for_backend_switch(progress, backend)
        if n_cleaned:
            print(f"[+] Cleared {n_cleaned} incomplete chunk(s) from the other backend.")
            texts = progress["texts"]
            backends = progress["backends"]
            save_progress(audio_path, progress)

        # Always rebuild the .txt from progress so any orphan lines left by a
        # crash (wrote to txt, then died before save_progress) are discarded.
        n_lines = rebuild_output_from_progress(out_path, texts)
        done_count = sum(1 for t in texts if t is not None)
        print(f"\n[+] Resuming '{os.path.basename(audio_path)}': "
              f"{done_count}/{len(chunks)} chunk(s) already done "
              f"(rebuilt {n_lines} line(s) from progress).")
    else:
        chunks = plan_chunks(audio_path, for_groq=for_groq)
        texts = [None] * len(chunks)
        backends = [None] * len(chunks)
        running_context = ""
        # Fresh output file
        open(out_path, "w", encoding="utf-8").close()
        progress = {
            "source": os.path.abspath(audio_path),
            "chunks": [list(c) for c in chunks],
            "texts": texts,
            "backends": backends,
            "running_context": running_context,
            "done": False,
        }
        save_progress(audio_path, progress)
        print(f"\n[+] Transcribing '{os.path.basename(audio_path)}' "
              f"({len(chunks)} chunk(s), backend={backend}/{style})...")

    client_holder = None
    model_id = None
    if backend == "groq":
        from groq import Groq
        if not api_keys:
            api_keys = select_groq_keys()
        client_holder = {"client": Groq(api_key=api_keys[0]), "key_index": 0}
        model_id = GROQ_MODEL_IDS[style]
    else:
        if local_model is None:
            local_model = get_local_model(style)

    try:
        for i, (start_sec, end_sec) in enumerate(chunks):
            if texts[i] is not None:
                continue

            # Extract chunk
            ext = "flac" if backend == "groq" else "wav"
            chunk_name = os.path.join(CHUNK_DIR, f"chunk_{i:04d}.{ext}")
            try:
                extract_chunk(audio_path, start_sec, end_sec, chunk_name, fmt=ext)
                size_mb = os.path.getsize(chunk_name) / (1024 * 1024)
                print(f"[>] Chunk {i+1}/{len(chunks)} ({size_mb:.1f} MB, "
                      f"{start_sec/60:.1f}-{end_sec/60:.1f} min) → {backend}/{style}...")

                if backend == "groq":
                    lines, lang = transcribe_chunk_groq(
                        client_holder, api_keys, chunk_name, start_sec, model_id, running_context
                    )
                    # If Groq only gave a single block with identical start/end, stretch to chunk end
                    fixed = []
                    for st, en, tx in lines:
                        if en <= st:
                            en = end_sec
                        fixed.append((st, en, tx))
                    lines = fixed
                else:
                    lines, lang = transcribe_chunk_local(
                        local_model, chunk_name, start_sec, running_context
                    )

                # Append to final transcript immediately (crash-safe)
                with open(out_path, "a", encoding="utf-8") as f_out:
                    for st, en, tx in lines:
                        f_out.write(f"[{st:.2f}s -> {en:.2f}s] {tx}\n")
                        f_out.flush()
                        os.fsync(f_out.fileno())

                # Store serialisable form of lines
                texts[i] = [[st, en, tx] for st, en, tx in lines]
                backends[i] = backend
                chunk_text = " ".join(tx for _, _, tx in lines)
                running_context = (running_context + " " + chunk_text).strip()

                progress["texts"] = texts
                progress["backends"] = backends
                progress["running_context"] = running_context
                save_progress(audio_path, progress)

                if lang:
                    print(f"    lang={lang}  lines={len(lines)}")

            except Exception as e:
                print(f"[!] Failed on chunk {i+1}: {e}")
                print("    Progress saved — re-run to continue from here.")
                return
            finally:
                if os.path.exists(chunk_name):
                    try:
                        os.remove(chunk_name)
                    except OSError:
                        pass
                if backend == "local":
                    free_memory()

        # All chunks done
        progress["done"] = True
        save_progress(audio_path, progress)
        print(f"[SUCCESS] {os.path.basename(audio_path)} → {out_path}")

    finally:
        if backend == "local":
            # Keep model loaded across files in a batch; unload at end of main
            pass


# =========================================================================
# MAIN
# =========================================================================
def prompt_backend_and_style() -> tuple:
    print("\nWhich transcription backend?")
    print("  1. Groq API (needs API key(s), good on laptop / no GPU)")
    print("  2. Local faster-whisper (uses GPU if available, Colab-friendly)")
    raw = input("Choose 1 or 2 (default 1): ").strip()
    backend = "local" if raw == "2" else "groq"

    print("\nWhich model style?")
    print("  1. turbo    - faster, good language switching")
    print("  2. large    - slower, max accuracy")
    raw2 = input("Choose 1 or 2 (default 1): ").strip()
    style = "large" if raw2 == "2" else "turbo"
    return backend, style


def main():
    print("=" * 60)
    print("  plain_transcribe — full-file transcription (no speakers)")
    print("  Groq  ↔  local Whisper   |   Colab  ↔  laptop")
    print("=" * 60)

    backend, style = prompt_backend_and_style()
    files = select_audio_files()
    if not files:
        print("[!] No audio files selected. Exiting.")
        return

    api_keys = []
    if backend == "groq":
        api_keys = select_groq_keys()

    print(f"\n[+] {len(files)} file(s), backend={backend}/{style}")
    if backend == "groq":
        print(f"[+] {len(api_keys)} Groq key(s) available for rotation.")

    try:
        for f in files:
            if not os.path.exists(f):
                print(f"[!] Skipping missing file: {f}")
                continue
            transcribe_file(f, backend, style, api_keys=api_keys)
    finally:
        unload_local_model()
        free_memory(aggressive=True)

    print(f"\n[OK] Done. Transcripts in: {TRANSCRIPTS_DIR}")
    print(f"     Progress files in: {PROGRESS_DIR}")
    print("     Re-run the same file anytime to resume; switch backends freely.")


if __name__ == "__main__":
    main()
