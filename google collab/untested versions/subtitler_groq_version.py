import os
import sys
import json
import time
import urllib.request
from datetime import datetime
from pydub import AudioSegment
from pydub.silence import detect_silence
from groq import Groq

# --- CONFIG ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KEYS_FILE = os.path.join(SCRIPT_DIR, "turbo_transcribe_keys.json")
CRASH_LOG = os.path.join(SCRIPT_DIR, "crash.log")
MODEL = "whisper-large-v3-turbo"

TARGET_CHUNK_MS = 15 * 60 * 1000     # aim for ~15 min chunks
SEARCH_WINDOW_MS = 30 * 1000         # look +/- 30s around target for a quiet spot to cut
MIN_SILENCE_LEN = 400                # ms of quiet to count as a "gap"
SILENCE_THRESH_OFFSET = -16          # dB below the chunk's average loudness
MAX_CHUNK_BYTES = 24 * 1024 * 1024   # hard safety ceiling, 1MB headroom under Groq's 25MB limit
RATE_LIMIT_COOLDOWN_SEC = 3          # pause before retrying after a 429, so we don't hammer the API

# --- DOUBLE-CHECK / CONFIDENCE THRESHOLDS ---
# Same defaults Whisper itself uses internally for its own temperature fallback.
LOGPROB_THRESHOLD = -1.0             # avg_logprob below this = shaky
NO_SPEECH_THRESHOLD = 0.6            # no_speech_prob above this = probably silence/noise
COMPRESSION_RATIO_THRESHOLD = 2.4    # compression_ratio above this = repetition/hallucination loop
LANG_MISMATCH_CONFIDENCE = 0.6       # fastText confidence needed before we trust a language-mismatch flag
RETRY_TEMPERATURES = (0.2, 0.4, 0.8) # escalating re-tries for chunks with flagged segments

LID_MODEL_PATH = os.path.join(SCRIPT_DIR, "lid.176.bin")
LID_MODEL_URL = "https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin"
_lid_model = None
_lid_unavailable_warned = False


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


# --- CHUNKING ---
def find_cut_point(audio: AudioSegment, target_ms: int) -> int:
    """Find a silent gap near target_ms so we don't cut mid-word/mid-sentence."""
    window_start = max(0, target_ms - SEARCH_WINDOW_MS)
    window_end = min(len(audio), target_ms + SEARCH_WINDOW_MS)
    window = audio[window_start:window_end]

    thresh = window.dBFS + SILENCE_THRESH_OFFSET
    silences = detect_silence(window, min_silence_len=MIN_SILENCE_LEN, silence_thresh=thresh)

    if not silences:
        return target_ms

    best = min(silences, key=lambda s: abs((window_start + (s[0] + s[1]) // 2) - target_ms))
    return window_start + (best[0] + best[1]) // 2


def build_chunks(audio: AudioSegment) -> list:
    chunks = []
    pos = 0
    while pos < len(audio):
        target_end = min(pos + TARGET_CHUNK_MS, len(audio))
        if target_end < len(audio):
            target_end = find_cut_point(audio, target_end)
        chunks.append((pos, target_end))
        pos = target_end
    return chunks


def enforce_size_limit(audio: AudioSegment, start_ms: int, end_ms: int) -> list:
    """Export a chunk to check its real FLAC size; if over the cap, split at a
    silence gap and check each half recursively."""
    test_path = os.path.join(SCRIPT_DIR, "temp_size_check.flac")
    audio[start_ms:end_ms].export(test_path, format="flac")
    size = os.path.getsize(test_path)
    os.remove(test_path)

    if size <= MAX_CHUNK_BYTES or (end_ms - start_ms) < 60 * 1000:
        return [(start_ms, end_ms)]

    midpoint = start_ms + (end_ms - start_ms) // 2
    cut = find_cut_point(audio, midpoint)
    if cut <= start_ms or cut >= end_ms:
        cut = midpoint

    return enforce_size_limit(audio, start_ms, cut) + enforce_size_limit(audio, cut, end_ms)


# --- TIMESTAMP HELPERS (for manual resume-point override + crash.log) ---
def format_hms(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def parse_hms(raw: str):
    """Parses 'H:M:S', 'M:S', or plain seconds. Returns seconds (float) or None if invalid."""
    raw = raw.strip()
    if not raw:
        return None
    parts = raw.split(":")
    try:
        parts = [float(p) for p in parts]
    except ValueError:
        return None
    if len(parts) == 1:
        h, m, s = 0.0, 0.0, parts[0]
    elif len(parts) == 2:
        h, m, s = 0.0, parts[0], parts[1]
    elif len(parts) == 3:
        h, m, s = parts
    else:
        return None
    if m >= 60 or s >= 60 or h < 0 or m < 0 or s < 0:
        return None
    return h * 3600 + m * 60 + s


def log_crash(fname: str, position_sec: float, reason: str):
    """Appends a line to crash.log - the other script (Colab or Groq) reads this
    the same way you do: as a plain HH:MM:SS to paste in as a manual resume point."""
    line = (f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] '{fname}' stopped at "
            f"{format_hms(position_sec)} ({position_sec:.2f}s) - {reason}\n")
    with open(CRASH_LOG, "a", encoding="utf-8") as f:
        f.write(line)
    print(f"[+] Logged to {os.path.basename(CRASH_LOG)}: stopped at {format_hms(position_sec)}")


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


def save_progress(file_path: str, chunks: list, texts: list, running_context: str, base_offset_sec: float = 0.0):
    path = progress_path(file_path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "chunks": chunks, "texts": texts, "running_context": running_context,
            "base_offset_sec": base_offset_sec,
        }, f, indent=2)


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


# --- DOUBLE-CHECKER: confidence + language-mismatch flagging ---
def _seg_get(seg, key, default=None):
    """Groq's verbose_json segments may come back as dicts or attr-style objects
    depending on SDK version - handle both."""
    if isinstance(seg, dict):
        return seg.get(key, default)
    return getattr(seg, key, default)


def _get_lid_model():
    """Lazy-load the fastText language-ID model, downloading it once if needed.
    Returns None (and prints a one-time warning) if fasttext isn't installed -
    the pipeline still works, it just skips the language-mismatch check."""
    global _lid_model, _lid_unavailable_warned
    if _lid_model is not None:
        return _lid_model
    try:
        import fasttext
    except ImportError:
        if not _lid_unavailable_warned:
            print("[!] fasttext not installed - language-mismatch double-checking is disabled.")
            print("    Run: pip install fasttext-wheel   (then restart the script)")
            _lid_unavailable_warned = True
        return None

    if not os.path.exists(LID_MODEL_PATH):
        print("[+] Downloading fastText language-ID model (~126MB, one-time)...")
        urllib.request.urlretrieve(LID_MODEL_URL, LID_MODEL_PATH)

    fasttext.FastText.eprint = lambda *a, **k: None  # silence a harmless load warning
    _lid_model = fasttext.load_model(LID_MODEL_PATH)
    return _lid_model


def detect_text_language(text: str):
    """Returns (lang_code, confidence) via fastText, or (None, 0.0) if unavailable."""
    model = _get_lid_model()
    if model is None or not text.strip():
        return None, 0.0
    clean = " ".join(text.strip().split())  # fastText chokes on embedded newlines
    labels, probs = model.predict(clean, k=1)
    lang = labels[0].replace("__label__", "")
    return lang, float(probs[0])


def flag_segments(segments: list, expected_lang: str) -> list:
    """Runs every segment through the confidence + language checks and returns
    a list of flag dicts for anything that looks unreliable."""
    flags = []
    for seg in segments:
        avg_logprob = _seg_get(seg, "avg_logprob", 0.0) or 0.0
        no_speech = _seg_get(seg, "no_speech_prob", 0.0) or 0.0
        comp_ratio = _seg_get(seg, "compression_ratio", 1.0) or 1.0
        text = _seg_get(seg, "text", "") or ""

        reasons = []
        if avg_logprob < LOGPROB_THRESHOLD:
            reasons.append(f"low avg_logprob ({avg_logprob:.2f})")
        if no_speech > NO_SPEECH_THRESHOLD:
            reasons.append(f"high no_speech_prob ({no_speech:.2f})")
        if comp_ratio > COMPRESSION_RATIO_THRESHOLD:
            reasons.append(f"high compression_ratio ({comp_ratio:.2f}, possible repetition loop)")

        seg_lang, seg_conf = detect_text_language(text)
        if seg_lang and expected_lang and seg_lang != expected_lang and seg_conf > LANG_MISMATCH_CONFIDENCE:
            reasons.append(
                f"language mismatch (chunk detected as '{expected_lang}', "
                f"this segment reads like '{seg_lang}' at {seg_conf:.2f} confidence)"
            )

        if reasons:
            flags.append({
                "start": _seg_get(seg, "start", None),
                "end": _seg_get(seg, "end", None),
                "text": text,
                "reasons": reasons,
            })
    return flags


def flags_path(file_path: str) -> str:
    base = os.path.splitext(os.path.basename(file_path))[0]
    return os.path.join(SCRIPT_DIR, f"{base}.flags.json")


def save_flags(file_path: str, flags_by_chunk: dict):
    if not flags_by_chunk:
        return
    with open(flags_path(file_path), "w", encoding="utf-8") as f:
        json.dump(flags_by_chunk, f, indent=2)


def _to_seg_list(segments: list) -> list:
    """Converts raw response segments (dict or attr-style) into plain, JSON-safe
    dicts with just what we need to reconstruct timestamps later."""
    return [
        {
            "start": _seg_get(s, "start", 0.0) or 0.0,
            "end": _seg_get(s, "end", 0.0) or 0.0,
            "text": (_seg_get(s, "text", "") or "").strip(),
        }
        for s in segments
    ]


def call_groq(client, chunk_name: str, prompt, temperature: float = 0.0):
    """One API call, requesting verbose_json so we get per-segment confidence
    data back instead of a bare string."""
    with open(chunk_name, "rb") as audio_file:
        response = client.audio.transcriptions.create(
            file=(os.path.basename(chunk_name), audio_file.read()),
            model=MODEL,
            response_format="verbose_json",
            temperature=temperature,
            prompt=prompt,
        )
    text = _seg_get(response, "text", "") or ""
    segments = _seg_get(response, "segments", []) or []
    lang = _seg_get(response, "language", None)
    return text.strip(), segments, lang


# --- TRANSCRIPTION ---
def transcribe_file(api_keys: list, file_path: str):
    if not os.path.exists(file_path):
        print(f"[!] Error: Could not find the file '{file_path}'")
        return

    key_index = 0
    client = Groq(api_key=api_keys[key_index])

    print(f"\n[+] Loading '{os.path.basename(file_path)}'...")
    fname = os.path.basename(file_path)
    audio_full = AudioSegment.from_file(file_path)
    audio_full = audio_full.set_frame_rate(16000).set_channels(1)
    total_sec = len(audio_full) / 1000
    print(f"[+] Audio length: {total_sec/60:.2f} minutes ({format_hms(total_sec)})")

    resumed = load_progress(file_path)
    known_offset = 0.0
    if resumed:
        chunks_prev = [tuple(c) for c in resumed["chunks"]]
        texts_prev = resumed["texts"]
        done_prev = [i for i, t in enumerate(texts_prev) if t is not None]
        base_prev = resumed.get("base_offset_sec", 0.0)
        if done_prev:
            known_offset = base_prev + chunks_prev[max(done_prev)][1] / 1000.0
        else:
            known_offset = base_prev
        print(f"[+] Found saved progress for '{fname}': {len(done_prev)}/{len(chunks_prev)} "
              f"chunk(s) done (up to {format_hms(known_offset)}).")

    raw = input(
        f"[?] Resume '{fname}' at {format_hms(known_offset)}? Press Enter to accept, or type a "
        f"different HH:MM:SS to jump there instead (e.g. wherever Colab left off) - "
        f"or Enter alone for 0:00:00 if this is a fresh file: "
    ).strip()

    base_offset = known_offset
    if raw:
        override_sec = parse_hms(raw)
        if override_sec is None:
            print("[!] Couldn't parse that timestamp - using the default instead.")
        else:
            base_offset = override_sec
            resumed = None  # manual override always starts a fresh chunk plan from here
            print(f"[+] Manual override: resuming '{fname}' from {format_hms(base_offset)}.")

    remaining_audio = audio_full[int(base_offset * 1000):] if base_offset > 0 else audio_full

    if resumed:
        chunks = [tuple(c) for c in resumed["chunks"]]
        texts = resumed["texts"]  # list, same length as chunks; None = not yet done
        running_context = resumed["running_context"]
        base_offset = resumed.get("base_offset_sec", base_offset)
        done_count = sum(1 for t in texts if t is not None)
        print(f"[+] Resuming previous run: {done_count}/{len(chunks)} chunk(s) already done.")
    else:
        raw_chunks = build_chunks(remaining_audio)
        chunks = []
        for start_ms, end_ms in raw_chunks:
            chunks.extend(enforce_size_limit(remaining_audio, start_ms, end_ms))
        texts = [None] * len(chunks)
        running_context = ""
        print(f"[+] Slicing into {len(chunks)} size-verified chunk(s) starting at {format_hms(base_offset)}.")
        save_progress(file_path, chunks, texts, running_context, base_offset)

    all_flags = {}  # chunk index -> list of flag dicts, for the .flags.json sidecar

    for i, (start_ms, end_ms) in enumerate(chunks):
        if texts[i] is not None:
            continue  # already transcribed in a prior run

        chunk_name = os.path.join(SCRIPT_DIR, f"temp_turbo_chunk_{i}.flac")
        remaining_audio[start_ms:end_ms].export(chunk_name, format="flac")
        size_mb = os.path.getsize(chunk_name) / (1024 * 1024)

        while True:
            print(f"[>] Chunk {i+1}/{len(chunks)} ({size_mb:.1f} MB) -> Groq "
                  f"({MODEL}, key '{key_index+1}/{len(api_keys)}')...")
            try:
                prompt = running_context[-800:] if running_context else None
                text, segments, chunk_lang = call_groq(client, chunk_name, prompt)
                flags = flag_segments(segments, chunk_lang)

                if flags:
                    print(f"[?] {len(flags)} segment(s) in chunk {i+1} look shaky - double-checking "
                          f"at higher temperatures...")
                    best_segments, best_flags = segments, flags
                    for temp in RETRY_TEMPERATURES:
                        retry_text, retry_segments, retry_lang = call_groq(
                            client, chunk_name, prompt, temperature=temp
                        )
                        retry_flags = flag_segments(retry_segments, retry_lang)
                        print(f"    retry @ temp={temp}: {len(retry_flags)} segment(s) still flagged")
                        if len(retry_flags) < len(best_flags):
                            best_segments, best_flags = retry_segments, retry_flags
                        if not retry_flags:
                            break
                    segments, flags = best_segments, best_flags
                    if flags:
                        print(f"[!] {len(flags)} segment(s) still flagged after retries - "
                              f"logged to {os.path.basename(flags_path(file_path))} for manual review.")
                        all_flags[i] = flags
                        save_flags(file_path, all_flags)

                seg_list = _to_seg_list(segments)
                texts[i] = seg_list  # list of {start,end,text} dicts, relative to this chunk's own audio
                chunk_text = " ".join(s["text"] for s in seg_list if s["text"]).strip()
                running_context = (running_context + " " + chunk_text).strip()
                save_progress(file_path, chunks, texts, running_context, base_offset)
                break  # chunk succeeded, move to next chunk

            except KeyboardInterrupt:
                done_count = sum(1 for t in texts if t is not None)
                last_done_end = base_offset
                for j in range(len(chunks)):
                    if texts[j] is None:
                        break
                    last_done_end = base_offset + chunks[j][1] / 1000.0
                if os.path.exists(chunk_name):
                    os.remove(chunk_name)
                log_crash(fname, last_done_end, f"manually interrupted ({done_count}/{len(chunks)} chunks done)")
                print(f"\n[STOPPED] Interrupted by user. Last completed point: {format_hms(last_done_end)}.")
                raise

            except Exception as e:
                if is_quota_or_rate_limit_error(e):
                    print(f"[!] Key '{key_index+1}' hit a rate limit/quota error: {e}")
                    print(f"[+] Waiting {RATE_LIMIT_COOLDOWN_SEC}s before retrying, to avoid a cycling error...")
                    time.sleep(RATE_LIMIT_COOLDOWN_SEC)

                    key_index += 1
                    if key_index >= len(api_keys):
                        if os.path.exists(chunk_name):
                            os.remove(chunk_name)
                        done_count = sum(1 for t in texts if t is not None)
                        # last contiguous completed chunk end = where it's actually safe to resume from
                        last_done_end = base_offset
                        for j in range(len(chunks)):
                            if texts[j] is None:
                                break
                            last_done_end = base_offset + chunks[j][1] / 1000.0
                        log_crash(fname, last_done_end,
                                  f"all {len(api_keys)} Groq API key(s) exhausted ({done_count}/{len(chunks)} chunks done)")
                        print(
                            "\n[STOPPED] All available API keys are exhausted for now.\n"
                            f"Progress is saved — {done_count}/{len(chunks)} chunks done, "
                            f"last completed point: {format_hms(last_done_end)}.\n"
                            "Rerun this script (it'll offer to resume there), or feed that timestamp "
                            "into the Colab script to pick up from there instead.\n"
                        )
                        return
                    print(f"[+] Switching to key {key_index+1}/{len(api_keys)}...")
                    client = Groq(api_key=api_keys[key_index])
                    continue  # retry same chunk with new key
                else:
                    print(f"[!] Error during chunk {i+1}: {e}")
                    texts[i] = []  # mark as done (empty) so we don't loop forever on a bad chunk
                    save_progress(file_path, chunks, texts, running_context, base_offset)
                    break

        if os.path.exists(chunk_name):
            os.remove(chunk_name)

    if all(t is not None for t in texts):
        lines = []
        for (chunk_start_ms, _chunk_end_ms), seg_list in zip(chunks, texts):
            for seg in seg_list:
                if not seg["text"]:
                    continue
                abs_start = base_offset + chunk_start_ms / 1000 + seg["start"]
                abs_end = base_offset + chunk_start_ms / 1000 + seg["end"]
                lines.append(f"[{abs_start:.2f}s -> {abs_end:.2f}s] {seg['text']}")
        final_text = "\n".join(lines)
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
 
