i want it so instead of 95 pages and then split to ev#!/usr/bin/env python3
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

    # Separate into odd and even page indices (based on human-readable page number)
    odd_pages  = [p for p in page_order if (p + 1) % 2 == 1]
    even_pages = [p for p in page_order if (p + 1) % 2 == 0]

    # How many chunks are needed (driven by whichever list is longer)
    num_chunks = max(
        (len(odd_pages)  + pages_per_chunk - 1) // pages_per_chunk,
        (len(even_pages) + pages_per_chunk - 1) // pages_per_chunk,
        1,
    )

    for chunk_idx in range(num_chunks):
        start = chunk_idx * pages_per_chunk
        end   = start + pages_per_chunk

        odd_chunk  = odd_pages[start:end]
        even_chunk = even_pages[start:end]

        # Label by source-page range covered in this chunk
        all_in_chunk = odd_chunk + even_chunk
        if all_in_chunk:
            # Use original (0-based) indices + 1 for human-readable label
            lo = min(all_in_chunk) + 1
            hi = max(all_in_chunk) + 1
            label = f"{lo}-{hi}"
        else:
            label = f"{start + 1}-{end}"

        if odd_chunk:
            odd_writer = PdfWriter()
            for page_index in odd_chunk:
                odd_writer.add_page(reader.pages[page_index])
            with open(os.path.join(odds_dir, f"odds_{label}.pdf"), "wb") as f:
                odd_writer.write(f)

        if even_chunk:
            even_writer = PdfWriter()
            for page_index in even_chunk:
                even_writer.add_page(reader.pages[page_index])
            with open(os.path.join(evens_dir, f"evens_{label}.pdf"), "wb") as f:
                even_writer.write(f)

    messagebox.showinfo(
        "Done",
        f"Processed {total_pages} pages into {num_chunks} chunk(s) of up to {pages_per_chunk} odds + {pages_per_chunk} evens.\n"
        f"Odds → odds/\nEvens → evens/",
    )


def browse_and_run():
    input_file = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
    if not input_file:
        return
    try:
        pages_per_chunk = int(entry_pages.get())
    except ValueError:
        pages_per_chunk = 95
    threading.Thread(
        target=process_pdf,
        args=(input_file, pages_per_chunk, reverse_var.get()),
        daemon=True,
    ).start()


# ── GUI ────────────────────────────────────────────────────────────────────────
root = tk.Tk()
root.title("PDF Odd/Even Splitter")

tk.Label(root, text="Pages per chunk\n(odds + evens separately):").grid(
    row=0, column=0, sticky="w", padx=8, pady=6
)
entry_pages = tk.Entry(root, width=10)
entry_pages.insert(0, "95")
entry_pages.grid(row=0, column=1, sticky="w", padx=8)

reverse_var = tk.BooleanVar(value=True)
tk.Checkbutton(root, text="Reverse page order", variable=reverse_var).grid(
    row=1, column=1, sticky="w", padx=8
)

ttk.Separator(root, orient="horizontal").grid(
    row=2, columnspan=2, sticky="ew", padx=8, pady=8
)

tk.Button(root, text="Select PDF & Run", command=browse_and_run, width=20).grid(
    row=3, column=1, padx=8, pady=8
)

root.mainloop()
