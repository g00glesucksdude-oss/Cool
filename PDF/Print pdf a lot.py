#!/usr/bin/env python3

import os
import tkinter as tk
from tkinter import filedialog, messagebox
from pypdf import PdfReader, PdfWriter

def split_pdf(file_path, chunk_size=95, reverse=True):
    base_dir = os.path.dirname(file_path)

    odds_dir = os.path.join(base_dir, "odds")
    evens_dir = os.path.join(base_dir, "evens")
    os.makedirs(odds_dir, exist_ok=True)
    os.makedirs(evens_dir, exist_ok=True)

    reader = PdfReader(file_path)
    total_pages = len(reader.pages)

    # Page index list
    page_numbers = list(range(total_pages))
    if reverse:
        page_numbers.reverse()

    # Chunk loop
    for start in range(0, total_pages, chunk_size):
        end = min(start + chunk_size, total_pages)
        chunk = page_numbers[start:end]

        odd_writer = PdfWriter()
        even_writer = PdfWriter()

        for p in chunk:
            page = reader.pages[p]

            # Human-readable page number = p+1
            if (p + 1) % 2 == 1:
                odd_writer.add_page(page)
            else:
                even_writer.add_page(page)

        chunk_label = f"{start+1}-{end}"

        odd_path = os.path.join(odds_dir, f"odds_{chunk_label}.pdf")
        even_path = os.path.join(evens_dir, f"evens_{chunk_label}.pdf")

        if len(odd_writer.pages) > 0:
            with open(odd_path, "wb") as f:
                odd_writer.write(f)

        if len(even_writer.pages) > 0:
            with open(even_path, "wb") as f:
                even_writer.write(f)

    messagebox.showinfo("Done", f"Split complete.\nChunks of {chunk_size} pages created.")

def select_pdf():
    file_path = filedialog.askopenfilename(
        title="Select PDF",
        filetypes=[("PDF files", "*.pdf")]
    )
    if not file_path:
        return

    try:
        chunk_size = int(chunk_entry.get())
    except ValueError:
        messagebox.showerror("Error", "Chunk size must be a number.")
        return

    reverse = reverse_var.get()
    split_pdf(file_path, chunk_size, reverse)

# GUI
root = tk.Tk()
root.title("PDF Chunk Splitter")

tk.Label(root, text="Chunk size (default 95):").pack()
chunk_entry = tk.Entry(root)
chunk_entry.insert(0, "95")
chunk_entry.pack()

reverse_var = tk.BooleanVar(value=True)
tk.Checkbutton(root, text="Reverse order", variable=reverse_var).pack()

tk.Button(root, text="Select PDF", command=select_pdf).pack()

root.mainloop()
