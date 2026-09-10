"""
diarize_and_reassemble.py
==========================
Fully integrated, Colab-friendly pipeline: pyannote diarization/split,
auto-transcription (Groq or local/Colab faster-whisper, with full feature
parity to your original two scripts - model choice, cross-segment context
continuity, T4-aware quantization, link/ZIP download, fine-grained resume),
translation, and reassembly. One script, four modes.

  1. SPLIT       - pyannote diarization, splits source audio into one file
                    per speaker turn, saves segments + manifest.json to Drive.
  2. TRANSCRIBE  - auto-transcribes segments missing a transcript. Backend
                    of your choice (Groq or local), with the same model
                    choice, context continuity, and resume behavior your
                    original scripts had. Skips anything already transcribed,
                    so hand-dropped files (e.g. from your laptop) are left alone.
  3. REASSEMBLE  - stitches everything into one ordered, speaker + language
                    tagged transcript, auto-filling any still-missing segment
                    first. Offers translation (deep-translator, no API key).
  4. FULL RUN    - split -> transcribe -> reassemble, no manual handoff.

Nothing about your original scripts is touched - this is a self-contained
re-implementation of the same Groq/faster-whisper/download/translation logic,
wrapped around pyannote diarization.

HOW TO USE IN COLAB
--------------------
Run it, mounts Drive automatically, asks which mode. Everything lives under
MyDrive/Transcripts/ so it persists across sessions:
    MyDrive/Transcripts/
        segments/<source_basename>/    seg_0001_SPEAKER_00.wav, manifest.json
        transcripts/                   <seg>_transcript.txt (Groq) or <seg>.txt (local)
        hf_token.json                  pyannote token (asked once, split mode only)
        groq_keys.json                 Groq API key(s) (asked once, add more anytime)
        local_resume_state.json        fine-grained resume tracking (local backend)

YOU DO NEED A HUGGING FACE TOKEN FOR SPLIT MODE
--------------------------------------------------
pyannote/speaker-diarization-3.1 is gated - free, one-time setup:
  1. Accept access on both pages while logged into huggingface.co:
       https://huggingface.co/pyannote/speaker-diarization-3.1
       https://huggingface.co/pyannote/segmentation-3.0
  2. Grab a token: https://huggingface.co/settings/tokens

TRANSLATION DOES NOT NEED A KEY
--------------------------------
Uses deep-translator's GoogleTranslator - free, no account.
"""

# --- 1. AUTO-INSTALL DEPENDENCIES ---
import os
import subprocess
import sys

# Installing pyannote.audio/faster-whisper can upgrade numpy underneath a
# Colab kernel that already has an older numpy loaded in memory - that leaves
# two incompatible binary versions half-loaded and breaks on first import
# after a FRESH install (ImportError from numpy._core.umath etc). The fix is
# a runtime restart, not different inputs. This marker + self-restart makes
# that automatic instead of surfacing as a confusing crash: on a truly fresh
# install we restart the kernel once and ask you to just re-run the cell -
# /content survives the restart, so the marker is still there on the second
# run and it proceeds normally with a clean numpy.
_ON_COLAB = os.path.exists("/content")
_RESTART_MARKER = "/content/.diarize_pipeline_deps_ready"
_fresh_install = _ON_COLAB and not os.path.exists(_RESTART_MARKER)

print("[+] Checking and installing dependencies...")
reqs = ["pyannote.audio", "faster-whisper", "pydub", "groq", "deep-translator"]
subprocess.check_call([sys.executable, "-m", "pip", "install", "-q"] + reqs)

if _fresh_install:
    open(_RESTART_MARKER, "w").close()
    print("\n[!] Fresh install - Colab's runtime needs a one-time restart so numpy/torch load cleanly.")
    print("[+] Restarting automatically. When it reconnects (~10-20s), just run this cell again -")
    print("    dependencies are already installed, so the second run starts straight into the menu.")
    os.kill(os.getpid(), 9)

# --- 2. IMPORTS ---
import re
import json
import glob
import time
import shutil
import zipfile
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
else:
    DRIVE_SAVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Transcripts")

os.makedirs(DRIVE_SAVE_DIR, exist_ok=True)
SEGMENTS_ROOT = os.path.join(DRIVE_SAVE_DIR, "segments")
DEFAULT_TRANSCRIPTS_DIR = os.path.join(DRIVE_SAVE_DIR, "transcripts")
os.makedirs(DEFAULT_TRANSCRIPTS_DIR, exist_ok=True)
TOKEN_FILE = os.path.join(DRIVE_SAVE_DIR, "hf_token.json")
GROQ_KEYS_FILE = os.path.join(DRIVE_SAVE_DIR, "groq_keys.json")
LOCAL_RESUME_FILE = os.path.join(DRIVE_SAVE_DIR, "local_resume_state.json")
DOWNLOAD_DIR = os.path.join(DRIVE_SAVE_DIR, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

DEFAULT_MERGE_GAP = 0.5
DEFAULT_MIN_SEGMENT = 0.3
LANGID_MODEL_SIZE = "small"          # used during split for the manifest's language field
GROQ_MODEL_IDS = {"turbo": "whisper-large-v3-turbo", "large": "whisper-large-v3"}
MAX_CHUNK_BYTES = 24 * 1024 * 1024   # Groq's 25MB limit, 1MB headroom
RATE_LIMIT_COOLDOWN_SEC = 3
CONTEXT_CHARS = 800                  # how much trailing context to feed forward for continuity
AUDIO_EXTS = (".ogg", ".oga", ".mp3", ".wav", ".m4a", ".flac", ".mp4", ".webm")

COLAB_LINE_RE = re.compile(r"^\[(\d+\.\d+)s\s*->\s*(\d+\.\d+)s\]\s?(.*)$")


# =========================================================================
# SHARED: HF TOKEN
# =========================================================================
def is_valid_hf_token(token: str) -> bool:
    """Real HF tokens start with 'hf_' and are reasonably long.
    Rejects dummy/placeholder values like '00000', empty strings, etc."""
    if not token or not isinstance(token, str):
        return False
    token = token.strip()
    if token.startswith("hf_") and len(token) >= 20:
        return True
    return False


def load_token() -> str:
    env_token = os.environ.get("HF_TOKEN")
    if env_token and is_valid_hf_token(env_token):
        return env_token.strip()
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                token = json.load(f).get("token", "")
            if is_valid_hf_token(token):
                return token.strip()
            print(f"[!] Found invalid/dummy HF token in {TOKEN_FILE} — clearing it.")
            try:
                os.remove(TOKEN_FILE)
            except OSError:
                pass
        except (json.JSONDecodeError, OSError):
            pass
    return ""


def save_token(token: str):
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump({"token": token}, f, indent=2)


def clear_hf_token():
    """Remove any saved token (file + env) so the next get_hf_token() prompts again."""
    os.environ.pop("HF_TOKEN", None)
    if os.path.exists(TOKEN_FILE):
        try:
            os.remove(TOKEN_FILE)
            print(f"[+] Cleared bad token file: {TOKEN_FILE}")
        except OSError:
            pass


def get_hf_token(force: bool = False) -> str:
    if not force:
        token = load_token()
        if token:
            return token

    print("\n[+] No valid Hugging Face token found.")
    print("    Get one at https://huggingface.co/settings/tokens")
    print("    Make sure you've accepted access to pyannote/speaker-diarization-3.1")
    print("    and pyannote/segmentation-3.0 on huggingface.co first (see docstring above).")
    token = input("Paste your Hugging Face token: ").strip()
    if token and is_valid_hf_token(token):
        save_token(token)
        return token
    if token:
        print("[!] That does not look like a valid Hugging Face token (should start with 'hf_').")
    return ""


# =========================================================================
# SHARED: GROQ KEY MANAGEMENT (mirrors subtitler_groq_version.py's pattern)
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
        print("[+] No saved Groq API keys found on Drive. Let's add one.")
        keys = add_groq_key_prompt(keys)

    while True:
        print("\nSaved Groq API keys:")
        for i, k in enumerate(keys, 1):
            print(f"  {i}. {k['label']}")
        print(f"  {len(keys) + 1}. Add a new key")
        raw = input("Select key(s) to use (e.g. '1' or '1,3' or 'all'): ").strip().lower()

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
# DOWNLOAD SUPPORT (direct links, Mediafire, Google Drive, ZIPs of many files)
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
        "Mediafire may have changed their page layout - try downloading "
        "manually and re-hosting on a direct link instead."
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
            "Google Drive returned a webpage instead of the file. This usually "
            "means the file isn't shared as 'Anyone with the link', or Drive's "
            "confirmation page format changed. Check sharing settings and retry."
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
    """Downloads a link (direct/Mediafire/Google Drive), handles a ZIP of many
    audio files or a single audio file, returns a list of local paths."""
    dest = os.path.join(DOWNLOAD_DIR, "downloaded_input")
    suggested_name = download_link(url, dest)
    if not os.path.exists(dest) or os.path.getsize(dest) == 0:
        raise ValueError("Download failed or produced an empty file. Check the link and try again.")
    print(f"[OK] Downloaded {os.path.getsize(dest) / 1_000_000:.1f} MB (as '{suggested_name}')")

    if zipfile.is_zipfile(dest):
        print("[+] Detected a ZIP archive - extracting all audio files...")
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
            raise ValueError(f"No supported audio files ({', '.join(AUDIO_EXTS)}) found inside that ZIP.")
        return audio_files

    ext = os.path.splitext(suggested_name)[1].lower()
    if ext not in AUDIO_EXTS:
        raise ValueError(
            f"Downloaded file '{suggested_name}' doesn't look like a supported "
            f"audio format ({', '.join(AUDIO_EXTS)}) or a ZIP. Check the link."
        )
    single_name = os.path.splitext(os.path.basename(suggested_name))[0] + ext
    single_path = os.path.join(DOWNLOAD_DIR, single_name)
    shutil.move(dest, single_path)
    return [single_path]


# =========================================================================
# SPLIT MODE
# =========================================================================
def select_audio_files_split() -> list:
    print("\nHow do you want to provide audio?")
    print("  1. Upload file(s)" + (" (Colab upload dialog)" if IN_COLAB else " (not available outside Colab)"))
    print("  2. Type/paste local file path(s)")
    print("  3. Paste a download link (direct URL, Mediafire, or Google Drive - single file or ZIP)")
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

    raw = input("Enter audio file path(s) (comma-separated): ").strip()
    return [p.strip().strip('"') for p in raw.split(",") if p.strip()]


def run_diarization(audio_path: str, hf_token: str):
    from pyannote.audio import Pipeline
    import torch

    print("[+] Loading pyannote speaker-diarization-3.1 pipeline (first run downloads the model)...")
    try:
        try:
            pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", token=hf_token)
        except TypeError:
            # older pyannote.audio versions use the pre-deprecation kwarg name
            pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", use_auth_token=hf_token)
    except Exception as e:
        err_name = type(e).__name__
        err_msg = str(e).lower()
        is_auth_fail = (
            "401" in str(e)
            or "gated" in err_msg
            or "unauthorized" in err_msg
            or "authentication" in err_msg
            or err_name in ("GatedRepoError", "RepositoryNotFoundError", "HTTPStatusError")
        )
        if is_auth_fail:
            print("\n[!] Hugging Face rejected the token (401 / gated model).")
            print("    Clearing the bad token so the next run will ask again.")
            clear_hf_token()
            raise RuntimeError(
                "Invalid or unauthorized Hugging Face token for the gated pyannote models.\n"
                "  1. Accept access at:\n"
                "       https://huggingface.co/pyannote/speaker-diarization-3.1\n"
                "       https://huggingface.co/pyannote/segmentation-3.0\n"
                "  2. Create a token at https://huggingface.co/settings/tokens\n"
                "  3. Re-run this script — it will now prompt you for the token."
            ) from e
        raise

    if torch.cuda.is_available():
        pipeline.to(torch.device("cuda"))
        print("[+] Running diarization on GPU.")
    else:
        print("[+] Running diarization on CPU (this can be slow on long files).")

    print(f"[+] Diarizing '{os.path.basename(audio_path)}'...")
    output = pipeline(audio_path)

    # pyannote.audio 4.x returns DiarizeOutput; older versions returned Annotation directly
    if hasattr(output, "speaker_diarization"):
        annotation = output.speaker_diarization
    elif hasattr(output, "itertracks"):
        annotation = output
    else:
        raise RuntimeError(
            f"Unexpected pyannote output type: {type(output)}. "
            "Expected Annotation or DiarizeOutput with .speaker_diarization"
        )

    raw_turns = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        raw_turns.append((turn.start, turn.end, speaker))
    raw_turns.sort(key=lambda t: t[0])
    return raw_turns


def merge_and_filter(turns: list, merge_gap: float, min_segment: float) -> list:
    if not turns:
        return []
    merged = [list(turns[0])]
    for start, end, speaker in turns[1:]:
        last = merged[-1]
        if speaker == last[2] and start - last[1] <= merge_gap:
            last[1] = end
        else:
            merged.append([start, end, speaker])
    return [(s, e, spk) for s, e, spk in merged if (e - s) >= min_segment]


_langid_model = None


def get_langid_model():
    global _langid_model
    if _langid_model is None:
        from faster_whisper import WhisperModel
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        compute_type = "int8_float16" if device == "cuda" else "int8"
        print(f"[+] Loading faster-whisper '{LANGID_MODEL_SIZE}' for language ID ({device})...")
        _langid_model = WhisperModel(LANGID_MODEL_SIZE, device=device, compute_type=compute_type)
    return _langid_model


def detect_language(wav_path: str):
    model = get_langid_model()
    segments, info = model.transcribe(wav_path, beam_size=1, language=None, vad_filter=False)
    list(segments)  # drain generator, we only want info
    return info.language, round(info.language_probability, 3)


def normalize_audio_for_diarization(audio_path: str) -> str:
    """Convert any input to a clean 16 kHz mono WAV.
    Avoids pyannote sample-count mismatches that happen with some OGG/MP3 files."""
    basename = os.path.splitext(os.path.basename(audio_path))[0]
    clean_path = os.path.join(DOWNLOAD_DIR, f"{basename}_16k_mono.wav")

    if os.path.exists(clean_path) and os.path.getsize(clean_path) > 0:
        return clean_path

    print("[+] Normalizing audio to 16 kHz mono WAV (avoids pyannote sample-count bugs)...")
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", audio_path,
                "-ac", "1",
                "-ar", "16000",
                "-sample_fmt", "s16",
                clean_path,
            ],
            check=True, capture_output=True,
        )
    except Exception as e:
        print(f"[!] ffmpeg normalize failed ({e}), falling back to pydub...")
        from pydub import AudioSegment
        audio = AudioSegment.from_file(audio_path)
        audio = audio.set_channels(1).set_frame_rate(16000)
        audio.export(clean_path, format="wav")

    print(f"[OK] Normalized → {clean_path} ({os.path.getsize(clean_path)/1_000_000:.1f} MB)")
    return clean_path


def split_file(audio_path: str, merge_gap: float, min_segment: float, do_langid: bool, hf_token: str):
    from pydub import AudioSegment

    # Normalize first so pyannote never sees the original OGG/MP3
    clean_path = normalize_audio_for_diarization(audio_path)

    turns = run_diarization(clean_path, hf_token)
    turns = merge_and_filter(turns, merge_gap, min_segment)
    if not turns:
        print(f"[!] No speaker turns detected in '{audio_path}'. Skipping.")
        return None

    print(f"[+] {len(turns)} speaker turn(s) after merge/filter.")

    basename = os.path.splitext(os.path.basename(audio_path))[0]
    out_dir = os.path.join(SEGMENTS_ROOT, basename)
    os.makedirs(out_dir, exist_ok=True)

    print("[+] Loading audio for slicing...")
    # Slice from the clean 16 kHz file so timings stay consistent
    audio = AudioSegment.from_file(clean_path)

    manifest = {"source_file": os.path.abspath(audio_path), "segments": []}

    for i, (start_sec, end_sec, speaker) in enumerate(turns, start=1):
        seg_filename = f"seg_{i:04d}_{speaker}.wav"
        seg_path = os.path.join(out_dir, seg_filename)
        audio[int(start_sec * 1000):int(end_sec * 1000)].export(seg_path, format="wav")

        language, language_prob = (None, None)
        if do_langid:
            try:
                language, language_prob = detect_language(seg_path)
            except Exception as e:
                print(f"[!] Language ID failed on segment {i}: {e}")

        manifest["segments"].append({
            "index": i, "file": seg_filename, "speaker": speaker,
            "start_sec": round(start_sec, 3), "end_sec": round(end_sec, 3),
            "language": language, "language_prob": language_prob,
        })

        lang_note = f", lang={language} ({language_prob})" if language else ""
        print(f"  [{i}/{len(turns)}] {seg_filename}  {start_sec:.2f}s-{end_sec:.2f}s  {speaker}{lang_note}")

    manifest_path = os.path.join(out_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"[SUCCESS] Segments + manifest written to: {out_dir}")
    return manifest_path


def do_split_mode(prompt_for_settings: bool = True):
    hf_token = get_hf_token()
    if not hf_token:
        print("[!] No Hugging Face token provided. pyannote needs one. Aborting split.")
        return []

    files = select_audio_files_split()
    if not files:
        print("[!] No audio files selected.")
        return []

    merge_gap, min_segment, do_langid = DEFAULT_MERGE_GAP, DEFAULT_MIN_SEGMENT, True
    if prompt_for_settings:
        raw = input(f"Merge same-speaker gaps under how many seconds? (default {DEFAULT_MERGE_GAP}): ").strip()
        merge_gap = float(raw) if raw else DEFAULT_MERGE_GAP
        raw = input(f"Drop segments shorter than how many seconds? (default {DEFAULT_MIN_SEGMENT}): ").strip()
        min_segment = float(raw) if raw else DEFAULT_MIN_SEGMENT
        do_langid = input("Run per-segment language ID? (Y/n): ").strip().lower() != "n"

    manifest_paths = []
    for f in files:
        if not os.path.exists(f):
            print(f"[!] File not found, skipping: {f}")
            continue
        mp = split_file(f, merge_gap, min_segment, do_langid, hf_token)
        if mp:
            manifest_paths.append(mp)
    return manifest_paths


# =========================================================================
# TRANSCRIBE MODE
# =========================================================================
def find_transcript_file(seg_filename: str, transcripts_dirs: list):
    stem = os.path.splitext(seg_filename)[0]
    for d in transcripts_dirs:
        groq_path = os.path.join(d, f"{stem}_transcript.txt")
        if os.path.exists(groq_path):
            return groq_path, "groq"
        colab_path = os.path.join(d, f"{stem}.txt")
        if os.path.exists(colab_path):
            return colab_path, "colab"
    return None, None


def export_flac_under_limit(seg_path: str, tmp_path: str) -> str:
    from pydub import AudioSegment
    audio = AudioSegment.from_file(seg_path)
    audio.export(tmp_path, format="flac")
    if os.path.getsize(tmp_path) <= MAX_CHUNK_BYTES:
        return tmp_path
    print(f"[!] '{seg_path}' exceeds Groq's size limit even as FLAC - truncating to first half.")
    half = audio[:len(audio) // 2]
    half.export(tmp_path, format="flac")
    return tmp_path


def transcribe_segment_groq(client_holder: dict, api_keys: list, seg_path: str, model_id: str, prompt_context: str):
    """Returns (text, detected_lang). client_holder = {'client': Groq, 'key_index': int},
    mutated in place so key rotation persists across calls within one run."""
    tmp_path = seg_path + ".groqtmp.flac"
    export_flac_under_limit(seg_path, tmp_path)

    while True:
        try:
            with open(tmp_path, "rb") as audio_file:
                response = client_holder["client"].audio.transcriptions.create(
                    file=(os.path.basename(tmp_path), audio_file.read()),
                    model=model_id,
                    response_format="verbose_json",
                    prompt=prompt_context[-CONTEXT_CHARS:] if prompt_context else None,
                )
            text = response.text.strip()
            lang = getattr(response, "language", None)
            os.remove(tmp_path)
            return text, lang

        except Exception as e:
            if is_quota_or_rate_limit_error(e):
                print(f"[!] Key '{client_holder['key_index']+1}' hit a rate limit/quota error: {e}")
                time.sleep(RATE_LIMIT_COOLDOWN_SEC)
                client_holder["key_index"] += 1
                if client_holder["key_index"] >= len(api_keys):
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                    raise RuntimeError("All available Groq API keys are exhausted for now.")
                print(f"[+] Switching to key {client_holder['key_index']+1}/{len(api_keys)}...")
                from groq import Groq
                client_holder["client"] = Groq(api_key=api_keys[client_holder["key_index"]])
                continue
            else:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                raise


_transcribe_model_cache = {}


def get_transcribe_model(style: str):
    """style: 'turbo' or 'large'. Mirrors the original Colab script's GPU/T4-
    aware model + quantization selection exactly, with a CPU fallback to
    'small'/int8 regardless of style (same as the original)."""
    import torch

    cache_key = style
    if cache_key in _transcribe_model_cache:
        return _transcribe_model_cache[cache_key]

    from faster_whisper import WhisperModel

    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        print(f"[+] GPU detected ({gpu_name}).")
        is_t4 = "T4" in gpu_name
        if style == "turbo":
            if is_t4:
                print("[+] T4 detected - using int8_float16 quantization on 'medium' for better speed...")
                model = WhisperModel("medium", device="cuda", compute_type="int8_float16")
            else:
                print("[+] Loading faster-whisper on GPU (int8_float16, large-v3-turbo)...")
                model = WhisperModel("large-v3-turbo", device="cuda", compute_type="int8_float16")
        else:  # large - full accuracy
            if is_t4:
                print("[+] T4 detected - using int8_float16 quantization on large-v3 (full accuracy, slower)...")
                model = WhisperModel("large-v3", device="cuda", compute_type="int8_float16")
            else:
                print("[+] Loading faster-whisper on GPU (float16, large-v3)...")
                model = WhisperModel("large-v3", device="cuda", compute_type="float16")
    else:
        print("[!] CPU-only (no GPU attached). Loading faster-whisper 'small' (int8) regardless of style chosen.")
        model = WhisperModel("small", device="cpu", compute_type="int8")

    _transcribe_model_cache[cache_key] = model
    return model


def load_local_resume_state() -> dict:
    if not os.path.exists(LOCAL_RESUME_FILE):
        return {}
    try:
        with open(LOCAL_RESUME_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_local_resume_state(state: dict):
    with open(LOCAL_RESUME_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def transcribe_segment_local(model, seg_path: str, out_path: str, initial_prompt: str):
    """Writes '[start -> end] text' lines to out_path progressively (flush +
    fsync per line), and tracks a resume state on Drive keyed by seg_path.
    If a prior run was interrupted mid-segment, seeks past the completed
    portion via ffmpeg (exact re-encode seek, same technique as the original
    Colab script - not '-c copy', which snaps to the nearest keyframe) and
    continues appending rather than restarting the whole segment. Returns the
    full segment text and the last detected language."""
    state = load_local_resume_state()
    entry = state.get(seg_path, {"last_end": 0.0, "done": False})

    if entry.get("done") and os.path.exists(out_path):
        with open(out_path, "r", encoding="utf-8") as f:
            content = f.read()
        lines = [m.group(3) for m in COLAB_LINE_RE.finditer(content)] if content else []
        return " ".join(lines).strip(), None

    resume_offset = float(entry.get("last_end", 0.0))
    process_path = seg_path
    if resume_offset > 0:
        resume_tmp = seg_path + ".resume.wav"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", seg_path, "-ss", str(resume_offset), resume_tmp],
                check=True, capture_output=True,
            )
            process_path = resume_tmp
            print(f"    [+] Resuming this segment from {resume_offset:.2f}s (previous run was interrupted).")
        except Exception as e:
            print(f"    [!] Couldn't seek to resume point ({e}) - restarting this segment from scratch.")
            resume_offset = 0.0
            process_path = seg_path

    mode = "a" if resume_offset > 0 else "w"
    segments, info = model.transcribe(
        process_path, beam_size=1, language=None, vad_filter=False,
        initial_prompt=initial_prompt[-CONTEXT_CHARS:] if initial_prompt else None,
    )

    all_text = []
    with open(out_path, mode, encoding="utf-8") as f_out:
        for seg in segments:
            start, end = seg.start + resume_offset, seg.end + resume_offset
            text = seg.text.strip()
            all_text.append(text)
            f_out.write(f"[{start:.2f}s -> {end:.2f}s] {text}\n")
            f_out.flush()
            os.fsync(f_out.fileno())

            entry["last_end"] = end
            state[seg_path] = entry
            save_local_resume_state(state)

    entry["done"] = True
    state[seg_path] = entry
    save_local_resume_state(state)

    if process_path != seg_path and os.path.exists(process_path):
        os.remove(process_path)

    # if we resumed, all_text only has the NEW portion - read the full file back for context purposes
    if resume_offset > 0:
        with open(out_path, "r", encoding="utf-8") as f:
            content = f.read()
        all_text = [m.group(3) for m in COLAB_LINE_RE.finditer(content)]

    return " ".join(all_text).strip(), info.language


def seed_initial_context(manifest_path: str, transcripts_dirs: list, first_missing_index: int) -> str:
    """Builds trailing context from whatever segments before the first missing
    one already have a transcript, so continuity isn't lost just because some
    segments were done in a prior run or hand-dropped in."""
    entries, _, _ = build_entries(manifest_path, transcripts_dirs)
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    cutoff_start = manifest["segments"][first_missing_index]["start_sec"] if first_missing_index < len(manifest["segments"]) else None
    prior_text = " ".join(e["text"] for e in entries if cutoff_start is None or e["start"] < cutoff_start)
    return prior_text[-CONTEXT_CHARS:] if prior_text else ""


def auto_transcribe_missing(manifest_path: str, transcripts_dirs: list, backend: str, style: str) -> int:
    """Transcribes any segment in the manifest that doesn't already have a
    transcript file in transcripts_dirs, in chronological order, carrying
    running context forward for continuity (dropped on a detected language
    switch, same as your original scripts). Returns count newly transcribed."""
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    seg_dir = os.path.dirname(manifest_path)
    all_segments = manifest["segments"]
    missing_indices = [
        i for i, seg in enumerate(all_segments)
        if find_transcript_file(seg["file"], transcripts_dirs)[0] is None
    ]

    if not missing_indices:
        print("[+] Every segment already has a transcript.")
        return 0

    print(f"[+] {len(missing_indices)} segment(s) need transcribing (backend: {backend}, style: {style}).")

    client_holder = None
    api_keys = []
    local_model = None

    if backend == "groq":
        api_keys = select_groq_keys()
        from groq import Groq
        client_holder = {"client": Groq(api_key=api_keys[0]), "key_index": 0}
        model_id = GROQ_MODEL_IDS[style]
    else:
        local_model = get_transcribe_model(style)

    running_context = seed_initial_context(manifest_path, transcripts_dirs, missing_indices[0])
    last_lang = None

    done_count = 0
    for n, idx in enumerate(missing_indices, 1):
        seg = all_segments[idx]
        seg_path = os.path.join(seg_dir, seg["file"])
        stem = os.path.splitext(seg["file"])[0]
        print(f"  [{n}/{len(missing_indices)}] Transcribing {seg['file']} ({backend}/{style})...")

        try:
            if backend == "groq":
                text, lang = transcribe_segment_groq(client_holder, api_keys, seg_path, model_id, running_context)
                out_path = os.path.join(DEFAULT_TRANSCRIPTS_DIR, f"{stem}_transcript.txt")
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(text)
            else:
                out_path = os.path.join(DEFAULT_TRANSCRIPTS_DIR, f"{stem}.txt")
                text, lang = transcribe_segment_local(local_model, seg_path, out_path, running_context)

            if lang and last_lang and lang != last_lang:
                print(f"    [~] Language switch detected: '{last_lang}' -> '{lang}' (dropping context for continuity)")
                running_context = ""
            else:
                running_context = (running_context + " " + text).strip()
            if lang:
                last_lang = lang
                seg["language"] = lang  # transcriber's own detection supersedes the split-time langid pass

            done_count += 1

        except Exception as e:
            print(f"[!] Failed to transcribe {seg['file']}: {e}")
            print("    Progress so far is saved (completed segments + any partial local segment stay written) - "
                  "rerun transcribe/reassemble later to pick up where this left off.")
            break

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    return done_count


def prompt_backend_choice() -> tuple:
    print("\nWhich transcription backend?")
    print("  1. Groq API (needs API key(s), rate-limited)")
    print("  2. Local/Colab faster-whisper (uses GPU if available, no API key)")
    raw = input("Choose 1 or 2 (default 1): ").strip()
    backend = "local" if raw == "2" else "groq"

    print("\nWhich model style?")
    print("  1. turbo    - faster, redetects language per segment (handles language switches)")
    print("  2. large-v3 - slower, single-language accuracy mode")
    raw2 = input("Choose 1 or 2 (default 1): ").strip()
    style = "large" if raw2 == "2" else "turbo"

    return backend, style


def resolve_manifest_choice() -> str:
    manifests = sorted(glob.glob(os.path.join(SEGMENTS_ROOT, "*", "manifest.json")))
    if manifests:
        print("\n[+] Found manifest(s) under Drive segments folder:")
        for i, m in enumerate(manifests, 1):
            print(f"  {i}. {m}")
        raw = input("Pick a number, or paste a manifest.json path directly: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(manifests):
            return manifests[int(raw) - 1]
        return raw
    return input("Path to manifest.json: ").strip()


def do_transcribe_mode(manifest_path: str = None):
    manifest_path = manifest_path or resolve_manifest_choice()
    if not os.path.exists(manifest_path):
        print(f"[!] Manifest not found: {manifest_path}")
        return
    backend, style = prompt_backend_choice()
    transcripts_dirs = [DEFAULT_TRANSCRIPTS_DIR, os.path.dirname(manifest_path), os.getcwd()]
    n = auto_transcribe_missing(manifest_path, transcripts_dirs, backend, style)
    print(f"[+] Transcribed {n} segment(s).")


# =========================================================================
# REASSEMBLE MODE
# =========================================================================
def parse_transcript(path: str, fmt: str, seg_start_sec: float, seg_end_sec: float):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        return []

    if fmt == "colab":
        lines_out = []
        matched_any = False
        for line in content.splitlines():
            m = COLAB_LINE_RE.match(line.strip())
            if m:
                matched_any = True
                rel_start, rel_end, text = float(m.group(1)), float(m.group(2)), m.group(3)
                lines_out.append((seg_start_sec + rel_start, seg_start_sec + rel_end, text))
        if matched_any:
            return lines_out
        return [(seg_start_sec, seg_end_sec, content)]

    return [(seg_start_sec, seg_end_sec, content)]


def format_srt_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_entries(manifest_path: str, transcripts_dirs: list):
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    entries = []
    missing_count = 0

    for seg in manifest["segments"]:
        path, fmt = find_transcript_file(seg["file"], transcripts_dirs)

        if path is None:
            missing_count += 1
            entries.append({
                "start": seg["start_sec"], "end": seg["end_sec"], "speaker": seg["speaker"],
                "language": seg.get("language"), "text": "[MISSING TRANSCRIPT]", "source_segment": seg["file"],
            })
            continue

        for abs_start, abs_end, text in parse_transcript(path, fmt, seg["start_sec"], seg["end_sec"]):
            text = text.strip()
            if not text:
                continue
            entries.append({
                "start": round(abs_start, 3), "end": round(abs_end, 3), "speaker": seg["speaker"],
                "language": seg.get("language"), "text": text, "source_segment": seg["file"],
            })

    entries.sort(key=lambda e: e["start"])
    return entries, manifest, missing_count


def write_outputs(entries: list, output_basename: str, source_file: str):
    txt_path = f"{output_basename}.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        for e in entries:
            lang_tag = f"[{e['language']}] " if e["language"] else ""
            f.write(f"[{e['start']:.2f}s - {e['end']:.2f}s] [{e['speaker']}] {lang_tag}{e['text']}\n")

    srt_path = f"{output_basename}.srt"
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, e in enumerate(entries, start=1):
            lang_tag = f"[{e['language']}] " if e["language"] else ""
            f.write(f"{i}\n{format_srt_timestamp(e['start'])} --> {format_srt_timestamp(e['end'])}\n")
            f.write(f"[{e['speaker']}] {lang_tag}{e['text']}\n\n")

    json_path = f"{output_basename}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"source_file": source_file, "entries": entries}, f, indent=2)

    print(f"[SUCCESS] Wrote {len(entries)} entrie(s):\n  {txt_path}\n  {srt_path}\n  {json_path}")
    return txt_path, srt_path, json_path


# --- TRANSLATION (deep-translator's GoogleTranslator - free, no API key) ---
def ensure_translator_installed() -> bool:
    try:
        from deep_translator import GoogleTranslator  # noqa: F401
        return True
    except ImportError:
        print("[+] Installing 'deep-translator' for the translation feature...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "deep-translator"])
            return True
        except Exception as e:
            print(f"[!] Could not install deep-translator ({e}). Skipping translation.")
            return False


def prompt_translation_choice():
    raw = input(
        "\nWould you also like a translated version of the transcript? "
        "Enter a target language code (e.g. 'es', 'fr', 'ja'), or press Enter to skip: "
    ).strip()
    if not raw:
        return None
    if not ensure_translator_installed():
        return None
    return raw.lower()


def translate_entries(entries: list, target_lang: str) -> list:
    """Translates each entry's text individually rather than one merged blob -
    every entry already has exact timestamp/speaker boundaries from the
    manifest, so there's no risk of the translator merging/splitting lines and
    desyncing them from timestamps (the failure mode the original scripts had
    to guard against with a length-mismatch fallback)."""
    from deep_translator import GoogleTranslator
    translator = GoogleTranslator(source="auto", target=target_lang)
    texts = [e["text"] for e in entries]

    try:
        translated = translator.translate_batch(texts)
    except Exception as e:
        print(f"[!] Batch translation failed ({e}), falling back to one-by-one...")
        translated = []
        for t in texts:
            try:
                translated.append(translator.translate(t))
            except Exception as e2:
                print(f"[!] Translation error on a line, leaving it untranslated: {e2}")
                translated.append(t)

    if len(translated) != len(texts):
        print("[!] Translated output length didn't match input - leaving text untranslated for safety.")
        translated = texts

    out_entries = []
    for e, t in zip(entries, translated):
        e2 = dict(e)
        e2["original_text"] = e["text"]
        e2["text"] = t if t else e["text"]
        out_entries.append(e2)
    return out_entries


def reassemble(manifest_path: str, transcripts_dirs: list, output_basename: str, offer_translation: bool = True):
    entries, manifest, missing_count = build_entries(manifest_path, transcripts_dirs)
    source_file = manifest.get("source_file")

    write_outputs(entries, output_basename, source_file)
    if missing_count:
        print(f"[!] {missing_count} segment(s) had no matching transcript file - marked [MISSING TRANSCRIPT].")

    if offer_translation:
        target_lang = prompt_translation_choice()
        if target_lang:
            print(f"[+] Translating to '{target_lang}'...")
            translated_entries = translate_entries(entries, target_lang)
            write_outputs(translated_entries, f"{output_basename}_{target_lang}", source_file)


def do_reassemble_mode(manifest_path: str = None):
    manifest_path = manifest_path or resolve_manifest_choice()
    if not os.path.exists(manifest_path):
        print(f"[!] Manifest not found: {manifest_path}")
        return

    default_dirs = [DEFAULT_TRANSCRIPTS_DIR, os.path.dirname(manifest_path), os.getcwd()]
    transcripts_dirs = default_dirs

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    still_missing = [s for s in manifest["segments"] if find_transcript_file(s["file"], transcripts_dirs)[0] is None]

    if still_missing:
        print(f"\n[+] {len(still_missing)} segment(s) don't have a transcript yet.")
        auto = input("Auto-transcribe them now before reassembling? (Y/n): ").strip().lower() != "n"
        if auto:
            backend, style = prompt_backend_choice()
            auto_transcribe_missing(manifest_path, transcripts_dirs, backend, style)

    out_dir = os.path.dirname(manifest_path)
    default_output = os.path.join(out_dir, "final_transcript")
    raw = input(f"Output basename (default: {default_output}): ").strip()
    output_basename = raw or default_output

    reassemble(manifest_path, transcripts_dirs, output_basename)


# =========================================================================
# FULL RUN MODE
# =========================================================================
def do_full_run():
    manifest_paths = do_split_mode(prompt_for_settings=True)
    if not manifest_paths:
        print("[!] Split produced nothing to transcribe. Stopping.")
        return

    backend, style = prompt_backend_choice()
    target_lang = prompt_translation_choice()

    for mp in manifest_paths:
        transcripts_dirs = [DEFAULT_TRANSCRIPTS_DIR, os.path.dirname(mp), os.getcwd()]
        auto_transcribe_missing(mp, transcripts_dirs, backend, style)
        out_dir = os.path.dirname(mp)
        output_basename = os.path.join(out_dir, "final_transcript")

        entries, manifest, missing_count = build_entries(mp, transcripts_dirs)
        write_outputs(entries, output_basename, manifest.get("source_file"))
        if missing_count:
            print(f"[!] {missing_count} segment(s) had no matching transcript file - marked [MISSING TRANSCRIPT].")
        if target_lang:
            print(f"[+] Translating to '{target_lang}'...")
            translated_entries = translate_entries(entries, target_lang)
            write_outputs(translated_entries, f"{output_basename}_{target_lang}", manifest.get("source_file"))


# =========================================================================
# MAIN
# =========================================================================
def main():
    print("Which step do you want to run?")
    print("  1. SPLIT       - diarize + split source audio into speaker-turn segments")
    print("  2. TRANSCRIBE  - auto-transcribe segments missing a transcript (Groq or local)")
    print("  3. REASSEMBLE  - stitch transcribed segments into one transcript (auto-fills gaps)")
    print("  4. FULL RUN    - split -> transcribe -> reassemble, all in one go")
    choice = input("Choose 1-4: ").strip()

    if choice == "1":
        do_split_mode()
    elif choice == "2":
        do_transcribe_mode()
    elif choice == "3":
        do_reassemble_mode()
    elif choice == "4":
        do_full_run()
    else:
        print("[!] Please enter a number 1-4.")


if __name__ == "__main__":
    main()
