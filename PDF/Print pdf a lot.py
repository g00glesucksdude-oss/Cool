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

    # Separate pages into odd and even lists by human-readable page number (1-based)
    # These stay in natural document order first
    odd_pages  = [p for p in range(total_pages) if (p + 1) % 2 == 1]  # pages 1,3,5,...
    even_pages = [p for p in range(total_pages) if (p + 1) % 2 == 0]  # pages 2,4,6,...

    # Apply reversal AFTER separating, so each stream is independently reversed if needed.
    # Default: odd stays normal, even is reversed so when you flip the printed odd stack,
    # the first even lines up with the back of odd page 1.
    if reverse_odd:
        odd_pages.reverse()
    if reverse_even:
        even_pages.reverse()

    # Chunk each stream independently by pages_per_chunk
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

        # Label by the 1-based position in each stream (e.g. stream positions 1-95, 96-190)
        # This tells you which chunk number it is, not which document page numbers
        label_start = start + 1
        label_end   = start + max(len(odd_chunk), len(even_chunk))
        label = f"{label_start}-{label_end}"

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
        f"Odds â†’ odds/\nEvens â†’ evens/",
    )


def show_reverse_menu():
    popup = tk.Toplevel(root)
    popup.title("Reverse Options")
    popup.resizable(False, False)
    popup.grab_set()

    tk.Label(popup, text="Which pages should be reversed?", font=("", 10, "bold")).grid(
        row=0, column=0, columnspan=2, padx=16, pady=(14, 8)
    )
    tk.Label(
        popup,
        text="Default: odds normal, evens reversed.\n"
             "This ensures evens line up correctly\n"
             "when you flip your printed odd stack.",
        fg="gray", justify="left"
    ).grid(row=1, column=0, columnspan=2, sticky="w", padx=20, pady=(0, 8))

    tk.Checkbutton(popup, text="Reverse Odd pages",  variable=reverse_odd_var).grid(
        row=2, column=0, columnspan=2, sticky="w", padx=20, pady=3
    )
    tk.Checkbutton(popup, text="Reverse Even pages", variable=reverse_even_var).grid(
        row=3, column=0, columnspan=2, sticky="w", padx=20, pady=3
    )

    def toggle_both():
        both = reverse_odd_var.get() and reverse_even_var.get()
        reverse_odd_var.set(not both)
        reverse_even_var.set(not both)

    tk.Button(popup, text="Toggle Both", command=toggle_both, width=14).grid(
        row=4, column=0, padx=12, pady=(8, 14)
    )
    tk.Button(popup, text="OK", command=popup.destroy, width=10).grid(
        row=4, column=1, padx=12, pady=(8, 14)
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


# â”€â”€ GUI â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

tk.Button(root, text="âš™ Reverse Optionsâ€¦", command=show_reverse_menu, width=20).grid(
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
