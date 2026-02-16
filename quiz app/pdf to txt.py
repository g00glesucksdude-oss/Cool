import tkinter as tk
from tkinter import filedialog, messagebox
import PyPDF2
import os

def pdf_to_text(pdf_path, txt_path):
    with open(pdf_path, 'rb') as pdf_file:
        reader = PyPDF2.PdfReader(pdf_file)
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    with open(txt_path, 'w', encoding='utf-8') as txt_file:
        txt_file.write(text)

def select_pdf():
    pdf_path = filedialog.askopenfilename(
        title="Select PDF file",
        filetypes=[("PDF files", "*.pdf")]
    )
    if pdf_path:
        # Save text file in same folder with same name
        txt_path = os.path.splitext(pdf_path)[0] + ".txt"
        try:
            pdf_to_text(pdf_path, txt_path)
            messagebox.showinfo("Success", f"Text saved to:\n{txt_path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to convert PDF:\n{e}")

# Tkinter window setup
root = tk.Tk()
root.title("PDF to Text Converter")

label = tk.Label(root, text="Select a PDF file to convert:")
label.pack(pady=10)

button = tk.Button(root, text="Browse PDF", command=select_pdf)
button.pack(pady=5)

root.mainloop()
