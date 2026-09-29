#!/usr/bin/env python3
"""
YouTube Playlist Scraper — single-file GUI + phone sync.

Paste playlist links → each gets a card with its own folder.
  • Update Video / Audio Only  — only fetches what's missing (archive + manifest).
  • Redownload #               — force one position.
  • Auto-update                — set from the phone page.
  • Phone / LAN page           — open the link on any device on the same Wi-Fi:
        - live status & progress
        - Play / Save every file
        - Sync button: pick a folder on the phone, then only missing files
          are streamed into it (Chrome / Edge recommended).

Data lives only in downloads/ next to this script, so you can replace the .py
anytime without losing playlists, archives, or the saved port.

Requires:
    pip install -U yt-dlp
    ffmpeg on PATH
"""

import http.server
import json
import mimetypes
import os
import random
import re
import socket
import sys
import tempfile
import threading
import time
import urllib.parse
import zipfile
import tkinter as tk
from tkinter import ttk, messagebox

try:
    import yt_dlp
except ImportError:
    sys.exit("yt-dlp is not installed.\nRun:\n\n    pip install -U yt-dlp\n")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.join(SCRIPT_DIR, "downloads")
CONFIG_FILE = os.path.join(BASE_DIR, "playlists.json")
PORT_FILE = os.path.join(BASE_DIR, "server_port.txt")
SUBTITLE_DIR = os.path.join(BASE_DIR, "subtitles")
os.makedirs(BASE_DIR, exist_ok=True)

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".m4v", ".mov"}
AUDIO_EXTS = {".mp3", ".m4a", ".opus", ".ogg", ".aac", ".flac", ".wav"}
AUTO_CHOICES = (0, 30, 60, 180, 360, 720, 1440)  # minutes; 0 = off
PORT_RANGE = (49152, 65535)
MANIFEST_LOCK = threading.Lock()

COMMON_YDL_OPTS = {
    "retries": 10,
    "fragment_retries": 10,
    "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    "http_headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    },
}

_TEMP_STEM = re.compile(r"\.(f\d+|temp)$")
_INDEX_PREFIX = re.compile(r"^\d+\s*-\s*")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def load_saved_port():
    try:
        with open(PORT_FILE, "r", encoding="utf-8") as f:
            p = int(f.read().strip())
        if PORT_RANGE[0] <= p <= PORT_RANGE[1]:
            return p
    except Exception:
        pass
    return None


def save_port(port):
    try:
        with open(PORT_FILE, "w", encoding="utf-8") as f:
            f.write(str(port))
    except Exception:
        pass


def sanitize_name(name: str) -> str:
    keep = "-_.() "
    return "".join(c for c in name if c.isalnum() or c in keep).strip() or "playlist"


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def save_config(playlists):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(playlists, f, indent=2)


def describe_minutes(m):
    if m < 60:
        return f"{m} min"
    hours = m // 60
    if hours == 24:
        return "day"
    return "hour" if hours == 1 else f"{hours} hours"


def norm_title(s):
    return "".join(c.lower() for c in s if c.isalnum())


def read_archive_ids(path):
    ids = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for ln in f:
                parts = ln.split()
                if len(parts) >= 2:
                    ids.append(parts[-1])
    except Exception:
        pass
    return list(dict.fromkeys(ids))


def drop_from_archive(path, ids):
    ids = set(ids)
    if not ids or not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        kept = [ln for ln in lines if not (ln.split() and ln.split()[-1] in ids)]
        if len(kept) != len(lines):
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(kept)
    except Exception:
        pass


def manifest_path(folder):
    return os.path.join(folder, ".manifest.json")


def load_manifest(folder):
    try:
        with open(manifest_path(folder), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_manifest(folder, manifest):
    try:
        with open(manifest_path(folder), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=1)
    except Exception:
        pass


def scan_media(folder, exts, with_size=True):
    out = []
    try:
        with os.scandir(folder) as it:
            for e in it:
                if not e.is_file() or e.name.startswith("."):
                    continue
                stem, ext = os.path.splitext(e.name)
                if ext.lower() not in exts or _TEMP_STEM.search(stem):
                    continue
                out.append({"name": e.name, "size": e.stat().st_size if with_size else 0})
    except FileNotFoundError:
        pass
    out.sort(key=lambda x: x["name"].lower())
    return out


def index_files_by_title(folder, exts):
    result = {}
    for f in scan_media(folder, exts, with_size=False):
        stem = os.path.splitext(f["name"])[0]
        key = norm_title(_INDEX_PREFIX.sub("", stem))
        if key:
            result.setdefault(key, f["name"])
    return result


def fetch_entries(url):
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    out = []
    for i, e in enumerate((info or {}).get("entries") or [], 1):
        if e and e.get("id"):
            out.append({"id": e["id"], "title": e.get("title") or "", "index": i})
    return out


def entries_of(info):
    if not info:
        return []
    if info.get("entries") is not None:
        return [e for e in info["entries"] if e]
    return [info]


def get_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    return None if not ip or ip.startswith("127.") or ip == "0.0.0.0" else ip


# ---------------------------------------------------------------------------
# Playlist card (GUI + shared state for the web server)
# ---------------------------------------------------------------------------

class PlaylistCard(ttk.Frame):
    def __init__(self, master, app, data):
        super().__init__(master, padding=10, relief="groove", borderwidth=1)
        self.app = app
        self.data = data
        self.busy = False
        self._busy_lock = threading.Lock()
        self.status_text = "Not downloaded yet"
        self.progress_value = 0.0
        self.current_kind = None
        self.next_auto = time.time() + 30 if data.get("auto_minutes") else 0

        top = ttk.Frame(self)
        top.pack(fill="x")
        self.title_label = ttk.Label(top, text=data["title"], font=("Segoe UI", 11, "bold"))
        self.title_label.pack(side="left")

        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=(4, 0))
        self.update_btn = ttk.Button(btns, text="Update Video", command=lambda: self.start_jobs(["video"]))
        self.update_btn.pack(side="left")
        self.audio_btn = ttk.Button(btns, text="Audio Only", command=lambda: self.start_jobs(["audio"]))
        self.audio_btn.pack(side="left", padx=6)
        ttk.Button(btns, text="Open Folder", command=self.open_folder).pack(side="left", padx=6)
        ttk.Button(btns, text="Remove", command=self.remove).pack(side="left")

        redl_row = ttk.Frame(self)
        redl_row.pack(fill="x", pady=(4, 0))
        ttk.Label(redl_row, text="Redownload #:").pack(side="left")
        self.redl_entry = ttk.Entry(redl_row, width=6)
        self.redl_entry.pack(side="left", padx=(4, 4))
        self.redl_entry.bind("<Return>", lambda e: self.start_redownload())
        self.redl_btn = ttk.Button(redl_row, text="Redownload", command=self.start_redownload)
        self.redl_btn.pack(side="left")

        self.auto_var = tk.StringVar()
        ttk.Label(self, textvariable=self.auto_var, foreground="#555").pack(anchor="w", pady=(4, 0))
        self._refresh_auto_label()

        self.status_var = tk.StringVar(value=self.status_text)
        ttk.Label(self, textvariable=self.status_var, foreground="#555", wraplength=500,
                  justify="left").pack(fill="x", pady=(6, 0))
        self.progress = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(4, 0))

    def kind_folder(self, kind):
        return os.path.join(self.data["folder"], "audio") if kind == "audio" else self.data["folder"]

    def archive_path(self, kind):
        if kind == "audio":
            return os.path.join(self.data["folder"], "audio", ".downloaded_audio.txt")
        return os.path.join(self.data["folder"], ".downloaded.txt")

    def after_safe(self, fn):
        try:
            self.app.root.after(0, fn)
        except Exception:
            pass

    def set_status(self, text):
        self.status_text = text
        self.after_safe(lambda: self.status_var.set(text))

    def set_progress(self, pct):
        self.progress_value = pct
        self.after_safe(lambda: self.progress.config(value=pct))

    def open_folder(self):
        path = self.data["folder"]
        os.makedirs(path, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')

    def remove(self):
        if messagebox.askyesno("Remove playlist",
                               f"Remove '{self.data['title']}' from the list?\n(Downloaded files stay on disk.)"):
            self.app.remove_playlist(self)

    def _lock_buttons(self):
        self.update_btn.config(state="disabled")
        self.audio_btn.config(state="disabled")
        self.redl_btn.config(state="disabled")

    def _reset_buttons(self):
        self.update_btn.config(state="normal")
        self.audio_btn.config(state="normal")
        self.redl_btn.config(state="normal")

    def _try_acquire(self):
        with self._busy_lock:
            if self.busy:
                return False
            self.busy = True
            return True

    def _refresh_auto_label(self):
        m = self.data.get("auto_minutes", 0)
        self.auto_var.set("Auto-update: off" if not m else f"Auto-update: every {describe_minutes(m)}")

    def set_auto(self, minutes):
        if minutes not in AUTO_CHOICES:
            return
        if minutes:
            self.data["auto_minutes"] = minutes
        else:
            self.data.pop("auto_minutes", None)
        self.next_auto = time.time() + minutes * 60
        self._refresh_auto_label()
        self.app.persist()

    def list_files(self):
        return {
            "video": scan_media(self.kind_folder("video"), VIDEO_EXTS),
            "audio": scan_media(self.kind_folder("audio"), AUDIO_EXTS),
        }

    def web_info(self):
        return {
            "title": self.data["title"],
            "busy": self.busy,
            "kind": self.current_kind,
            "status": self.status_text,
            "progress": round(self.progress_value, 1),
            "auto_minutes": self.data.get("auto_minutes", 0),
            "videos": len(scan_media(self.kind_folder("video"), VIDEO_EXTS, with_size=False)),
            "audios": len(scan_media(self.kind_folder("audio"), AUDIO_EXTS, with_size=False)),
        }

    # -- manifest / archive ------------------------------------------------

    def _record_file(self, kind, vid, fname):
        with MANIFEST_LOCK:
            manifest = load_manifest(self.data["folder"])
            manifest.setdefault(kind, {})[vid] = fname
            save_manifest(self.data["folder"], manifest)

    def _record_from_info(self, kind, info):
        done = set()
        for e in entries_of(info):
            for rd in e.get("requested_downloads") or []:
                path = rd.get("filepath")
                if e.get("id") and path and os.path.isfile(path):
                    self._record_file(kind, e["id"], os.path.basename(path))
                    done.add(e["id"])
        return done

    def _make_pp_hook(self, kind, done):
        def hook(d):
            if d.get("status") == "finished" and d.get("postprocessor") == "MoveFiles":
                info = d.get("info_dict") or {}
                vid, path = info.get("id"), info.get("filepath")
                if vid and path:
                    self._record_file(kind, vid, os.path.basename(path))
                    done.add(vid)
        return hook

    def _make_progress_hook(self, describe):
        def hook(d):
            if d.get("status") != "downloading":
                return
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            got = d.get("downloaded_bytes")
            pct = (got * 100.0 / total) if total and got is not None else None
            self.set_status(describe(d, f"{pct:.0f}%" if pct is not None else "…"))
            if pct is not None:
                self.set_progress(min(pct, 100.0))
        return hook

    def _sync_archive(self, kind, entries):
        folder = self.kind_folder(kind)
        archive = self.archive_path(kind)
        archived = read_archive_ids(archive)
        if not archived:
            return 0, 0

        live = {e["id"]: e for e in entries}
        exts = AUDIO_EXTS if kind == "audio" else VIDEO_EXTS
        by_title = index_files_by_title(folder, exts)

        missing, left_alone = [], 0
        with MANIFEST_LOCK:
            manifest = load_manifest(self.data["folder"])
            section = manifest.setdefault(kind, {})
            for vid in archived:
                entry = live.get(vid)
                if entry is None:
                    left_alone += 1
                    continue
                fname = section.get(vid)
                if fname and os.path.isfile(os.path.join(folder, fname)):
                    continue
                found = by_title.get(norm_title(entry["title"])) if entry["title"] else None
                if found:
                    section[vid] = found
                    continue
                section.pop(vid, None)
                missing.append(vid)
            save_manifest(self.data["folder"], manifest)

        drop_from_archive(archive, missing)
        return len(missing), left_alone

    # -- update jobs -------------------------------------------------------

    def start_jobs(self, kinds):
        if not self._try_acquire():
            return False
        self._lock_buttons()
        self.set_status("Checking the playlist…")
        self.set_progress(0)
        threading.Thread(target=self._run_jobs, args=(list(kinds),), daemon=True).start()
        return True

    def _run_jobs(self, kinds):
        messages = []
        try:
            for kind in kinds:
                self.current_kind = kind
                messages.append(self._run_one(kind))
            self.set_status("  ".join(m for m in messages if m))
        except Exception as e:
            self.set_status(f"Error: {e}")
        finally:
            self.current_kind = None
            self.set_progress(0)
            self.after_safe(self._reset_buttons)
            self.busy = False
            minutes = self.data.get("auto_minutes", 0)
            self.next_auto = time.time() + minutes * 60 if minutes else 0

    def _run_one(self, kind):
        audio = kind == "audio"
        noun = "audio" if audio else "video"
        folder = self.kind_folder(kind)
        os.makedirs(folder, exist_ok=True)
        archive = self.archive_path(kind)

        self.set_status("Checking the playlist…")
        self.set_progress(0)
        try:
            entries = fetch_entries(self.data["url"])
        except Exception as e:
            return f"Couldn't read the playlist (offline?): {e}"

        restored, left_alone = self._sync_archive(kind, entries)
        if restored:
            self.set_status(f"{restored} deleted file(s) found — restoring…")

        done = set()
        ydl_opts = {
            "outtmpl": os.path.join(folder, "%(playlist_index)03d - %(title)s.%(ext)s"),
            "download_archive": archive,
            "ignoreerrors": True,
            "continuedl": True,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [self._make_progress_hook(
                lambda d, pct: f"Downloading {noun}: {os.path.basename(d.get('filename', ''))} ({pct})")],
            "postprocessor_hooks": [self._make_pp_hook(kind, done)],
            **COMMON_YDL_OPTS,
        }
        if audio:
            ydl_opts["format"] = "bestaudio/best"
            ydl_opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
        else:
            ydl_opts["format"] = "bestvideo+bestaudio/best"
            ydl_opts["merge_output_format"] = "mp4"

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.data["url"], download=True)
            done |= self._record_from_info(kind, info)
        except Exception as e:
            return f"Error: {e}"

        n = len(done)
        label = "audio track(s)" if audio else "video(s)"
        if n == 0 and restored:
            msg = f"Couldn't re-download {restored} deleted file(s). They'll be retried next update."
        elif n == 0:
            msg = f"{'Audio' if audio else 'Videos'} up to date — nothing new."
        else:
            msg = f"Done — {n} {label} downloaded"
            msg += f" ({min(restored, n)} restored after you deleted them)." if restored else "."
        if left_alone:
            msg += f" {left_alone} no longer in the playlist (left alone)."
        return msg

    # -- redownload one item -----------------------------------------------

    def start_redownload(self):
        if self.busy:
            return
        idx_str = self.redl_entry.get().strip()
        if not idx_str.isdigit():
            messagebox.showerror("Redownload", "Enter a playlist position number, e.g. 63")
            return
        idx = int(idx_str)
        if not self._try_acquire():
            return
        self._lock_buttons()
        self.set_status(f"Looking up #{idx}…")
        self.set_progress(0)
        threading.Thread(target=self.run_redownload, args=(idx,), daemon=True).start()

    def run_redownload(self, idx):
        archive_file = self.archive_path("video")
        audio_archive_file = self.archive_path("audio")
        self.current_kind = "video"
        try:
            probe_opts = {
                "quiet": True, "no_warnings": True, "extract_flat": True,
                "skip_download": True, "playlist_items": str(idx),
            }
            with yt_dlp.YoutubeDL(probe_opts) as probe:
                info = probe.extract_info(self.data["url"], download=False)
            entries = (info or {}).get("entries") or []
            if not entries or not entries[0]:
                self.set_status(f"No video found at position {idx}.")
                return
            vid_id = entries[0].get("id")
            vid_title = entries[0].get("title", "")

            drop_from_archive(archive_file, [vid_id])
            drop_from_archive(audio_archive_file, [vid_id])

            done = set()
            ydl_opts = {
                "format": "bestvideo+bestaudio/best",
                "merge_output_format": "mp4",
                "outtmpl": os.path.join(self.data["folder"], "%(playlist_index)03d - %(title)s.%(ext)s"),
                "download_archive": archive_file,
                "playlist_items": str(idx),
                "force_overwrites": True,
                "ignoreerrors": True,
                "continuedl": True,
                "quiet": True,
                "no_warnings": True,
                "progress_hooks": [self._make_progress_hook(
                    lambda d, pct: f"Redownloading #{idx}: {vid_title} ({pct})")],
                "postprocessor_hooks": [self._make_pp_hook("video", done)],
                **COMMON_YDL_OPTS,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(self.data["url"], download=True)
            self._record_from_info("video", result)
            self.set_status(f"Redownloaded #{idx} — {vid_title}")
        except Exception as e:
            self.set_status(f"Error: {e}")
        finally:
            self.current_kind = None
            self.set_progress(0)
            self.after_safe(self._reset_buttons)
            self.busy = False


# ---------------------------------------------------------------------------
# Phone / LAN page (includes folder-sync via File System Access API)
# ---------------------------------------------------------------------------

PAGE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Playlists</title>
<style>
:root{color-scheme:light dark;--bg:#f1f4f7;--surface:#fff;--ink:#14202b;--muted:#586675;--line:#d6dde4;--accent:#0a67bf;--accent-ink:#fff;--warn:#a85a00}
@media (prefers-color-scheme:dark){:root{--bg:#0d141b;--surface:#151f29;--ink:#e6edf3;--muted:#93a2b1;--line:#243241;--accent:#4ea4f2;--accent-ink:#06121d;--warn:#f0a84a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 "Avenir Next","Segoe UI",system-ui,sans-serif;-webkit-text-size-adjust:100%}
main{max-width:640px;margin:0 auto;padding:20px 16px 72px}
h1{font-size:1.8rem;letter-spacing:-.01em;margin:8px 0 6px}
#offline{margin:8px 0 0;padding:8px 12px;border-left:3px solid var(--warn);background:var(--surface);font-size:.92rem}
#empty{color:var(--muted);margin-top:24px}
.playlist{padding:22px 0 18px;border-top:1px solid var(--line)}
.playlist:first-child{border-top:0}
h2{font-size:1.15rem;line-height:1.3;margin:0 0 10px;overflow-wrap:anywhere}
.bar{height:3px;background:var(--line);border-radius:2px;overflow:hidden}
.fill{height:100%;width:0;background:var(--accent);transition:width .4s linear}
.bar.wait .fill{width:30%;animation:slide 1.1s ease-in-out infinite alternate}
@keyframes slide{from{margin-left:0}to{margin-left:70%}}
.meta{margin:8px 0 0;color:var(--muted);font-size:.9rem}
.status{margin:2px 0 14px;min-height:1.4em;font-size:.95rem;overflow-wrap:anywhere}
.actions{display:flex;flex-wrap:wrap;gap:8px}
button,select{font:inherit;min-height:44px;border-radius:10px;border:1px solid var(--line)}
button{padding:0 16px;background:var(--accent);color:var(--accent-ink);border-color:var(--accent);font-weight:600}
button.quiet{background:transparent;color:var(--ink);border-color:var(--line)}
button:disabled{opacity:.45}
label.auto{display:flex;align-items:center;gap:10px;margin-top:10px;color:var(--muted);font-size:.92rem}
select{padding:0 10px;background:var(--surface);color:var(--ink)}
details{margin-top:10px}
summary{cursor:pointer;min-height:44px;display:flex;align-items:center;font-weight:600}
h3{font-size:.95rem;margin:14px 0 2px;color:var(--muted);font-weight:600}
.none{color:var(--muted);margin:6px 0;font-size:.92rem}
.file{display:grid;grid-template-columns:1fr auto;gap:2px 12px;padding:10px 0;border-top:1px solid var(--line)}
.fname{grid-column:1/-1;overflow-wrap:anywhere;font-size:.95rem}
.size{color:var(--muted);font-size:.85rem;align-self:center}
.links{display:flex;gap:18px}
a{color:var(--accent);font-weight:600;text-decoration:none;padding:6px 0}
a:focus-visible,button:focus-visible,select:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
#toast{position:fixed;left:50%;bottom:20px;transform:translate(-50%,16px);max-width:90vw;background:var(--ink);color:var(--bg);padding:10px 16px;border-radius:10px;opacity:0;pointer-events:none;transition:opacity .2s,transform .2s;z-index:10}
#toast.show{opacity:1;transform:translate(-50%,0)}
.hint{font-size:.85rem;color:var(--muted);margin:6px 0 0}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
</style>
</head>
<body>
<main>
  <h1>Playlists</h1>
  <p id="offline" hidden>Lost the connection to your PC. Trying again…</p>
  <div id="list"></div>
  <p id="empty" hidden>No playlists yet. Add one in the app on your PC and it will show up here.</p>
</main>
<div id="toast" role="status" aria-live="polite"></div>
<script>
const AUTO = [[0,"Off"],[30,"Every 30 min"],[60,"Every hour"],[180,"Every 3 hours"],[360,"Every 6 hours"],[720,"Every 12 hours"],[1440,"Every day"]];
const list = document.getElementById("list");
const emptyMsg = document.getElementById("empty");
const offlineMsg = document.getElementById("offline");
const toastEl = document.getElementById("toast");
const cards = new Map();
let timer = null, toastTimer = null;

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid);
  return el;
}
const enc = encodeURIComponent;

function fmtSize(n) {
  if (n < 1024) return n + " B";
  const units = ["KB", "MB", "GB"];
  let i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < units.length - 1);
  return n.toFixed(n >= 100 ? 0 : 1) + " " + units[i];
}

function toast(msg) {
  toastEl.textContent = msg;
  toastEl.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove("show"), 3600);
}

function makeCard(p) {
  const c = { title: p.title, wasBusy: false, counts: "", syncing: false };
  c.name = h("h2", {}, p.title);
  c.fill = h("div", { class: "fill" });
  c.bar = h("div", { class: "bar" }, c.fill);
  c.meta = h("p", { class: "meta" });
  c.status = h("p", { class: "status" });
  c.btnVideo = h("button", { onclick: () => update(c, "video") }, "Update videos");
  c.btnAudio = h("button", { class: "quiet", onclick: () => update(c, "audio") }, "Update audio");
  c.btnSync = h("button", { class: "quiet", onclick: () => syncFolder(c) }, "Sync");
  c.auto = h("select", { "aria-label": "Auto-update", onchange: () => setAuto(c) },
    AUTO.map(([m, label]) => h("option", { value: m }, label)));
  c.list = h("div", {});
  c.files = h("details", { ontoggle: () => { if (c.files.open) loadFiles(c); } },
    h("summary", {}, "Files"), c.list);
  c.hint = h("p", { class: "hint" }, "Sync: enter a range (e.g. 1-10) → downloads it as one zip.");
  c.root = h("section", { class: "playlist" },
    c.name, c.bar, c.meta, c.status,
    h("div", { class: "actions" }, c.btnVideo, c.btnAudio, c.btnSync),
    h("label", { class: "auto" }, "Auto-update", c.auto),
    c.hint, c.files);
  return c;
}

function paint(c, p) {
  c.fill.style.width = p.busy ? Math.max(p.progress, 0) + "%" : "0";
  c.bar.classList.toggle("wait", p.busy && p.progress < 1);
  c.meta.textContent = p.videos + (p.videos === 1 ? " video, " : " videos, ") + p.audios + " audio";
  c.status.textContent = p.status;
  const busy = p.busy || c.syncing;
  c.btnVideo.disabled = c.btnAudio.disabled = busy;
  c.btnSync.disabled = p.busy;
  if (document.activeElement !== c.auto) c.auto.value = String(p.auto_minutes || 0);
  const counts = p.videos + "/" + p.audios;
  if (c.files.open && ((c.wasBusy && !p.busy) || counts !== c.counts)) loadFiles(c);
  c.counts = counts;
  c.wasBusy = p.busy;
}

function render(state) {
  const seen = new Set();
  state.playlists.forEach((p, i) => {
    seen.add(p.title);
    let c = cards.get(p.title);
    if (!c) { c = makeCard(p); cards.set(p.title, c); }
    if (list.children[i] !== c.root) list.insertBefore(c.root, list.children[i] || null);
    paint(c, p);
  });
  for (const [title, c] of cards) {
    if (!seen.has(title)) { c.root.remove(); cards.delete(title); }
  }
  emptyMsg.hidden = state.playlists.length > 0;
}

async function loadFiles(c) {
  try {
    const r = await fetch("/api/files?playlist=" + enc(c.title), { cache: "no-store" });
    if (!r.ok) throw new Error("bad response");
    const j = await r.json();
    c.list.replaceChildren(group(c, "Videos", "video", j.video), group(c, "Audio", "audio", j.audio));
  } catch (e) {
    c.list.textContent = "Couldn't load the file list.";
  }
}

function group(c, label, kind, files) {
  const box = h("div", {}, h("h3", {}, label));
  if (!files.length) box.append(h("p", { class: "none" },
    kind === "video" ? "No videos downloaded yet." : "No audio downloaded yet."));
  for (const f of files) {
    const base = "/files/" + enc(c.title) + "/" + kind + "/" + enc(f.name);
    box.append(h("div", { class: "file" },
      h("span", { class: "fname" }, f.name),
      h("span", { class: "size" }, fmtSize(f.size)),
      h("span", { class: "links" },
        h("a", { href: base, target: "_blank", rel: "noopener" }, "Play"),
        h("a", { href: base + "?dl=1", download: f.name }, "Save"))));
  }
  return box;
}

async function update(c, kind) {
  c.btnVideo.disabled = c.btnAudio.disabled = true;
  try {
    const r = await fetch("/api/update?playlist=" + enc(c.title) + "&kind=" + kind, { method: "POST" });
    const j = await r.json();
    if (!j.ok) toast(j.error || "Couldn't start the update.");
  } catch (e) {
    toast("Can't reach your PC.");
  }
  setTimeout(refresh, 400);
}

async function setAuto(c) {
  try {
    const r = await fetch("/api/auto?playlist=" + enc(c.title) + "&minutes=" + c.auto.value, { method: "POST" });
    const j = await r.json();
    toast(j.ok ? (c.auto.value === "0" ? "Auto-update is off." : "Auto-update saved.") : (j.error || "Couldn't save that."));
  } catch (e) {
    toast("Can't reach your PC.");
  }
}

/* ---------- Sync: range -> one zip ---------- */
async function syncFolder(c) {
  const ans = prompt("Range to download as a zip (e.g. 1-10 or 5):", "1-10");
  if (!ans) return;
  const m = ans.trim().match(/^(\d+)\s*(?:-\s*(\d+))?$/);
  if (!m) { toast("Use a range like 1-10."); return; }
  const from = Math.min(+m[1], +(m[2] || m[1])), to = Math.max(+m[1], +(m[2] || m[1]));
  try {
    const r = await fetch("/api/files?playlist=" + enc(c.title), { cache: "no-store" });
    const j = await r.json();
    const n = [...(j.video || []), ...(j.audio || [])].filter(f => {
      const k = parseInt(f.name, 10);
      return k >= from && k <= to;
    }).length;
    if (!n) { toast("No files in that range."); return; }
    const a = h("a", { href: "/api/zip?playlist=" + enc(c.title) + "&from=" + from + "&to=" + to });
    document.body.append(a);
    a.click();
    a.remove();
    toast("Zipping " + n + " file" + (n === 1 ? "" : "s") + "…");
  } catch (e) {
    toast("Can't reach your PC.");
  }
}

async function refresh() {
  clearTimeout(timer);
  let delay = 6000;
  try {
    const r = await fetch("/api/state", { cache: "no-store" });
    if (!r.ok) throw new Error("bad response");
    const state = await r.json();
    render(state);
    offlineMsg.hidden = true;
    if (state.playlists.some(p => p.busy)) delay = 1500;
  } catch (e) {
    offlineMsg.hidden = false;
    delay = 4000;
  }
  timer = setTimeout(refresh, delay);
}

refresh();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class LanServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True


def make_handler(app):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "PlaylistScraper"

        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            try:
                if u.path in ("/", "/index.html"):
                    self._send(200, PAGE_HTML.encode("utf-8"), "text/html; charset=utf-8")
                elif u.path == "/api/state":
                    self._json(app.web_state())
                elif u.path == "/api/files":
                    card = app.find_card(q.get("playlist", [""])[0])
                    if card is None:
                        self._json({"error": "Unknown playlist."}, 404)
                    else:
                        self._json(card.list_files())
                elif u.path == "/api/zip":
                    self._serve_zip(q)
                elif u.path.startswith("/files/"):
                    self._serve_file(u.path, q)
                else:
                    self._json({"error": "Not found."}, 404)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def do_POST(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                card = app.find_card(q.get("playlist", [""])[0])
                if card is None:
                    return self._json({"ok": False, "error": "Unknown playlist."}, 404)

                if u.path == "/api/update":
                    kind = q.get("kind", ["video"])[0]
                    if kind not in ("video", "audio", "both"):
                        return self._json({"ok": False, "error": "Unknown update type."}, 400)
                    if card.busy:
                        return self._json({"ok": False, "error": "Already updating this playlist."}, 409)
                    kinds = ["video", "audio"] if kind == "both" else [kind]
                    app.root.after(0, lambda: card.start_jobs(kinds))
                    return self._json({"ok": True})

                if u.path == "/api/auto":
                    try:
                        minutes = int(q.get("minutes", ["0"])[0])
                    except ValueError:
                        minutes = -1
                    if minutes not in AUTO_CHOICES:
                        return self._json({"ok": False, "error": "Unsupported interval."}, 400)
                    app.root.after(0, lambda: card.set_auto(minutes))
                    return self._json({"ok": True})

                self._json({"ok": False, "error": "Not found."}, 404)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def _serve_zip(self, q):
            card = app.find_card(q.get("playlist", [""])[0])
            try:
                lo, hi = int(q.get("from", ["0"])[0]), int(q.get("to", ["0"])[0])
            except ValueError:
                return self._json({"error": "Bad range."}, 400)
            if card is None:
                return self._json({"error": "Unknown playlist."}, 404)
            picked = []
            for kind, exts in (("video", VIDEO_EXTS), ("audio", AUDIO_EXTS)):
                folder = card.kind_folder(kind)
                for f in scan_media(folder, exts, with_size=False):
                    m = re.match(r"\d+", f["name"])
                    if m and lo <= int(m.group()) <= hi:
                        picked.append((os.path.join(folder, f["name"]), kind + "/" + f["name"]))
            if not picked:
                return self._json({"error": "No files in that range."}, 404)
            with tempfile.TemporaryFile(dir=BASE_DIR) as tmp:
                with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
                    for full, arc in picked:
                        z.write(full, arc)
                size = tmp.tell()
                tmp.seek(0)
                zname = f"{card.data['title']} {lo}-{hi}.zip"
                ascii_name = zname.encode("ascii", "replace").decode("ascii").replace('"', "'")
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(size))
                self.send_header("Content-Disposition",
                                 f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{urllib.parse.quote(zname)}")
                self.end_headers()
                while True:
                    chunk = tmp.read(1 << 20)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

        def _serve_file(self, path, q):
            parts = path[len("/files/"):].split("/")
            if len(parts) != 3:
                return self._json({"error": "Bad path."}, 400)
            title, kind, name = (urllib.parse.unquote(p) for p in parts)
            card = app.find_card(title)
            exts = {"video": VIDEO_EXTS, "audio": AUDIO_EXTS}.get(kind)
            if card is None or exts is None:
                return self._json({"error": "Not found."}, 404)
            if (not name or name != os.path.basename(name) or name.startswith(".")
                    or os.path.splitext(name)[1].lower() not in exts):
                return self._json({"error": "Not found."}, 404)
            full = os.path.join(card.kind_folder(kind), name)
            if not os.path.isfile(full):
                return self._json({"error": "That file is gone."}, 404)

            size = os.path.getsize(full)
            start, end, status = 0, size - 1, 200
            rng = self.headers.get("Range")
            if rng and rng.startswith("bytes=") and size > 0:
                try:
                    first, _, last = rng[6:].split(",")[0].strip().partition("-")
                    if first == "":
                        start = max(size - int(last), 0)
                    else:
                        start = int(first)
                        end = min(int(last), size - 1) if last else size - 1
                    if start > end or start >= size:
                        raise ValueError
                    status = 206
                except ValueError:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

            length = max(end - start + 1, 0)
            disp = "attachment" if q.get("dl") else "inline"
            ascii_name = name.encode("ascii", "replace").decode("ascii").replace('"', "'")
            self.send_response(status)
            self.send_header("Content-Type", mimetypes.guess_type(name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Disposition",
                             f"{disp}; filename=\"{ascii_name}\"; filename*=UTF-8''{urllib.parse.quote(name)}")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with open(full, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(1 << 20, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

    return Handler


class ServerManager:
    CHECK_MS = 10000

    def __init__(self, app):
        self.app = app
        self.httpd = None
        self.ip = None
        self.port = None

    @property
    def url(self):
        return f"http://{self.ip}:{self.port}" if self.httpd else None

    def tick(self):
        try:
            ip = get_lan_ip()
            if ip and self.httpd is None:
                self._start(ip)
            elif ip and ip != self.ip:
                self.ip = ip
                self.app.set_server_status(self.url)
            elif not ip and self.httpd is not None:
                self.stop()
                self.app.set_server_status(None)
        finally:
            self.app.root.after(self.CHECK_MS, self.tick)

    def _start(self, ip):
        handler = make_handler(self.app)
        httpd = None
        preferred = load_saved_port()
        candidates = []
        if preferred is not None:
            candidates.append(preferred)
        for _ in range(100):
            p = random.randint(*PORT_RANGE)
            if p not in candidates:
                candidates.append(p)

        for port in candidates:
            try:
                httpd = LanServer(("0.0.0.0", port), handler)
                break
            except OSError:
                httpd = None
                continue

        if httpd is None:
            self.app.set_server_status(None, error="couldn't find a free port")
            return
        self.httpd, self.ip, self.port = httpd, ip, port
        save_port(port)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.app.set_server_status(self.url)

    def stop(self):
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass
        self.httpd = self.ip = self.port = None


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

class App:
    def __init__(self, root):
        self.root = root
        root.title("Playlist Scraper")
        root.geometry("580x780")

        top = ttk.Frame(root, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Paste a YouTube playlist link:").pack(anchor="w")
        entry_row = ttk.Frame(top)
        entry_row.pack(fill="x", pady=(4, 0))
        self.url_entry = ttk.Entry(entry_row)
        self.url_entry.pack(side="left", fill="x", expand=True)
        self.url_entry.bind("<Return>", lambda e: self.add_playlist())
        ttk.Button(entry_row, text="Add Playlist", command=self.add_playlist).pack(side="left", padx=(6, 0))

        self.add_status = ttk.Label(top, text="", foreground="#555")
        self.add_status.pack(anchor="w", pady=(4, 0))

        srv_row = ttk.Frame(top)
        srv_row.pack(fill="x", pady=(6, 0))
        self.server_url = None
        self.server_var = tk.StringVar(value="Phone access: checking the network…")
        ttk.Label(srv_row, textvariable=self.server_var, foreground="#555").pack(side="left")
        self.copy_btn = ttk.Button(srv_row, text="Copy link", command=self.copy_link, state="disabled")
        self.copy_btn.pack(side="right")

        # Subtitle downloader
        sub_frame = ttk.LabelFrame(root, text="Subtitle Downloader (YouTube)", padding=10)
        sub_frame.pack(fill="x", padx=10, pady=(0, 10))
        sub_row = ttk.Frame(sub_frame)
        sub_row.pack(fill="x")
        ttk.Label(sub_row, text="Video or playlist link:").pack(side="left")
        self.sub_url_entry = ttk.Entry(sub_row)
        self.sub_url_entry.pack(side="left", fill="x", expand=True, padx=(6, 6))
        ttk.Label(sub_row, text="Lang:").pack(side="left")
        self.sub_lang_entry = ttk.Entry(sub_row, width=6)
        self.sub_lang_entry.insert(0, "en")
        self.sub_lang_entry.pack(side="left", padx=(4, 6))
        self.sub_btn = ttk.Button(sub_row, text="Download Subtitles", command=self.start_subtitle_download)
        self.sub_btn.pack(side="left")
        self.sub_status = ttk.Label(sub_frame, text="", foreground="#555", wraplength=540, justify="left")
        self.sub_status.pack(fill="x", pady=(6, 0))

        # scrollable cards
        container = ttk.Frame(root)
        container.pack(fill="both", expand=True, padx=10, pady=10)
        canvas = tk.Canvas(container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        self.list_frame = ttk.Frame(canvas)
        self.list_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.list_frame, anchor="nw", width=540)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.cards = []
        for data in load_config():
            self.add_card(data)

        self.server = ServerManager(self)
        root.after(300, self.server.tick)
        root.after(30000, self._auto_tick)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    def set_server_status(self, url, error=None):
        self.server_url = url
        if url:
            self.server_var.set(f"Phone access: {url}")
            self.copy_btn.config(state="normal")
        else:
            why = f" ({error})" if error else ""
            self.server_var.set(f"Phone access: offline{why} — waiting for a network connection")
            self.copy_btn.config(state="disabled")

    def copy_link(self):
        if not self.server_url:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.server_url)
        self.copy_btn.config(text="Copied")
        self.root.after(1500, lambda: self.copy_btn.config(text="Copy link"))

    def find_card(self, title):
        for c in list(self.cards):
            if c.data["title"] == title:
                return c
        return None

    def web_state(self):
        return {"playlists": [c.web_info() for c in list(self.cards)]}

    def _auto_tick(self):
        now = time.time()
        for card in list(self.cards):
            if card.data.get("auto_minutes") and not card.busy and now >= card.next_auto:
                kinds = ["video"]
                if os.path.exists(card.archive_path("audio")):
                    kinds.append("audio")
                card.start_jobs(kinds)
        self.root.after(30000, self._auto_tick)

    def on_close(self):
        try:
            self.server.stop()
        finally:
            self.root.destroy()

    def add_playlist(self):
        url = self.url_entry.get().strip()
        if not url:
            return
        if any(c.data["url"] == url for c in self.cards):
            self.add_status.config(text="That playlist is already in your list.")
            return
        self.add_status.config(text="Fetching playlist info…")
        self.url_entry.config(state="disabled")
        threading.Thread(target=self._fetch_and_add, args=(url,), daemon=True).start()

    def _fetch_and_add(self, url):
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": True, "skip_download": True}) as probe:
                info = probe.extract_info(url, download=False)
            title = sanitize_name(info.get("title") or "playlist")
        except Exception as e:
            self.root.after(0, lambda: self._add_failed(str(e)))
            return
        folder = os.path.join(BASE_DIR, title)
        os.makedirs(folder, exist_ok=True)
        data = {"url": url, "title": title, "folder": folder}
        self.root.after(0, lambda: self._add_succeeded(data))

    def _add_failed(self, err):
        self.add_status.config(text=f"Couldn't read that playlist: {err}")
        self.url_entry.config(state="normal")

    def _add_succeeded(self, data):
        self.add_card(data)
        self.persist()
        self.add_status.config(text=f'Added "{data["title"]}"')
        self.url_entry.delete(0, "end")
        self.url_entry.config(state="normal")
        self.cards[-1].start_jobs(["video"])

    def add_card(self, data):
        card = PlaylistCard(self.list_frame, self, data)
        card.pack(fill="x", pady=6)
        self.cards.append(card)

    def remove_playlist(self, card):
        card.destroy()
        self.cards.remove(card)
        self.persist()

    def persist(self):
        save_config([c.data for c in self.cards])

    def start_subtitle_download(self):
        url = self.sub_url_entry.get().strip()
        lang = self.sub_lang_entry.get().strip() or "en"
        if not url:
            return
        self.sub_btn.config(state="disabled")
        self.sub_status.config(text="Downloading subtitles…")
        threading.Thread(target=self._run_subtitle_download, args=(url, lang), daemon=True).start()

    def _run_subtitle_download(self, url, lang):
        os.makedirs(SUBTITLE_DIR, exist_ok=True)
        ydl_opts = {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": [lang],
            "subtitlesformat": "srt/best",
            "outtmpl": os.path.join(SUBTITLE_DIR, "%(playlist_index)03d - %(title)s.%(ext)s"),
            "ignoreerrors": True,
            "quiet": True,
            "no_warnings": True,
            **COMMON_YDL_OPTS,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
            entries = info.get("entries") if info and info.get("entries") is not None else [info]
            entries = [e for e in entries if e]
            total = len(entries)
            found = sum(1 for e in entries if e.get("requested_subtitles"))

            if total == 0:
                msg = "Couldn't read that link."
            elif found == 0:
                msg = (
                    f"No '{lang}' subtitles (manual or auto-generated) were found. "
                    "Nothing to grab here — your offline transcriber project would "
                    "be the better tool for this one."
                )
            elif found < total:
                msg = (
                    f"Got subtitles for {found}/{total} video(s) in downloads/subtitles/. "
                    f"The rest had no '{lang}' subtitles available — try the offline "
                    "transcriber for those."
                )
            else:
                msg = f"Done — subtitles saved for {found} video(s) in downloads/subtitles/."
            self.root.after(0, lambda: self.sub_status.config(text=msg))
        except Exception as e:
            self.root.after(0, lambda: self.sub_status.config(text=f"Error: {e}"))
        finally:
            self.root.after(0, lambda: self.sub_btn.config(state="normal"))


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
