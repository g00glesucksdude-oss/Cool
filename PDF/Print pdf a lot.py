#!/usr/bin/env python3
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pypdf import PdfReader, PdfWriter


def process_pdf(input_file, pages_per_chunk=95, reverse_odd=False, reverse_even=True):
    base_dir = os.path.dirname(input_file)
    odds_dir = os.path.join(base_dir, "odds")
    evens_dir = os.path.join(base_dir, "evens")
    os.makedirs(odds_dir, exist_ok=True)
    os.makedirs(evens_dir, exist_ok=True)

    reader = PdfReader(input_file)
    total_pages = len(reader.pages)

    all_pages = list(range(total_pages))

    # Separate into odd and even by human-readable page number
    odd_pages  = [p for p in all_pages if (p + 1) % 2 == 1]
    even_pages = [p for p in all_pages if (p + 1) % 2 == 0]

    if reverse_odd:
        odd_pages.reverse()
    if reverse_even:
        even_pages.reverse()

    # How many chunks needed
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

        all_in_chunk = odd_chunk + even_chunk
        if all_in_chunk:
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

    rev_desc = []
    if reverse_odd:
        rev_desc.append("odds reversed")
    if reverse_even:
        rev_desc.append("evens reversed")
    rev_str = ", ".join(rev_desc) if rev_desc else "no reversal"

    messagebox.showinfo(
        "Done",
        f"Processed {total_pages} pages into {num_chunks} chunk(s) of up to "
        f"{pages_per_chunk} odds + {pages_per_chunk} evens.\n"
        f"Order: {rev_str}\n"
        f"Odds → odds/\nEvens → evens/",
    )


def show_reverse_menu():
    """Show a small popup to choose which streams to reverse."""
    popup = tk.Toplevel(root)
    popup.title("Reverse Options")
    popup.resizable(False, False)
    popup.grab_set()  # modal

    tk.Label(popup, text="Which pages should be reversed?", font=("", 10, "bold")).grid(
        row=0, column=0, columnspan=2, padx=16, pady=(14, 8)
    )

    tk.Checkbutton(popup, text="Reverse Odd pages",  variable=reverse_odd_var).grid(
        row=1, column=0, columnspan=2, sticky="w", padx=20, pady=3
    )
    tk.Checkbutton(popup, text="Reverse Even pages", variable=reverse_even_var).grid(
        row=2, column=0, columnspan=2, sticky="w", padx=20, pady=3
    )

    def toggle_both():
        # If both are on, turn both off; otherwise turn both on
        both = reverse_odd_var.get() and reverse_even_var.get()
        reverse_odd_var.set(not both)
        reverse_even_var.set(not both)

    tk.Button(popup, text="Toggle Both", command=toggle_both, width=14).grid(
        row=3, column=0, padx=12, pady=(8, 14)
    )
    tk.Button(popup, text="OK", command=popup.destroy, width=10).grid(
        row=3, column=1, padx=12, pady=(8, 14)
    )

    popup.wait_window()
    update_reverse_label()


def update_reverse_label(*_):
    parts = []
    if reverse_odd_var.get():
        parts.append("odds")
    if reverse_even_var.get():
        parts.append("evens")
    lbl_reverse_status.config(text=f"Reversed: {', '.join(parts) if parts else 'none'}")


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
        args=(input_file, pages_per_chunk, reverse_odd_var.get(), reverse_even_var.get()),
        daemon=True,
    ).start()


# ── GUI ────────────────────────────────────────────────────────────────────────
root = tk.Tk()
root.title("PDF Odd/Even Splitter")

# Default: odd = normal (False), even = reversed (True)
reverse_odd_var  = tk.BooleanVar(value=False)
reverse_even_var = tk.BooleanVar(value=True)

tk.Label(root, text="Pages per chunk\n(odds + evens separately):").grid(
    row=0, column=0, sticky="w", padx=8, pady=6
)
entry_pages = tk.Entry(root, width=10)
entry_pages.insert(0, "95")
entry_pages.grid(row=0, column=1, sticky="w", padx=8)

tk.Button(root, text="⚙ Reverse Options…", command=show_reverse_menu, width=20).grid(
    row=1, column=0, sticky="w", padx=8, pady=(4, 0)
)
lbl_reverse_status = tk.Label(root, text="Reversed: evens", fg="gray")
lbl_reverse_status.grid(row=1, column=1, sticky="w", padx=8)

ttk.Separator(root, orient="horizontal").grid(
    row=2, columnspan=2, sticky="ew", padx=8, pady=8
)

tk.Button(root, text="Select PDF & Run", command=browse_and_run, width=20).grid(
    row=3, column=1, padx=8, pady=8
)

root.mainloop()
