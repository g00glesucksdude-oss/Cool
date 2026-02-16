import tkinter as tk
from tkinter import filedialog, messagebox
import random
import string
import os

def obfuscate_lookup_only(input_path):
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # Lookup table for obfuscation
        charset = string.ascii_letters + string.digits + " .:/\\-_@\""
        shuffled = list(charset)
        random.shuffle(shuffled)
        key_string = "".join(shuffled)
        key_var = "".join(random.choices(string.ascii_uppercase, k=6))

        output_lines = [
            "@echo off",
            f"set {key_var}={key_string}",
            "cls",
            "setlocal enabledelayedexpansion"
        ]

        for i, line in enumerate(lines, 1):
            line = line.strip()
            if not line: 
                continue

            # Encode each character using lookup table
            encoded_line = ""
            for char in line:
                if char in key_string:
                    idx = key_string.index(char)
                    encoded_line += f"%{key_var}:~{idx},1%"
                else:
                    encoded_line += char

            # Store encoded payload in variable
            output_lines.append(f"set encoded{i}={encoded_line}")

            # Decoder routine (just expands lookup references)
            output_lines.append("set final{i}=!encoded{i}!")
            output_lines.append(f"call !encoded{i}!")

        output_path = input_path.replace(".bat", "_lookupobf.bat")
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(output_lines))

        return output_path

    except Exception as e:
        return str(e)

def run_gui():
    root = tk.Tk()
    root.withdraw()
    file_path = filedialog.askopenfilename(title="Select Batch File", filetypes=[("Batch Files", "*.bat")])
    if file_path:
        res = obfuscate_lookup_only(file_path)
        messagebox.showinfo("Success", f"File created:\n{res}")

if __name__ == "__main__":
    run_gui()
