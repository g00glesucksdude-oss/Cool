#!/usr/bin/env python3

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pypdf import PdfReader, PdfWriter

def process_pdf(input_file, pages_per_chunk=95, reverse=True):
    base_dir = os.path.dirname(input_file)
    odds_dir = os.path.join(base_dir, "odds")
    evens_dir = os.path.join(base_dir, "evens")
    os.makedirs(odds_dir, exist_ok=True)
    os.makedirs(evens_dir, exist_ok=True)

    reader = PdfReader(input_file)
    total_pages = len(reader.pages)

    page_order = list(range(total_pages))
    if reverse:
        page_order.reverse()

    for i in range(0, total_pages, pages_per_chunk):
        chunk = page_order[i:i + pages_per_chunk]
        label = f"{i + 1}-{i + len(chunk)}"

        odd_writer = PdfWriter()
        even_writer = PdfWriter()

        for page_index in chunk:
            page = reader.pages[page_index]
            # Human-readable page number = page_index + 1
            if (page_index + 1) % 2 == 1:
                odd_writer.add_page(page)
            else:
                even_writer.add_page(page)

        if len(odd_writer.pages) > 0:
            with open(os.path.join(odds_dir, f"odds_{label}.pdf"), "wb") as f:
                odd_writer.write(f)

        if len(even_writer.pages) > 0:
            with open(os.path.join(evens_dir, f"evens_{label}.pdf"), "wb") as f:
                even_writer.write(f)

    messagebox.showinfo("Done", f"Processed {total_pages} pages into chunks of {pages_per_chunk}.\nOdds â†’ odds/\nEvens â†’ evens/")

def browse_and_run():
    input_file = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
    if not input_file:
        return
    try:
        pages_per_chunk = int(entry_pages.get())
    except ValueError:
        pages_per_chunk = 95
    threading.Thread(target=process_pdf, args=(input_file, pages_per_chunk, reverse_var.get()), daemon=True).start()

# â”€â”€ GUI â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

root = tk.Tk()
root.title("PDF Odd/Even Splitter")

tk.Label(root, text="Pages per chunk:").grid(row=0, column=0, sticky="w", padx=8, pady=6)
entry_pages = tk.Entry(root, width=10)
entry_pages.insert(0, "95")
entry_pages.grid(row=0, column=1, sticky="w", padx=8)

reverse_var = tk.BooleanVar(value=True)
tk.Checkbutton(root, text="Reverse page order", variable=reverse_var).grid(row=1, column=1, sticky="w", padx=8)

ttk.Separator(root, orient="horizontal").grid(row=2, columnspan=2, sticky="ew", padx=8, pady=8)

tk.Button(root, text="Select PDF & Run", command=browse_and_run, width=20).grid(row=3, column=1, padx=8, pady=8)

root.mainloop()
