import os
import tkinter as tk
from tkinter import filedialog, messagebox

def open_file(file_path):
    """Open file with the system default editor."""
    import subprocess, platform
    try:
        if platform.system() == "Windows":
            os.startfile(file_path)
        elif platform.system() == "Darwin":  # macOS
            subprocess.call(["open", file_path])
        else:  # Linux
            subprocess.call(["xdg-open", file_path])
    except Exception as e:
        messagebox.showerror("Error", f"Could not open file: {e}")

def search_folder():
    folder_path = filedialog.askdirectory(title="Select a folder to search")
    if not folder_path:
        return

    words = word_entry.get().strip().split(",")
    if not words or words == [""]:
        messagebox.showerror("Error", "Please enter at least one word to search.")
        return

    # Clear previous results
    for widget in results_frame.winfo_children():
        widget.destroy()

    try:
        row = 0
        for root, _, files in os.walk(folder_path):
            for file in files:
                # Scan JSON, LANG, MCFUNCTION, TXT files
                if file.endswith((".json", ".lang", ".mcfunction", ".txt")):
                    file_path = os.path.join(root, file)
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()

                    for i, line in enumerate(lines, start=1):
                        for word in words:
                            if word.strip() and word.strip().lower() in line.lower():
                                lbl = tk.Label(
                                    results_frame,
                                    text=f"{file_path} (Line {i}): '{word.strip()}' → {line.strip()}",
                                    anchor="w",
                                    justify="left",
                                    wraplength=700
                                )
                                lbl.grid(row=row, column=0, sticky="w", padx=5, pady=2)

                                btn = tk.Button(
                                    results_frame,
                                    text="Open File",
                                    command=lambda fp=file_path: open_file(fp)
                                )
                                btn.grid(row=row, column=1, padx=5, pady=2)

                                row += 1

        if row == 0:
            tk.Label(results_frame, text="No matches found.").grid(row=0, column=0, padx=5, pady=5)

    except Exception as e:
        messagebox.showerror("Error", f"Could not read files: {e}")

# === GUI setup ===
root = tk.Tk()
root.title("Folder Search (JSON, LANG, MCFUNCTION, TXT)")

tk.Label(root, text="Enter words to search (comma separated):").pack(pady=5)
word_entry = tk.Entry(root, width=50)
word_entry.pack(pady=5)

tk.Button(root, text="Select Folder and Search", command=search_folder).pack(pady=10)

# === Scrollable results frame ===
canvas = tk.Canvas(root, height=400, width=800)
scrollbar = tk.Scrollbar(root, orient="vertical", command=canvas.yview)
scrollable_frame = tk.Frame(canvas)

scrollable_frame.bind(
    "<Configure>",
    lambda e: canvas.configure(
        scrollregion=canvas.bbox("all")
    )
)

results_frame = scrollable_frame  # use this for results

canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
canvas.configure(yscrollcommand=scrollbar.set)

canvas.pack(side="left", fill="both", expand=True)
scrollbar.pack(side="right", fill="y")

root.mainloop()
