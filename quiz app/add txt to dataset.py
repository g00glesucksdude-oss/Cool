import os
import json
import random
import re
import shutil
import tkinter as tk
from tkinter import filedialog

def extract_sentences(text):
    """Split text into sentences using regex (no spaCy needed)."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in sentences if s.strip()]

def build_mc_question(question, correct_answer, wrong_options, context="", source_file="", snippet=""):
    options = wrong_options + [correct_answer]
    random.shuffle(options)
    return {
        "type": "multiple_choice",
        "question": question,
        "options": options,
        "answer": correct_answer,
        "context": context,
        "source_file": source_file,
        "source_snippet": snippet,
        "explanation": f"{context} The correct answer is: {correct_answer}"
    }

def build_dataset(sentences, full_text, source_file):
    entries = []
    for i, s in enumerate(sentences):
        context = ""
        if i > 0: context += sentences[i-1] + " "
        if i+1 < len(sentences): context += sentences[i+1]

        # Flashcard
        entries.append({
            "type": "flashcard",
            "statement": s,
            "answer": "True" if s.lower() in full_text.lower() else "False",
            "context": context,
            "source_file": source_file,
            "source_snippet": s,
            "explanation": s
        })

        # Type-answer: detect "X is Y"
        match = re.match(r"(.+?) is (.+)", s)
        if match:
            subject, predicate = match.groups()
            question = f"What is {subject.strip()}?"
            answer = predicate.strip().rstrip(".")

            entries.append({
                "type": "type_answer",
                "question": question,
                "expected_answer": answer,
                "context": context,
                "source_file": source_file,
                "source_snippet": s,
                "explanation": f"{context} {s}"
            })

            wrongs = [
                "different from domestic law",
                "based on external treaties",
                "unknown unless proven"
            ]
            mc_entry = build_mc_question(
                f"{subject.strip()} is ...?",
                answer,
                wrongs,
                context=context,
                source_file=source_file,
                snippet=s
            )
            entries.append(mc_entry)

    return {"entries": entries}

def main():
    root = tk.Tk()
    root.withdraw()
    file_path = filedialog.askopenfilename(
        title="Select a text file",
        filetypes=[("Text files", "*.txt")]
    )
    if not file_path:
        print("❌ No file selected.")
        return

    with open(file_path, "r", encoding="utf-8") as f:
        text = f.read()

    sentences = extract_sentences(text)
    source_file = os.path.basename(file_path)
    dataset = build_dataset(sentences, text, source_file)

    dataset_dir = os.path.join(os.getcwd(), "datasets")
    os.makedirs(dataset_dir, exist_ok=True)
    base_name = os.path.splitext(source_file)[0]
    out_file = os.path.join(dataset_dir, f"{base_name}_dataset.json")

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(dataset, f, indent=2)

    # Copy original source file into datasets/
    shutil.copy(file_path, dataset_dir)

    print(f"📚 Dataset saved to {out_file}")
    print(f"📄 Source file copied to {dataset_dir}/{source_file}")

if __name__ == "__main__":
    main()
