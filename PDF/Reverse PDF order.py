import os
from tkinter import Tk, filedialog, messagebox
from pypdf import PdfReader, PdfWriter

def reverse_pdfs():
    # Initialize and hide the main Tkinter root window
    root = Tk()
    root.withdraw()
    root.update() # Ensures the hidden state is processed by the OS

    # Logic: Open file dialog for multiple selection
    files = filedialog.askopenfilenames(
        title="Select PDF files to reverse",
        filetypes=[("PDF files", "*.pdf")]
    )

    if not files:
        root.destroy()
        return

    success_count = 0

    for file_path in files:
        try:
            reader = PdfReader(file_path)
            writer = PdfWriter()

            # Logic: Use the built-in reversed iterator for cleaner code
            for page in reversed(reader.pages):
                writer.add_page(page)

            # Output naming: file.pdf -> file_reversed.pdf
            base, ext = os.path.splitext(file_path)
            output_path = f"{base}_reversed{ext}"

            # Write the file
            with open(output_path, "wb") as output_file:
                writer.write(output_file)
            
            success_count += 1
            print(f"Reversed: {os.path.basename(output_path)}")

        except Exception as e:
            print(f"Error processing {file_path}: {e}")

    # Final logic confirmation
    if success_count > 0:
        messagebox.showinfo("Success", f"Successfully reversed {success_count} files.")
    
    root.destroy()

if __name__ == "__main__":
    reverse_pdfs()
