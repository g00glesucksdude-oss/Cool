#!/usr/bin/env python3
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PyPDF2 import PdfReader, PdfWriter


def split_pdf(file_path, chunk_size=95, reverse=True, status_callback=None):
    base_dir = os.path.dirname(file_path)
    odds_dir = os.path.join(base_dir, "odds")
    evens_dir = os.path.join(base_dir, "evens")
    os.makedirs(odds_dir, exist_ok=True)
    os.makedirs(evens_dir, exist_ok=True)

    reader = PdfReader(file_path)
    total_pages = len(reader.pages)

    # Reverse page order if enabled (so stacking works correctly)
    page_numbers = list(range(total_pages))
    if reverse:
        page_numbers = page_numbers[::-1]

    chunks_created = 0

    for start in range(0, total_pages, chunk_size):
        end = min(start + chunk_size, total_pages)
        chunk_pages = page_numbers[start:end]

        odd_writer = PdfWriter()
        even_writer = PdfWriter()

        for p in chunk_pages:
            if (p + 1) % 2 == 1:  # odd page (1-indexed)
                odd_writer.add_page(reader.pages[p])
            else:
                even_writer.add_page(reader.pages[p])

        # Label by original page range for easy printing order reference
        chunk_id = f"{start + 1}-{end}"
        odd_path = os.path.join(odds_dir, f"odds_{chunk_id}.pdf")
        even_path = os.path.join(evens_dir, f"evens_{chunk_id}.pdf")

        if len(odd_writer.pages) > 0:
            with open(odd_path, "wb") as f:
                odd_writer.write(f)

        if len(even_writer.pages) > 0:
            with open(even_path, "wb") as f:
                even_writer.write(f)

        chunks_created += 1
        if status_callback:
            status_callback(f"Processed chunk {chunk_id} ({end}/{total_pages} pages)...")

    return total_pages, chunks_created


def select_and_split():
    file_path = filedialog.askopenfilename(
        title="Select PDF to Split",
        filetypes=[("PDF files", "*.pdf")]
    )
    if not file_path:
        return

    try:
        chunk_size = int(chunk_entry.get())
        if chunk_size < 1:
            raise ValueError
    except ValueError:
        messagebox.showerror("Invalid Input", "Chunk size must be a positive whole number.")
        return

    reverse = reverse_var.get()

    btn.config(state="disabled", text="Processing...")
    status_var.set("Starting...")
    root.update()

    try:
        def update_status(msg):
            status_var.set(msg)
            root.update()

        total_pages, chunks = split_pdf(file_path, chunk_size, reverse, update_status)

        status_var.set(f"Done! {total_pages} pages split into {chunks} chunk(s).")
        messagebox.showinfo(
            "Done",
            f"Split complete!\n\n"
            f"• {total_pages} total pages\n"
            f"• {chunks} chunk(s) of up to {chunk_size} pages\n"
            f"• Odds saved to: odds/\n"
            f"• Evens saved to: evens/\n\n"
            f"Print order: odds_1-{chunk_size} → evens_1-{chunk_size} → stack, repeat for next chunk."
        )
    except Exception as e:
        status_var.set("Error occurred.")
        messagebox.showerror("Error", f"Something went wrong:\n{e}")
    finally:
        btn.config(state="normal", text="Select PDF & Split")


# ── GUI ────────────────────────────────────────────────────────────────────────
root = tk.Tk()
root.title("PDF Odd/Even Splitter")
root.resizable(False, False)

pad = {"padx": 16, "pady": 6}

tk.Label(root, text="PDF Odd/Even Splitter", font=("Helvetica", 14, "bold")).pack(**pad, pady=(16, 4))
tk.Label(root, text="Splits a PDF into odd & even pages per chunk so\nyou can print and stack without jamming your printer.",
         justify="center", fg="#555").pack(padx=16, pady=(0, 10))

ttk.Separator(root, orient="horizontal").pack(fill="x", padx=16, pady=4)

# Chunk size
frame_chunk = tk.Frame(root)
frame_chunk.pack(**pad)
tk.Label(frame_chunk, text="Pages per chunk:").pack(side="left")
chunk_entry = tk.Entry(frame_chunk, width=6, justify="center")
chunk_entry.insert(0, "95")
chunk_entry.pack(side="left", padx=(8, 0))

# Reverse toggle
reverse_var = tk.BooleanVar(value=True)
tk.Checkbutton(root, text="Reverse page order (recommended for stacking)", variable=reverse_var).pack(**pad)

ttk.Separator(root, orient="horizontal").pack(fill="x", padx=16, pady=4)

# Action button
btn = tk.Button(root, text="Select PDF & Split", command=select_and_split,
                bg="#2563eb", fg="white", font=("Helvetica", 11, "bold"),
                relief="flat", padx=12, pady=8, cursor="hand2")
btn.pack(**pad, pady=(8, 4))

# Status label
status_var = tk.StringVar(value="Ready.")
tk.Label(root, textvariable=status_var, fg="#777", font=("Helvetica", 9)).pack(padx=16, pady=(0, 14))

root.mainloop()
