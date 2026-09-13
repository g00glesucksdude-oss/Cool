import os
import sys
import json
import time
import re
import subprocess
import tempfile
from groq import Groq

# --- CONFIG ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KEYS_FILE = os.path.join(SCRIPT_DIR, "turbo_transcribe_keys.json")
MODEL = "whisper-large-v3-turbo"

TARGET_CHUNK_SEC = 15 * 60          # aim for ~15 min chunks
SEARCH_WINDOW_SEC = 30              # look +/- 30s around target for a quiet spot to cut
MIN_SILENCE_LEN = 0.4               # seconds of quiet to count as a "gap"
SILENCE_THRESHOLD_DB = -35          # absolute dB threshold (ffmpeg style)
SILENCE_PAD_SEC = 0.15              # tiny pad so we don't clip speech at the cut
MAX_CHUNK_BYTES = 24 * 1024 * 1024  # hard safety ceiling, 1MB headroom under Groq's 25MB limit
RATE_LIMIT_COOLDOWN_SEC = 3         # pause before retrying after a 429


# --- API KEY MANAGEMENT ---
def load_keys() -> list:
    if not os.path.exists(KEYS_FILE):
        return []
    try:
        with open(KEYS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_keys(keys: list):
    with open(KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump(keys, f, indent=2)


def add_key_prompt(keys: list) -> list:
    label = input("Label for this key (e.g. 'personal', 'work'): ").strip() or f"key{len(keys) + 1}"
    key = input("Paste your Groq API key: ").strip()
    keys.append({"label": label, "key": key})
    save_keys(keys)
    print(f"[+] Saved '{label}'.")
    return keys


def select_api_keys() -> list:
    """Returns an ordered list of API key strings to rotate through when one gets rate-limited."""
    keys = load_keys()

    if not keys:
        print("[+] No saved Groq API keys found. Let's add one.")
        keys = add_key_prompt(keys)

    while True:
        print("\nSaved API keys:")
        for i, k in enumerate(keys, 1):
            print(f"  {i}. {k['label']}")
        print(f"  {len(keys) + 1}. Add a new key")

        raw = input(
            f"Select key(s) to use this session (e.g. '1' or '1,3' or 'all'), "
            f"or {len(keys) + 1} to add one: "
        ).strip().lower()

        if raw == "all":
            return [k["key"] for k in keys]

        if raw == str(len(keys) + 1):
            keys = add_key_prompt(keys)
            continue

        try:
            indices = [int(x.strip()) for x in raw.split(",") if x.strip()]
            selected = [keys[i - 1]["key"] for i in indices if 1 <= i <= len(keys)]
            if selected:
                return selected
        except ValueError:
            pass

        print("[!] Couldn't parse that. Try again.")


# --- FILE SELECTION ---
def select_audio_files() -> list:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        paths = filedialog.askopenfilenames(
            title="Select audio file(s) to transcribe",
            filetypes=[
                ("Audio files", "*.mp3 *.wav *.m4a *.flac *.ogg *.mp4 *.webm"),
                ("All files", "*.*"),
            ],
        )
        root.destroy()
        if paths:
            return list(paths)
        print("[!] No files selected in dialog.")
    except Exception as e:
        print(f"[!] File picker unavailable ({e}). Falling back to manual entry.")

    raw = input("Enter audio file path(s), separated by commas: ").strip()
    return [p.strip().strip('"') for p in raw.split(",") if p.strip()]


# --- FFMPEG HELPERS (no full-file RAM load) ---
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
    """Return list of (silence_start, silence_end) in seconds. Never loads the whole file into Python."""
    cmd = [
        "ffmpeg", "-hide_banner", "-i", path,
        "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    text = result.stderr
    starts = [float(m) for m in re.findall(r"silence_start:\s*([\d.]+)", text)]
    ends   = [float(m) for m in re.findall(r"silence_end:\s*([\d.]+)", text)]
    return [(s, e) for s, e in zip(starts, ends) if e > s]


def find_best_cut(ideal_sec: float, silences: list, search_window: float = SEARCH_WINDOW_SEC) -> float:
    """Pick the silence midpoint closest to ideal_sec within Â±search_window. Fallback = ideal_sec."""
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


def extract_chunk_flac(src: str, start_sec: float, end_sec: float, dest: str):
    """Extract [start_sec, end_sec] to a mono 16 kHz FLAC via ffmpeg. Low memory."""
    length = max(0.01, end_sec - start_sec)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start_sec:.3f}",
        "-i", src,
        "-t", f"{length:.3f}",
        "-ar", "16000", "-ac", "1",
        "-c:a", "flac",
        dest,
    ]
    subprocess.run(cmd, check=True)


def estimate_flac_size(src: str, start_sec: float, end_sec: float) -> int:
    """Actually export a temp FLAC and return its byte size, then delete it."""
    fd, tmp = tempfile.mkstemp(suffix=".flac", dir=SCRIPT_DIR)
    os.close(fd)
    try:
        extract_chunk_flac(src, start_sec, end_sec, tmp)
        return os.path.getsize(tmp)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# --- CHUNKING (silence-aware, size-aware, no full RAM load) ---
def build_chunks(src: str, duration: float, silences: list) -> list:
    """Return list of (start_sec, end_sec) aiming for ~TARGET_CHUNK_SEC, cutting on silence."""
    chunks = []
    pos = 0.0
    while pos < duration - 1.0:
        ideal_end = min(pos + TARGET_CHUNK_SEC, duration)
        if ideal_end < duration - 5.0:
            end = find_best_cut(ideal_end, silences)
            # small pad so speech at the boundary isn't clipped
            end = min(duration, end + SILENCE_PAD_SEC)
        else:
            end = duration
        if end <= pos + 1.0:  # safety
            end = min(duration, pos + TARGET_CHUNK_SEC)
        chunks.append((pos, end))
        pos = end
    return chunks


def enforce_size_limit(src: str, start: float, end: float, silences: list) -> list:
    """If the FLAC would exceed MAX_CHUNK_BYTES, recursively split at a silence gap."""
    size = estimate_flac_size(src, start, end)
    if size <= MAX_CHUNK_BYTES or (end - start) < 60.0:
        return [(start, end)]

    midpoint = start + (end - start) / 2.0
    cut = find_best_cut(midpoint, silences, search_window=SEARCH_WINDOW_SEC)
    if cut <= start + 5.0 or cut >= end - 5.0:
        cut = midpoint

    return enforce_size_limit(src, start, cut, silences) + enforce_size_limit(src, cut, end, silences)


# --- PROGRESS / RESUME ---
def progress_path(file_path: str) -> str:
    base = os.path.splitext(os.path.basename(file_path))[0]
    return os.path.join(SCRIPT_DIR, f"{base}.progress.json")


def load_progress(file_path: str):
    path = progress_path(file_path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_progress(file_path: str, chunks: list, texts: list, running_context: str):
    path = progress_path(file_path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"chunks": chunks, "texts": texts, "running_context": running_context}, f, indent=2)


def clear_progress(file_path: str):
    path = progress_path(file_path)
    if os.path.exists(path):
        os.remove(path)


# --- RATE LIMIT DETECTION ---
def is_quota_or_rate_limit_error(e: Exception) -> bool:
    status = getattr(e, "status_code", None)
    if status in (429, 401, 403):
        return True
    msg = str(e).lower()
    return any(term in msg for term in ["rate limit", "rate_limit", "quota", "too many requests"])


# --- TRANSCRIPTION ---
def transcribe_file(api_keys: list, file_path: str):
    if not os.path.exists(file_path):
        print(f"[!] Error: Could not find the file '{file_path}'")
        return

    key_index = 0
    client = Groq(api_key=api_keys[key_index])

    print(f"\n[+] Probing '{os.path.basename(file_path)}' (no full load)...")
    duration = get_duration(file_path)
    print(f"[+] Audio length: {duration / 60:.2f} minutes")

    resumed = load_progress(file_path)
    if resumed:
        chunks = [tuple(c) for c in resumed["chunks"]]
        texts = resumed["texts"]
        running_context = resumed["running_context"]
        done_count = sum(1 for t in texts if t is not None)
        print(f"[+] Resuming previous run: {done_count}/{len(chunks)} chunk(s) already done.")
    else:
        print("[+] Detecting silence gaps for clean cuts...")
        silences = detect_silences(file_path)
        print(f"[+] Found {len(silences)} silence gap(s)")

        raw_chunks = build_chunks(file_path, duration, silences)
        chunks = []
        for start, end in raw_chunks:
            chunks.extend(enforce_size_limit(file_path, start, end, silences))
        texts = [None] * len(chunks)
        running_context = ""
        print(f"[+] Slicing into {len(chunks)} size-verified chunk(s).")
        save_progress(file_path, chunks, texts, running_context)

    for i, (start_sec, end_sec) in enumerate(chunks):
        if texts[i] is not None:
            continue

        chunk_name = os.path.join(SCRIPT_DIR, f"temp_turbo_chunk_{i}.flac")
        extract_chunk_flac(file_path, start_sec, end_sec, chunk_name)
        size_mb = os.path.getsize(chunk_name) / (1024 * 1024)

        while True:
            print(f"[>] Chunk {i+1}/{len(chunks)} ({size_mb:.1f} MB, "
                  f"{start_sec/60:.1f}-{end_sec/60:.1f} min) -> Groq "
                  f"({MODEL}, key '{key_index+1}/{len(api_keys)}')...")
            try:
                with open(chunk_name, "rb") as audio_file:
                    response = client.audio.transcriptions.create(
                        file=(os.path.basename(chunk_name), audio_file.read()),
                        model=MODEL,
                        response_format="text",
                        prompt=running_context[-800:] if running_context else None,
                    )
                text = response.strip()
                texts[i] = text
                running_context = (running_context + " " + text).strip()
                save_progress(file_path, chunks, texts, running_context)
                break

            except Exception as e:
                if is_quota_or_rate_limit_error(e):
                    print(f"[!] Key '{key_index+1}' hit a rate limit/quota error: {e}")
                    print(f"[+] Waiting {RATE_LIMIT_COOLDOWN_SEC}s before retrying...")
                    time.sleep(RATE_LIMIT_COOLDOWN_SEC)

                    key_index += 1
                    if key_index >= len(api_keys):
                        if os.path.exists(chunk_name):
                            os.remove(chunk_name)
                        print(
                            "\n[STOPPED] All available API keys are exhausted for now.\n"
                            f"Progress is saved â€” {sum(1 for t in texts if t is not None)}/{len(chunks)} "
                            "chunks done.\n"
                            "Just rerun the script later (or add another key) and it will "
                            "pick up exactly where it left off.\n"
                        )
                        return
                    print(f"[+] Switching to key {key_index+1}/{len(api_keys)}...")
                    client = Groq(api_key=api_keys[key_index])
                    continue
                else:
                    print(f"[!] Error during chunk {i+1}: {e}")
                    texts[i] = ""
                    save_progress(file_path, chunks, texts, running_context)
                    break

        if os.path.exists(chunk_name):
            os.remove(chunk_name)

    if all(t is not None for t in texts):
        final_text = "\n\n".join(t for t in texts if t)
        output_filename = os.path.splitext(os.path.basename(file_path))[0] + "_transcript.txt"
        with open(output_filename, "w", encoding="utf-8") as f:
            f.write(final_text)
        clear_progress(file_path)
        print(f"[SUCCESS] Saved: {os.path.abspath(output_filename)}")


def main():
    api_keys = select_api_keys()
    files = select_audio_files()
    if not files:
        print("[!] No audio files selected. Exiting.")
        sys.exit(0)

    print(f"\n[+] {len(files)} file(s) queued, {len(api_keys)} key(s) available to rotate through.")
    for f in files:
        transcribe_file(api_keys, f)

    print("\n[+] All done.")


if __name__ == "__main__":
    main() 
