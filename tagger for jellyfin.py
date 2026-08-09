import os
import re
import xml.etree.ElementTree as ET
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

TV_PATTERN = re.compile(r'([sS]\d+[eE]\d+|\d+x\d+)')

def process_nfo(nfo_path, root_tag, tag_value, default_title, action):
    tree, root = None, None
    
    if os.path.exists(nfo_path):
        try:
            tree = ET.parse(nfo_path)
            root = tree.getroot()
        except Exception: pass

    if root is None:
        if action == "remove": return
        root = ET.Element(root_tag)
        tree = ET.ElementTree(root)
        ET.SubElement(root, "title").text = default_title

    existing_tags = [t for t in root.findall('tag') if t.text == tag_value]
    
    if action == "add" and not existing_tags:
        ET.SubElement(root, 'tag').text = tag_value
    elif action == "remove" and existing_tags:
        for t in existing_tags: root.remove(t)
        
    if len(root.findall('*')) > 0:
        with open(nfo_path, 'wb') as f:
            f.write(b'<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n')
            tree.write(f, encoding='utf-8', xml_declaration=False)
    elif os.path.exists(nfo_path):
        os.remove(nfo_path)

def start_process(action):
    folder = folder_var.get()
    tag = tag_var.get().strip().lower()
    if not folder or not tag:
        return messagebox.showerror("Error", "Please select a folder and enter a tag name.")
        
    files_processed, folders_tagged = 0, 0
    
    for dirpath, dirnames, filenames in os.walk(folder):
        dir_name_lower = os.path.basename(dirpath).lower()
        
        is_season_folder = "season" in dir_name_lower or "series" in dir_name_lower
        has_tv_files = any(TV_PATTERN.search(f) for f in filenames if not f.lower().endswith('.nfo'))
        is_tv_context = is_season_folder or has_tv_files or "season" in dirpath.lower()

        # 1. Folder Level Metadata (.nfo for the containers)
        if is_season_folder:
            process_nfo(os.path.join(dirpath, "season.nfo"), "season", tag, os.path.basename(dirpath), action)
            folders_tagged += 1
        elif is_tv_context and not has_tv_files:
            process_nfo(os.path.join(dirpath, "tvshow.nfo"), "tvshow", tag, os.path.basename(dirpath), action)
            folders_tagged += 1

        # 2. Process LITERALLY EVERY SINGLE FILE
        for f in filenames:
            # Skip .nfo files so we don't try to make an .nfo file for an .nfo file
            if f.lower().endswith('.nfo'):
                continue
                
            nfo_file = os.path.splitext(os.path.join(dirpath, f))[0] + '.nfo'
            title = os.path.splitext(f)[0]
            
            if is_tv_context or TV_PATTERN.search(f):
                wrapper = "episodedetails"
            else:
                wrapper = "movie"
                
            process_nfo(nfo_file, wrapper, tag, title, action)
            files_processed += 1
                
    messagebox.showinfo("Success", f"Total Lockdown Complete!\nTagged Items: {files_processed}\nTagged Folders: {folders_tagged}\n\nRun 'Replace all metadata' scan in Jellyfin!")

# --- UI Setup ---
root = tk.Tk()
root.title("Absolute Lockdown NFO Generator")
root.geometry("480x200")
root.resizable(False, False)

folder_var, tag_var = tk.StringVar(), tk.StringVar(value="secret")

ttk.Label(root, text="Target Folder:").grid(row=0, column=0, padx=10, pady=10, sticky="w")
ttk.Entry(root, textvariable=folder_var, width=35).grid(row=0, column=1, pady=10)
ttk.Button(root, text="Browse", command=lambda: folder_var.set(filedialog.askdirectory())).grid(row=0, column=2, padx=5, pady=10)

ttk.Label(root, text="Custom Tag:").grid(row=1, column=0, padx=10, pady=10, sticky="w")
ttk.Entry(root, textvariable=tag_var, width=35).grid(row=1, column=1, pady=10, columnspan=2, sticky="w")

btn_frame = ttk.Frame(root)
btn_frame.grid(row=2, column=0, columnspan=3, pady=20)
ttk.Button(btn_frame, text="LOCKDOWN ALL FILES", command=lambda: start_process("add")).pack(side="left", padx=10)
ttk.Button(btn_frame, text="Remove All Restrictions", command=lambda: start_process("remove")).pack(side="left", padx=10)

root.mainloop()