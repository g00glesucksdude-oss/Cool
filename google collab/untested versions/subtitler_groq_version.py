import os
import sys
import json
import time
from pydub import AudioSegment
from pydub.silence import detect_silence
from groq import Groq

# --- CONFIG ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KEYS_FILE = os.path.join(SCRIPT_DIR, "turbo_transcribe_keys.json")
MODEL = "whisper-large-v3-turbo"

TARGET_CHUNK_MS = 15 * 60 * 1000     # aim for ~15 min chunks
SEARCH_WINDOW_MS = 30 * 1000         # look +/- 30s around target for a quiet spot to cut
MIN_SILENCE_LEN = 400                # ms of quiet to count as a "gap"
SILENCE_THRESH_OFFSET = -16          # dB below the chunk's average loudness
MAX_CHUNK_BYTES = 24 * 1024 * 1024   # hard safety ceiling, 1MB headroom under Groq's 25MB limit
RATE_LIMIT_COOLDOWN_SEC = 3          # pause before retrying after a 429, so we don't hammer the API
TRANSLATE_CHUNK_CHARS = 4500         # stay under free-tier translation API limits per request


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


# --- TRANSLATION ---
def ensure_translator_installed():
    try:
        from deep_translator import GoogleTranslator  # noqa: F401
        return True
    except ImportError:
        print("[+] Installing 'deep-translator' for the translation feature...")
        try:
            import subprocess
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "deep-translator"])
            return True
        except Exception as e:
            print(f"[!] Could not install deep-translator ({e}). Skipping translation.")
            return False


def prompt_translation_choice():
    """Ask once whether/what to translate transcripts into. Returns a language
    code (e.g. 'es') or None if translation is skipped."""
    raw = input(
        "\nTranslate finished transcripts too? Enter a target language code "
        "(e.g. 'es', 'fr', 'ja'), or press Enter to skip: "
    ).strip()
    if not raw:
        return None
    if not ensure_translator_installed():
        return None
    return raw.lower()


def chunk_text_for_translation(text: str, max_chars: int = TRANSLATE_CHUNK_CHARS) -> list:
    """Split on paragraph/sentence boundaries so we don't cut mid-sentence,
    while staying under the translation API's per-request size limit."""
    if len(text) <= max_chars:
        return [text]

    pieces = text.split("\n\n")
    chunks, current = [], ""
    for piece in pieces:
        candidate = (current + "\n\n" + piece) if current else piece
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current)
            if len(piece) <= max_chars:
                current = piece
            else:
                # Single paragraph itself too long; hard-split it.
                for i in range(0, len(piece), max_chars):
                    chunks.append(piece[i:i + max_chars])
                current = ""
    if current:
        chunks.append(current)
    return chunks


def translate_text(text: str, target_lang: str) -> str:
    from deep_translator import GoogleTranslator
    translator = GoogleTranslator(source="auto", target=target_lang)
    out_parts = []
    for chunk in chunk_text_for_translation(text):
        try:
            out_parts.append(translator.translate(chunk))
        except Exception as e:
            print(f"[!] Translation error on a chunk, leaving it untranslated: {e}")
            out_parts.append(chunk)
    return "\n\n".join(out_parts)


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
def transcribe_file(api_keys: list, file_path: str, translate_lang: str = None):
    if not os.path.exists(file_path):
        print(f"[!] Error: Could not find the file '{file_path}'")
        return

    key_index = 0
    client = Groq(api_key=api_keys[key_index])

    print(f"\n[+] Loading '{os.path.basename(file_path)}'...")
    audio = AudioSegment.from_file(file_path)
    audio = audio.set_frame_rate(16000).set_channels(1)
    print(f"[+] Audio length: {len(audio) / 1000 / 60:.2f} minutes")

    resumed = load_progress(file_path)
    if resumed:
        chunks = [tuple(c) for c in resumed["chunks"]]
        texts = resumed["texts"]  # list, same length as chunks; None = not yet done
        running_context = resumed["running_context"]
        done_count = sum(1 for t in texts if t is not None)
        print(f"[+] Resuming previous run: {done_count}/{len(chunks)} chunk(s) already done.")
    else:
        raw_chunks = build_chunks(audio)
        chunks = []
        for start_ms, end_ms in raw_chunks:
            chunks.extend(enforce_size_limit(audio, start_ms, end_ms))
        texts = [None] * len(chunks)
        running_context = ""
        print(f"[+] Slicing into {len(chunks)} size-verified chunk(s).")
        save_progress(file_path, chunks, texts, running_context)

    for i, (start_ms, end_ms) in enumerate(chunks):
        if texts[i] is not None:
            continue  # already transcribed in a prior run

        chunk_name = os.path.join(SCRIPT_DIR, f"temp_turbo_chunk_{i}.flac")
        audio[start_ms:end_ms].export(chunk_name, format="flac")
        size_mb = os.path.getsize(chunk_name) / (1024 * 1024)

        while True:
            print(f"[>] Chunk {i+1}/{len(chunks)} ({size_mb:.1f} MB) -> Groq "
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
                break  # chunk succeeded, move to next chunk

            except Exception as e:
                if is_quota_or_rate_limit_error(e):
                    print(f"[!] Key '{key_index+1}' hit a rate limit/quota error: {e}")
                    print(f"[+] Waiting {RATE_LIMIT_COOLDOWN_SEC}s before retrying, to avoid a cycling error...")
                    time.sleep(RATE_LIMIT_COOLDOWN_SEC)

                    key_index += 1
                    if key_index >= len(api_keys):
                        if os.path.exists(chunk_name):
                            os.remove(chunk_name)
                        print(
                            "\n[STOPPED] All available API keys are exhausted for now.\n"
                            f"Progress is saved — {sum(1 for t in texts if t is not None)}/{len(chunks)} "
                            "chunks done.\n"
                            "Just rerun the script later (or add another key) and it will "
                            "pick up exactly where it left off.\n"
                        )
                        return
                    print(f"[+] Switching to key {key_index+1}/{len(api_keys)}...")
                    client = Groq(api_key=api_keys[key_index])
                    continue  # retry same chunk with new key
                else:
                    print(f"[!] Error during chunk {i+1}: {e}")
                    texts[i] = ""  # mark as done (empty) so we don't loop forever on a bad chunk
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

        if translate_lang:
            print(f"[+] Translating transcript to '{translate_lang}'...")
            translated_text = translate_text(final_text, translate_lang)
            translated_filename = (
                os.path.splitext(os.path.basename(file_path))[0]
                + f"_transcript_{translate_lang}.txt"
            )
            with open(translated_filename, "w", encoding="utf-8") as f:
                f.write(translated_text)
            print(f"[SUCCESS] Saved translation: {os.path.abspath(translated_filename)}")


def main():
    api_keys = select_api_keys()
    files = select_audio_files()
    if not files:
        print("[!] No audio files selected. Exiting.")
        sys.exit(0)

    translate_lang = prompt_translation_choice()

    print(f"\n[+] {len(files)} file(s) queued, {len(api_keys)} key(s) available to rotate through.")
    for f in files:
        transcribe_file(api_keys, f, translate_lang)

    print("\n[+] All done.")


if __name__ == "__main__":
    main()
 
