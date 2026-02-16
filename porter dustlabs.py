import tkinter as tk
from tkinter import filedialog

def convert_file(userinput: str):
    # Open file selection dialog
    root = tk.Tk()
    root.withdraw()
    filepath = filedialog.askopenfilename(
        title="Select a .mcfunction file",
        filetypes=[("MCFunction files", "*.mcfunction")]
    )
    if not filepath:
        print("No file selected.")
        return

    # Read and process lines
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    new_lines = []
    for line in lines:
        if line.strip().startswith("particle minecraft:dust"):
            # Split by spaces and keep only coords
            parts = line.strip().split()
            # coords are parts[2:5] (x, y, z)
            coords = " ".join(parts[2:5])
            new_line = f"particle minecraft:{userinput} {coords}\n"
            new_lines.append(new_line)
        else:
            new_lines.append(line)

    # Save back to file (or create new one)
    outpath = filepath.replace(".mcfunction", "_converted.mcfunction")
    with open(outpath, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print(f"Conversion complete. Saved as {outpath}")

if __name__ == "__main__":
    userinput = input("Enter particle type (e.g. flame, smoke): ").strip()
    convert_file(userinput)
