#!/usr/bin/env python3
"""
Audio Pool Compiler v2

Workflow:
  audio pool -> one master -> 8-hour parts
  -> select one or more 8-hour parts
  -> split each selected part at silence near a configurable target length
  -> transcribe chunks externally (e.g. cbro33 Whisper GUI)
  -> select transcript folder(s)
  -> automatically merge transcripts in chronological chunk order

Requires ffmpeg + ffprobe on PATH.
"""

from pathlib import Path
import json, re, shutil, subprocess, threading, tkinter as tk
from tkinter import ttk, filedialog, messagebox

AUDIO_EXTS = {".mp3",".wav",".m4a",".flac",".ogg",".oga",".aac",".webm",".opus"}
EIGHT_HOURS = 8 * 60 * 60


def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode:
        raise RuntimeError(p.stderr[-4000:] or "Command failed")
    return p.stdout.strip()


def natural_key(p):
    return [int(x) if x.isdigit() else x.lower()
            for x in re.split(r"(\d+)", p.name)]


def normalize(src, dst):
    run(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(src),
         "-vn","-ac","1","-ar","16000","-c:a","pcm_s16le",str(dst)])


def compile_pool(files, out, log):
    work = out.parent / "_compiler_tmp"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    normalized = []
    for i, src in enumerate(files, 1):
        log(f"Preparing {i}/{len(files)}: {src.name}")
        dst = work / f"{i:06d}.wav"
        normalize(src, dst)
        normalized.append(dst)

    concat = work / "concat.txt"
    concat.write_text(
        "\n".join("file '" + p.as_posix().replace("'", "'\\''") + "'"
                  for p in normalized),
        encoding="utf-8"
    )
    log("Joining audio...")
    run(["ffmpeg","-y","-hide_banner","-loglevel","error",
         "-f","concat","-safe","0","-i",str(concat),"-c","copy",str(out)])
    shutil.rmtree(work, ignore_errors=True)
    log(f"Created master: {out}")


def split_fixed(src, outdir, seconds, prefix, log):
    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob(prefix + "*.wav"):
        old.unlink()
    log(f"Creating {seconds/3600:g}-hour parts...")
    run(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(src),
         "-f","segment","-segment_time",str(seconds),"-reset_timestamps","1",
         "-c","copy",str(outdir/(prefix+"%06d.wav"))])
    return sorted(outdir.glob(prefix+"*.wav"), key=natural_key)


def get_duration(path):
    return float(run(["ffprobe","-v","error","-show_entries","format=duration",
                      "-of","default=noprint_wrappers=1:nokey=1",str(path)]))


def detect_silences(path, log):
    # Broad speech-friendly silence detection. We only use silence points as
    # candidate boundaries; the target duration remains the primary constraint.
    log(f"Finding silence points in {path.name}...")
    raw = run(["ffmpeg","-hide_banner","-i",str(path),
               "-af","silencedetect=noise=-35dB:d=0.35",
               "-f","null","-"],)
    starts, ends = [], []
    for line in raw.splitlines():
        m = re.search(r"silence_start:\s*([0-9.]+)", line)
        if m: starts.append(float(m.group(1)))
        m = re.search(r"silence_end:\s*([0-9.]+)", line)
        if m: ends.append(float(m.group(1)))
    points = []
    for a,b in zip(starts, ends):
        points.append((a+b)/2)
    return points


def split_at_silence(src, outdir, target, log):
    """
    Make sequential chunks whose boundaries are near target seconds.
    The boundary is moved to the nearest detected silence within a search
    window. If no silence is found, the exact target is used.

    A practical guard prevents extremely short or extremely long chunks:
      preferred boundary search: target +/- 25% of target
      hard fallback: exact target
    """
    if target <= 0:
        raise ValueError("Chunk length must be greater than zero.")

    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob("chunk_*.wav"):
        old.unlink()

    total = get_duration(src)
    silences = detect_silences(src, log)
    log(f"Duration: {total/3600:.2f}h; found {len(silences)} silence candidates.")

    boundaries = [0.0]
    current = 0.0
    while current + target < total - 0.05:
        wanted = current + target
        window = max(5.0, target * 0.25)
        candidates = [s for s in silences
                      if current + max(3.0, target*0.5) <= s <= wanted + window]
        if candidates:
            boundary = min(candidates, key=lambda s: abs(s-wanted))
        else:
            boundary = wanted

        # Never allow a boundary to be too close to the previous one.
        if boundary <= current + max(2.0, target*0.25):
            boundary = wanted
        boundaries.append(min(boundary, total))
        current = boundaries[-1]

    if boundaries[-1] < total - 0.05:
        boundaries.append(total)

    # Generate each segment by exact timestamps. Re-encoding to PCM WAV makes
    # boundaries deterministic and keeps every chunk in the same format.
    for i, (a,b) in enumerate(zip(boundaries, boundaries[1:])):
        dst = outdir / f"chunk_{i:06d}_{int(round(a)):010d}-{int(round(b)):010d}.wav"
        log(f"Chunk {i+1}/{len(boundaries)-1}: {a/60:.2f}m -> {b/60:.2f}m")
        run(["ffmpeg","-y","-hide_banner","-loglevel","error",
             "-ss",f"{a:.3f}","-i",str(src),"-t",f"{b-a:.3f}",
             "-ac","1","-ar","16000","-c:a","pcm_s16le",str(dst)])

    return sorted(outdir.glob("chunk_*.wav"), key=natural_key)


def read_transcript(p):
    if p.suffix.lower() == ".txt":
        return p.read_text(encoding="utf8", errors="replace").strip()

    if p.suffix.lower() == ".json":
        d = json.loads(p.read_text(encoding="utf8", errors="replace"))
        if isinstance(d, dict):
            for k in ("text","transcript"):
                if isinstance(d.get(k), str):
                    return d[k].strip()
            if isinstance(d.get("segments"), list):
                return " ".join(
                    str(x.get("text","")).strip()
                    for x in d["segments"]
                    if isinstance(x, dict) and x.get("text")
                ).strip()
        if isinstance(d, list):
            return " ".join(
                (x if isinstance(x,str) else str(x.get("text",""))).strip()
                for x in d if isinstance(x,str) or isinstance(x,dict)
            ).strip()
    return ""


def transcript_key(p):
    # Prefer the first numeric sequence in the filename, which works with
    # chunk_000001... and most Whisper GUI naming schemes.
    nums = re.findall(r"\d+", p.stem)
    return tuple(int(x) for x in nums) if nums else (10**12, natural_key(p))


def merge_transcripts(folders, out, log):
    files = []
    for folder in folders:
        files.extend(p for p in Path(folder).iterdir()
                     if p.is_file() and p.suffix.lower() in {".txt",".json"})
    files = sorted(set(files), key=lambda p: (transcript_key(p), natural_key(p)))

    if not files:
        raise RuntimeError("No .txt or .json transcript files found.")

    texts = []
    for p in files:
        text = read_transcript(p)
        if text:
            texts.append(text)

    if not texts:
        raise RuntimeError("Transcript files were found, but no readable text was found.")

    out.write_text("\n".join(texts) + "\n", encoding="utf8")
    log(f"Merged {len(texts)} transcript files -> {out}")


class App:
    def __init__(self, root):
        self.r = root
        root.title("Audio Pool Compiler v2")
        root.geometry("820x650")
        self.pool = self.master = self.parts = None
        self.transcripts = []
        self.poolv = tk.StringVar(value="No audio pool selected")
        self.masterv = tk.StringVar(value="No master selected")
        self.lengthv = tk.StringVar(value="30")
        self.chunkv = tk.StringVar(value="No chunks created")

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, padx=10, pady=10)

        a,b,c = ttk.Frame(nb),ttk.Frame(nb),ttk.Frame(nb)
        nb.add(a,text="1. Compile + 8h Parts")
        nb.add(b,text="2. Silence-Based Chunks")
        nb.add(c,text="3. Merge Transcripts")

        ttk.Button(a,text="Choose Audio Pool...",command=self.choose_pool).pack(anchor="w",padx=12,pady=(15,5))
        ttk.Label(a,textvariable=self.poolv,wraplength=770).pack(anchor="w",padx=12)
        ttk.Button(a,text="COMPILE POOL → ONE MASTER AUDIO",command=self.compile).pack(anchor="w",padx=12,pady=14)
        ttk.Label(a,textvariable=self.masterv,wraplength=770).pack(anchor="w",padx=12)
        ttk.Button(a,text="SPLIT MASTER → 8-HOUR PARTS",command=self.make_parts).pack(anchor="w",padx=12,pady=14)
        ttk.Label(a,text="The 8-hour split is fixed. The later transcription chunk size is completely configurable.",
                  wraplength=770).pack(anchor="w",padx=12)

        ttk.Label(b,text="Select one or more 8-hour parts:",font=("Arial",12,"bold")).pack(anchor="w",padx=12,pady=(15,5))
        self.listbox=tk.Listbox(b,selectmode=tk.EXTENDED,width=100,height=10)
        self.listbox.pack(fill="x",padx=12)
        ttk.Button(b,text="Refresh 8-Hour Parts",command=self.refresh_parts).pack(anchor="w",padx=12,pady=6)
        row=ttk.Frame(b);row.pack(anchor="w",padx=12,pady=12)
        ttk.Label(row,text="Target chunk length (seconds):").pack(side="left")
        ttk.Entry(row,textvariable=self.lengthv,width=10).pack(side="left",padx=8)
        ttk.Label(row,text="Examples: 30 = 30 sec, 60 = 1 min, 240 = 4 min, 3600 = 1 hour").pack(side="left")
        ttk.Button(b,text="SPLIT SELECTED PARTS AT NEAREST SILENCE",command=self.make_chunks).pack(anchor="w",padx=12,pady=10)
        ttk.Label(b,textvariable=self.chunkv,wraplength=770).pack(anchor="w",padx=12,pady=4)
        ttk.Label(b,text="The target is approximate: each boundary is moved to a nearby silence when one is available. If none is found, the target time is used.",
                  wraplength=770).pack(anchor="w",padx=12,pady=8)

        ttk.Button(c,text="Add Transcript Folder...",command=self.add_transcripts).pack(anchor="w",padx=12,pady=(15,5))
        self.tlist=tk.Listbox(c,width=100,height=8)
        self.tlist.pack(fill="x",padx=12)
        ttk.Button(c,text="Clear Folder List",command=self.clear_transcripts).pack(anchor="w",padx=12,pady=6)
        ttk.Button(c,text="MERGE ALL TRANSCRIPTS → ONE FULL TRANSCRIPT",command=self.merge).pack(anchor="w",padx=12,pady=14)
        ttk.Label(c,text="Add the output folder(s) produced by your Whisper workflow. The app sorts transcript files by their numeric chunk order and joins them.",
                  wraplength=770).pack(anchor="w",padx=12)

        self.log=tk.Text(root,height=10,state="disabled",font=("Consolas",9))
        self.log.pack(fill="x",padx=10,pady=(0,10))

    def L(self,s):
        self.log.config(state="normal");self.log.insert("end",s+"\\n");self.log.see("end");self.log.config(state="disabled");self.r.update_idletasks()

    def choose_pool(self):
        p=filedialog.askdirectory(title="Choose audio pool")
        if p:self.pool=Path(p);self.poolv.set(str(self.pool))

    def compile(self):
        if not self.pool:return messagebox.showwarning("Audio pool","Choose the audio pool first.")
        files=sorted([p for p in self.pool.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS],key=natural_key)
        if not files:return messagebox.showwarning("Audio pool","No supported audio files found.")
        p=filedialog.asksaveasfilename(title="Save master audio",initialfile="MASTER_AUDIO.wav",
                                       defaultextension=".wav",filetypes=[("WAV","*.wav")])
        if not p:return
        self.master=Path(p);self.masterv.set(str(self.master))
        threading.Thread(target=self._compile,args=(files,),daemon=True).start()

    def _compile(self,files):
        try:compile_pool(files,self.master,self.L);self.r.after(0,lambda:messagebox.showinfo("Done","Master audio created."))
        except Exception as e:self.L("ERROR: "+str(e));self.r.after(0,lambda:messagebox.showerror("Compile failed",str(e)))

    def make_parts(self):
        if not self.master or not self.master.exists():
            p=filedialog.askopenfilename(title="Choose master audio",filetypes=[("WAV","*.wav"),("All","*.*")])
            if not p:return
            self.master=Path(p);self.masterv.set(str(self.master))
        p=filedialog.askdirectory(title="Choose output folder for 8-hour parts")
        if not p:return
        self.parts=Path(p)
        threading.Thread(target=self._parts,daemon=True).start()

    def _parts(self):
        try:split_fixed(self.master,self.parts,EIGHT,"part_",self.L);self.refresh_parts()
        except Exception as e:self.L("ERROR: "+str(e));self.r.after(0,lambda:messagebox.showerror("Split failed",str(e)))

    def refresh_parts(self):
        if not self.parts or not self.parts.exists():return
        self.listbox.delete(0,"end")
        for p in sorted(self.parts.glob("part_*.wav"),key=natural_key):self.listbox.insert("end",str(p))

    def make_chunks(self):
        sel=self.listbox.curselection()
        if not sel:return messagebox.showwarning("Choose parts","Select one or more 8-hour parts.")
        try:target=float(self.lengthv.get())
        except: return messagebox.showwarning("Chunk length","Enter a number of seconds, e.g. 30, 60, 240, or 3600.")
        if target<=0:return messagebox.showwarning("Chunk length","Chunk length must be greater than zero.")
        parts=[Path(self.listbox.get(i)) for i in sel]
        parent=filedialog.askdirectory(title="Choose output folder for silence-based chunks")
        if not parent:return
        threading.Thread(target=self._chunks,args=(parts,Path(parent),target),daemon=True).start()

    def _chunks(self,parts,parent,target):
        try:
            total=0
            for part in parts:
                out=parent/part.stem
                total += len(split_at_silence(part,out,target,self.L))
            self.chunkv.set(f"Created {total} chunks in {parent}")
            self.r.after(0,lambda:messagebox.showinfo("Done",f"Created {total} silence-based chunks."))
        except Exception as e:self.L("ERROR: "+str(e));self.r.after(0,lambda:messagebox.showerror("Chunking failed",str(e)))

    def add_transcripts(self):
        p=filedialog.askdirectory(title="Choose a transcript output folder")
        if p and p not in self.transcripts:
            self.transcripts.append(p);self.tlist.insert("end",p)

    def clear_transcripts(self):
        self.transcripts=[];self.tlist.delete(0,"end")

    def merge(self):
        if not self.transcripts:return messagebox.showwarning("Transcript folders","Add at least one transcript folder.")
        p=filedialog.asksaveasfilename(title="Save merged transcript",initialfile="FULL_TRANSCRIPT.txt",
                                       defaultextension=".txt",filetypes=[("Text","*.txt")])
        if not p:return
        threading.Thread(target=self._merge,args=(Path(p),),daemon=True).start()

    def _merge(self,out):
        try:merge_transcripts(self.transcripts,out,self.L);self.r.after(0,lambda:messagebox.showinfo("Done",f"Full transcript created:\\n{out}"))
        except Exception as e:self.L("ERROR: "+str(e));self.r.after(0,lambda:messagebox.showerror("Merge failed",str(e)))


if __name__=="__main__":
    root=tk.Tk();App(root);root.mainloop()
