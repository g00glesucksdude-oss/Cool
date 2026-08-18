#!/usr/bin/env python3
"""
YouTube Playlist Scraper — single-file GUI edition.

Paste playlist links in, each one gets its own card with its own folder
and its own "Update" button. Hitting Update only pulls videos that
weren't downloaded yet (tracked via a per-playlist archive file), so you
can re-check a playlist anytime without re-downloading everything.

Requires:
    pip install -U yt-dlp
Also needs ffmpeg on your PATH (for merging video+audio):
    Mac:     brew install ffmpeg
    Linux:   sudo apt install ffmpeg
    Windows: https://ffmpeg.org/download.html

Run:
    python playlist_scraper.py

--- YOUR DATA IS SAFE ACROSS UPDATES ---
Everything this app remembers — your playlist list (downloads/playlists.json)
and each playlist's list of already-downloaded videos (the .downloaded.txt
file inside each playlist folder) — lives in the downloads/ folder next to
this script, completely separate from the script's code.

If you ever replace this .py file with a newer version I send you, just
drop the new file in over the old one (same filename, same location) and
leave the downloads/ folder alone. Nothing about editing or overwriting
playlist_scraper.py touches downloads/ — they're independent, so your
playlists and download history survive every update.
"""

import json
import os
import sys
import threading
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox

try:
    import yt_dlp
except ImportError:
    sys.exit("yt-dlp is not installed.\nRun:\n\n    pip install -U yt-dlp\n")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.join(SCRIPT_DIR, "downloads")
CONFIG_FILE = os.path.join(BASE_DIR, "playlists.json")
os.makedirs(BASE_DIR, exist_ok=True)


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

        self.update_btn = ttk.Button(btns, text="Update", command=self.start_update)
        self.update_btn.pack(side="left")

        ttk.Button(btns, text="Open Folder", command=self.open_folder).pack(side="left", padx=6)
        ttk.Button(btns, text="Remove", command=self.remove).pack(side="left")

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

    def start_update(self):
        if self.busy:
            return
        self.busy = True
        self.update_btn.config(state="disabled")
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
            # --- helps avoid intermittent HTTP 403s from YouTube ---
            "retries": 10,
            "fragment_retries": 10,
            "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            },
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
            self.after_safe(lambda: self.update_btn.config(state="normal"))
            self.busy = False

    def after_safe(self, fn):
        self.app.root.after(0, fn)


class App:
    def __init__(self, root):
        self.root = root
        root.title("Playlist Scraper")
        root.geometry("560x600")

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

        # scrollable list of cards
        container = ttk.Frame(root)
        container.pack(fill="both", expand=True, padx=10, pady=10)

        canvas = tk.Canvas(container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        self.list_frame = ttk.Frame(canvas)

        self.list_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.list_frame, anchor="nw", width=520)
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


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
