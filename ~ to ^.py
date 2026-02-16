import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
import re

def convert_mcfunction():
    file_path = filedialog.askopenfilename(
        title="Select a .mcfunction file",
        filetypes=[("MCFunction files", "*.mcfunction")]
    )
    if not file_path: return

    mode = simpledialog.askstring("Mode", "Type '^' for ~→^ or '~' for ^→~")
    if mode not in ("^","~"): return

    with open(file_path,"r",encoding="utf-8") as f: lines=f.readlines()
    converted=[]
    for line in lines:
        orig=line.strip()
        line=line.replace("~","^") if mode=="^" else line.replace("^","~")
        line=re.sub(r"\s*minecraft:air\s*$","",line)
        parts=line.strip().split()
        if orig.startswith("fill "):
            coords=parts[1:7]; line="fill "+" ".join(coords)+" destroy\n"
        elif orig.startswith("setblock "):
            coords=parts[1:4]; line="fill "+" ".join(coords)+" "+" ".join(coords)+" destroy\n"
        converted.append(line)

    with open(file_path,"w",encoding="utf-8") as f: f.writelines(converted)
    messagebox.showinfo("Done","Conversion complete.")

if __name__=="__main__": convert_mcfunction()
