import os
import re
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox

def fix_particle_line(line, old_p=None, new_p=None, scale=1.0):
    """Strictly: particle name x y z. No trash. Preserves negative math."""
    if "particle " not in line:
        return line

    if old_p and new_p:
        line = line.replace(f"minecraft:{old_p}", new_p)
        line = line.replace(f" {old_p} ", f" {new_p} ")

    parts = line.split()
    try:
        idx = parts.index("particle")

        # Namespace
        if len(parts) > idx + 1 and ":" not in parts[idx + 1]:
            parts[idx + 1] = f"minecraft:{parts[idx + 1]}"

        # Scale X, Y, Z
        for i in range(idx + 2, idx + 5):
            if i < len(parts):
                val = parts[i]
                prefix = ""
                if val.startswith("~") or val.startswith("^"):
                    prefix = val[0]
                    num_part = val[1:]
                else:
                    num_part = val

                try:
                    num = float(num_part) if num_part not in ["", "-", "^", "~"] else 0.0
                    result = round(num * scale, 6)
                    parts[i] = f"{prefix}{result:g}"
                except ValueError:
                    pass

        # Strict Trim: particle <name> <x> <y> <z>
        return " ".join(parts[:idx + 5]) + "\n"
    except (ValueError, IndexError):
        return line

def get_file_content(base_path, func_path, p_word, old_p, new_p, scale, iterations):
    """Extracts lines from sub-functions for merging into the parent condition."""
    clean_rel_path = func_path.strip().replace(":", "/").lstrip('/')
    full_path = os.path.join(base_path, f"{clean_rel_path}.mcfunction")
    if os.path.exists(full_path):
        with open(full_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            processed = []
            for l in lines:
                if not l.strip() or l.startswith("#"): continue 
                l = l.replace("cw_particleplot", p_word)
                fixed_l = fix_particle_line(l, old_p, new_p, scale)
                for _ in range(iterations):
                    processed.append(fixed_l)
            return processed, full_path
    return None, None

def full_converter():
    """Merges sub-functions, renames files, and cleans particles."""
    root = tk.Tk(); root.withdraw()
    folder_path = filedialog.askdirectory(title="Select Project Folder")
    if not folder_path: return

    new_animate = input("New name for 'animate': ")
    new_logic = input("New name for 'l1_0': ")
    p_word = input("Replace 'cw_particleplot' with: ")
    scale = float(input("Scale: ") or 1.0)
    iterations = int(input("Density (iterations): ") or 1)
    old_p = input("Old Particle: ").strip()
    new_p = input("New Particle: ").strip()
    if new_p and ":" not in new_p: new_p = f"minecraft:{new_p}"

    files_to_delete = set()
    animate_old, logic_old = None, None

    for root_dir, _, files in os.walk(folder_path):
        for filename in files:
            if not filename.endswith(".mcfunction"): continue
            file_path = os.path.join(root_dir, filename)
            if filename == "animate.mcfunction": animate_old = file_path
            if filename == "l1_0.mcfunction": logic_old = file_path

            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            new_lines = []
            for line in lines:
                if not line.strip(): continue
                line = line.replace("cw_particleplot", p_word)
                line = line.replace("animate", new_animate).replace("demo/l1/l1_0", new_logic)

                # Merge Logic
                match = re.search(r"(.*?run\s+)function\s+([\w\d/_.:-]+)", line)
                if match:
                    prefix_condition = match.group(1)
                    sub_path = match.group(2)
                    if "demo/l0" in sub_path or "demo/frames" in sub_path:
                        child_lines, child_full = get_file_content(folder_path, sub_path, p_word, old_p, new_p, scale, iterations)
                        if child_lines:
                            for c_line in child_lines:
                                new_lines.append(f"{prefix_condition}run {c_line.strip()}\n")
                            files_to_delete.add(child_full)
                            continue 

                fixed_line = fix_particle_line(line, old_p, new_p, scale)
                reps = iterations if "particle " in line else 1
                for _ in range(reps):
                    new_lines.append(fixed_line)

            with open(file_path, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)

    if animate_old: shutil.move(animate_old, os.path.join(folder_path, f"{new_animate}.mcfunction"))
    if logic_old: shutil.move(logic_old, os.path.join(folder_path, f"{new_logic}.mcfunction"))
    for f in files_to_delete: 
        if os.path.exists(f): os.remove(f)
    demo_dir = os.path.join(folder_path, "demo")
    if os.path.exists(demo_dir): shutil.rmtree(demo_dir)
    print("Full Conversion Complete.")

def cleaner_only():
    """Option 2: Strictly cleans one file and adds density."""
    root = tk.Tk(); root.withdraw()
    file_path = filedialog.askopenfilename(filetypes=[("MCFunction", "*.mcfunction")])
    if not file_path: return

    scale = float(input("Scale: ") or 1.0)
    density = int(input("Density (iterations per line): ") or 1)
    old_p = input("Old Particle: ").strip()
    new_p = input("New Particle: ").strip()

    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    new_lines = []
    for line in lines:
        if not line.strip(): continue
        fixed = fix_particle_line(line, old_p, new_p, scale)
        
        # Apply density (only to particle lines)
        reps = density if "particle " in line else 1
        for _ in range(reps):
            new_lines.append(fixed)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    print(f"File Cleaned. Density of {density} applied.")

def main():
    print("--- Particle Logic Fixer ---")
    print("1 = Full (Merge, Rename, Scale, Clean)")
    print("2 = Clean (Single file, Scale, Density, Clean)")
    choice = input("Choice: ").strip()
    if choice == "1":
        full_converter()
    elif choice == "2":
        cleaner_only()

if __name__ == "__main__":
    main()
