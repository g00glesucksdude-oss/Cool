import os
from tkinter import Tk, filedialog, messagebox
from pypdf import PdfReader, PdfWriter

def reverse_pdfs():
    # Hide the main Tkinter root window
    root = Tk()
    root.withdraw()

    # Logic: Open file dialog for multiple selection
    files = filedialog.askopenfilenames(
        title="Select PDF files to reverse",
        filetypes=[("PDF files", "*.pdf")]
    )

    if not files:
        print("No files selected.")
        return

    success_count = 0

    for file_path in files:
        try:
            reader = PdfReader(file_path)
            writer = PdfWriter()

            # Reverse page logic: iterate N-1 down to 0
            for i in range(len(reader.pages) - 1, -1, -1):
                writer.add_page(reader.pages[i])

            # Output naming: file.pdf -> file_reversed.pdf
            base, ext = os.path.splitext(file_path)
            output_path = f"{base}_reversed{ext}"

            with open(output_path, "wb") as f:
                writer.write(f)
            
            success_count += 1
            print(f"Reversed: {os.path.basename(output_path)}")

        except Exception as e:
            print(f"Error processing {file_path}: {e}")

    # Final logic confirmation
    messagebox.showinfo("Done", f"Successfully reversed {success_count} files.")
    root.destroy()

if __name__ == "__main__":
    reverse_pdfs()
