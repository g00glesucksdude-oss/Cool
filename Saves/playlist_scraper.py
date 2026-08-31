#!/usr/bin/env python3
"""
YouTube Playlist Scraper — single-file GUI edition.

Paste playlist links in, each one gets its own card with its own folder
and its own "Update Video" button. Hitting Update only pulls videos that
weren't downloaded yet (tracked via a per-playlist archive file), so you
can re-check a playlist anytime without re-downloading everything.

Each card also has:
  - "Audio Only"   — pulls just the audio (mp3) for the whole playlist,
                      tracked with its own separate archive so it doesn't
                      interfere with the video archive.
  - "Redownload #" — type a playlist position (e.g. 63) and it forces a
                      fresh redownload of just that one item (useful if a
                      file got corrupted or you deleted it by hand).

There's also a standalone Subtitle Downloader at the top of the window —
paste any YouTube video or playlist link and it grabs subtitles (manual
if available, otherwise auto-generated) into downloads/subtitles/. If a
video has no subtitles at all, it'll tell you that and point you at your
offline transcriber project instead, since that's the tool for the job
when there's nothing to grab.

Requires:
    pip install -U yt-dlp
Also needs ffmpeg on your PATH (for merging video+audio and for
extracting audio-only tracks):
    Mac:     brew install ffmpeg
    Linux:   sudo apt install ffmpeg
    Windows: https://ffmpeg.org/download.html

Run:
    python playlist_scraper.py

--- YOUR DATA IS SAFE ACROSS UPDATES ---
Everything this app remembers — your playlist list (downloads/playlists.json),
each playlist's list of already-downloaded videos (the .downloaded.txt file
inside each playlist folder), and now also each playlist's audio archive
(downloads/<playlist>/audio/.downloaded_audio.txt) — lives in the downloads/
folder next to this script, completely separate from the script's code.

If you ever replace this .py file with a newer version I send you, just
drop the new file in over the old one (same filename, same location) and
leave the downloads/ folder alone. Nothing about editing or overwriting
playlist_scraper.py touches downloads/ — they're independent, so your
playlists and download history survive every update. This version reads
the exact same playlists.json format as before, so your existing list of
playlists will load in without any changes.
"""

import json
import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox

try:
    import yt_dlp
except ImportError:
    sys.exit("yt-dlp is not installed.\nRun:\n\n    pip install -U yt-dlp\n")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.join(SCRIPT_DIR, "downloads")
CONFIG_FILE = os.path.join(BASE_DIR, "playlists.json")
SUBTITLE_DIR = os.path.join(BASE_DIR, "subtitles")
os.makedirs(BASE_DIR, exist_ok=True)

COMMON_YDL_OPTS = {
    # --- helps avoid intermittent HTTP 403s from YouTube ---
    "retries": 10,
    "fragment_retries": 10,
    "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    "http_headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    },
}


def sanitize_name(name: str) -> str:
    keep = "-_.() "
    return "".join(c for c in name if c.isalnum() or c in keep).strip() or "playlist"


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_config(playlists):
    with open(CONFIG_FILE, "w") as f:
        json.dump(playlists, f, indent=2)


class PlaylistCard(ttk.Frame):
    def __init__(self, master, app, data):
        super().__init__(master, padding=10, relief="groove", borderwidth=1)
        self.app = app
        self.data = data  # {"url":..., "title":..., "folder":...}
        self.busy = False

        top = ttk.Frame(self)
        top.pack(fill="x")

        self.title_label = ttk.Label(top, text=data["title"], font=("Segoe UI", 11, "bold"))
        self.title_label.pack(side="left")

        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=(4, 0))

        self.update_btn = ttk.Button(btns, text="Update Video", command=self.start_update)
        self.update_btn.pack(side="left")

        self.audio_btn = ttk.Button(btns, text="Audio Only", command=self.start_audio_update)
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

        self.status_var = tk.StringVar(value="Not downloaded yet")
        ttk.Label(self, textvariable=self.status_var, foreground="#555").pack(fill="x", pady=(6, 0))

        self.progress = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(4, 0))

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
        if messagebox.askyesno("Remove playlist", f"Remove '{self.data['title']}' from the list?\n(Downloaded files stay on disk.)"):
            self.app.remove_playlist(self)

    def _lock_buttons(self):
        self.update_btn.config(state="disabled")
        self.audio_btn.config(state="disabled")
        self.redl_btn.config(state="disabled")

    def _reset_buttons(self):
        self.update_btn.config(state="normal")
        self.audio_btn.config(state="normal")
        self.redl_btn.config(state="normal")

    # ------------------------------------------------------------------
    # Full video update (unchanged behavior from before)
    # ------------------------------------------------------------------
    def start_update(self):
        if self.busy:
            return
        self.busy = True
        self._lock_buttons()
        self.status_var.set("Checking for new videos…")
        self.progress["value"] = 0
        threading.Thread(target=self.run_download, daemon=True).start()

    def run_download(self):
        archive_file = os.path.join(self.data["folder"], ".downloaded.txt")
        new_count = {"n": 0}

        def hook(d):
            if d["status"] == "downloading":
                pct = d.get("_percent_str", "").strip()
                fname = os.path.basename(d.get("filename", ""))
                self.after_safe(lambda: self.status_var.set(f"Downloading: {fname} ({pct})"))
                try:
                    pct_num = float(pct.replace("%", ""))
                    self.after_safe(lambda: self.progress.config(value=pct_num))
                except ValueError:
                    pass
            elif d["status"] == "finished":
                new_count["n"] += 1

        ydl_opts = {
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
            "outtmpl": os.path.join(self.data["folder"], "%(playlist_index)03d - %(title)s.%(ext)s"),
            "download_archive": archive_file,
            "ignoreerrors": True,
            "continuedl": True,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [hook],
            **COMMON_YDL_OPTS,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([self.data["url"]])
            if new_count["n"] == 0:
                msg = "Up to date — no new videos."
            else:
                msg = f"Done — {new_count['n']} new video(s) downloaded."
            self.after_safe(lambda: self.status_var.set(msg))
        except Exception as e:
            self.after_safe(lambda: self.status_var.set(f"Error: {e}"))
        finally:
            self.after_safe(lambda: self.progress.config(value=0))
            self.after_safe(self._reset_buttons)
            self.busy = False

    # ------------------------------------------------------------------
    # Audio-only download (new)
    # ------------------------------------------------------------------
    def start_audio_update(self):
        if self.busy:
            return
        self.busy = True
        self._lock_buttons()
        self.status_var.set("Checking for new audio…")
        self.progress["value"] = 0
        threading.Thread(target=self.run_audio_download, daemon=True).start()

    def run_audio_download(self):
        audio_folder = os.path.join(self.data["folder"], "audio")
        os.makedirs(audio_folder, exist_ok=True)
        # Kept separate from the video archive on purpose: a video that's
        # already in .downloaded.txt (video form) still needs to be
        # fetched here the first time you ask for audio, and vice versa.
        archive_file = os.path.join(audio_folder, ".downloaded_audio.txt")
        new_count = {"n": 0}

        def hook(d):
            if d["status"] == "downloading":
                pct = d.get("_percent_str", "").strip()
                fname = os.path.basename(d.get("filename", ""))
                self.after_safe(lambda: self.status_var.set(f"Downloading audio: {fname} ({pct})"))
                try:
                    pct_num = float(pct.replace("%", ""))
                    self.after_safe(lambda: self.progress.config(value=pct_num))
                except ValueError:
                    pass
            elif d["status"] == "finished":
                new_count["n"] += 1

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(audio_folder, "%(playlist_index)03d - %(title)s.%(ext)s"),
            "download_archive": archive_file,
            "ignoreerrors": True,
            "continuedl": True,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [hook],
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            **COMMON_YDL_OPTS,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([self.data["url"]])
            if new_count["n"] == 0:
                msg = "Audio up to date — no new tracks."
            else:
                msg = f"Done — {new_count['n']} new audio track(s) downloaded."
            self.after_safe(lambda: self.status_var.set(msg))
        except Exception as e:
            self.after_safe(lambda: self.status_var.set(f"Error: {e}"))
        finally:
            self.after_safe(lambda: self.progress.config(value=0))
            self.after_safe(self._reset_buttons)
            self.busy = False

    # ------------------------------------------------------------------
    # Redownload a single item by playlist position (new)
    # ------------------------------------------------------------------
    def start_redownload(self):
        if self.busy:
            return
        idx_str = self.redl_entry.get().strip()
        if not idx_str.isdigit():
            messagebox.showerror("Redownload", "Enter a playlist position number, e.g. 63")
            return
        idx = int(idx_str)
        self.busy = True
        self._lock_buttons()
        self.status_var.set(f"Looking up #{idx}…")
        self.progress["value"] = 0
        threading.Thread(target=self.run_redownload, args=(idx,), daemon=True).start()

    @staticmethod
    def _remove_from_archive(archive_file, vid_id):
        if not vid_id or not os.path.exists(archive_file):
            return
        try:
            with open(archive_file, "r") as f:
                lines = f.readlines()
            kept = [ln for ln in lines if vid_id not in ln]
            if len(kept) != len(lines):
                with open(archive_file, "w") as f:
                    f.writelines(kept)
        except Exception:
            pass

    def run_redownload(self, idx):
        archive_file = os.path.join(self.data["folder"], ".downloaded.txt")
        audio_archive_file = os.path.join(self.data["folder"], "audio", ".downloaded_audio.txt")

        try:
            probe_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": True,
                "skip_download": True,
                "playlist_items": str(idx),
            }
            with yt_dlp.YoutubeDL(probe_opts) as probe:
                info = probe.extract_info(self.data["url"], download=False)
            entries = (info or {}).get("entries") or []
            if not entries or not entries[0]:
                self.after_safe(lambda: self.status_var.set(f"No video found at position {idx}."))
                return
            vid_id = entries[0].get("id")
            vid_title = entries[0].get("title", "")

            # Clear it from both archives so this forces a real redownload
            # regardless of whether you'd grabbed it as video, audio, or both.
            self._remove_from_archive(archive_file, vid_id)
            self._remove_from_archive(audio_archive_file, vid_id)

            def hook(d):
                if d["status"] == "downloading":
                    pct = d.get("_percent_str", "").strip()
                    self.after_safe(lambda: self.status_var.set(f"Redownloading #{idx}: {vid_title} ({pct})"))
                    try:
                        pct_num = float(pct.replace("%", ""))
                        self.after_safe(lambda: self.progress.config(value=pct_num))
                    except ValueError:
                        pass

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
                "progress_hooks": [hook],
                **COMMON_YDL_OPTS,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([self.data["url"]])
            self.after_safe(lambda: self.status_var.set(f"Redownloaded #{idx} — {vid_title}"))
        except Exception as e:
            self.after_safe(lambda: self.status_var.set(f"Error: {e}"))
        finally:
            self.after_safe(lambda: self.progress.config(value=0))
            self.after_safe(self._reset_buttons)
            self.busy = False

    def after_safe(self, fn):
        self.app.root.after(0, fn)


class App:
    def __init__(self, root):
        self.root = root
        root.title("Playlist Scraper")
        root.geometry("580x720")

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

        # --- Subtitle Downloader (new, standalone, works on any YT link) ---
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

        # scrollable list of playlist cards
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
        # kick off first download automatically
        self.cards[-1].start_update()

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

    # ------------------------------------------------------------------
    # Subtitle downloader (new)
    # ------------------------------------------------------------------
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
                    "Nothing to grab here — your offline transcriber project would be "
                    "the better tool for this one."
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
