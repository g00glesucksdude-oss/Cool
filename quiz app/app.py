import os
import json
import sqlite3
import numpy as np
import torch
from datetime import datetime, date
from flask import Flask, render_template, request, redirect, url_for
from transformers import AutoTokenizer, AutoModel

app = Flask(__name__)

print("Loading Qwen3-Embedding-0.6B...")
tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen3-Embedding-0.6B")
model = AutoModel.from_pretrained("Qwen/Qwen3-Embedding-0.6B")

DB_FILE = "quiz_progress.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS progress (
        dataset TEXT,
        qid TEXT,
        last_answered DATE,
        correct INTEGER
    )
    """)
    conn.commit()
    conn.close()

init_db()

def get_embedding(text: str):
    inputs = tokenizer(text, return_tensors="pt")
    with torch.no_grad():
        outputs = model(**inputs)
        embeddings = outputs.last_hidden_state.mean(dim=1)
    return embeddings[0].to(torch.float32).numpy()

def cosine_similarity(vec1, vec2):
    return np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2))

def normalize(text):
    return text.strip().lower()

def verify_answer(entry, user_answer, threshold=0.8):
    etype = entry.get("type")
    if etype == "multiple_choice":
        correct = entry.get("answer", "").strip().lower()
        return user_answer.strip().lower() == correct
    elif etype == "flashcard":
        return user_answer.strip().lower() == entry.get("answer", "").strip().lower()
    elif etype == "type_answer":
        expected = entry.get("expected_answer", "")
        explanation = entry.get("explanation", "")
        if not expected and not explanation:
            return False
        emb_user = get_embedding(user_answer)
        if expected:
            emb_expected = get_embedding(expected)
            if cosine_similarity(emb_expected, emb_user) >= threshold:
                return True
        if explanation:
            emb_expl = get_embedding(explanation)
            if cosine_similarity(emb_expl, emb_user) >= threshold:
                return True
        return False
    return False

def load_dataset(dataset_file):
    with open(os.path.join("datasets", dataset_file), "r") as f:
        data = json.load(f)
    if isinstance(data, dict) and "entries" in data:
        return data["entries"]
    elif isinstance(data, list):
        return data
    else:
        raise ValueError("Unsupported dataset format")

def record_progress(dataset, qid, correct):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("REPLACE INTO progress (dataset, qid, last_answered, correct) VALUES (?, ?, ?, ?)",
              (dataset, qid, date.today().isoformat(), int(correct)))
    conn.commit()
    conn.close()

def should_show_question(dataset, qid):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT last_answered, correct FROM progress WHERE dataset=? AND qid=?", (dataset, qid))
    row = c.fetchone()
    conn.close()
    if not row:
        return True
    last_answered, correct = row
    last_date = datetime.strptime(last_answered, "%Y-%m-%d").date()
    if last_date < date.today():
        return True
    if correct == 0:
        return True
    return False

@app.route("/", methods=["GET", "POST"])
def index():
    datasets = [f for f in os.listdir("datasets") if f.endswith(".json")]
    if request.method == "POST":
        dataset_file = request.form["dataset"]
        qtype = request.form["qtype"]
        return redirect(url_for("quiz_question", dataset=dataset_file, qtype=qtype, index=0))
    return render_template("index.html", datasets=datasets)

@app.route("/quiz/<dataset>/<qtype>/<int:index>", methods=["GET", "POST"])
def quiz_question(dataset, qtype, index):
    questions = load_dataset(dataset)
    if qtype != "mixed":
        questions = [q for q in questions if q.get("type") == qtype]

    while index < len(questions) and not should_show_question(dataset, str(index)):
        index += 1

    if index >= len(questions):
        return "🎉 Quiz finished for today!"

    entry = questions[index]
    entry["dataset"] = dataset

    if request.method == "POST":
        user_answer = request.form.get("answer", "")
        correct = verify_answer(entry, user_answer)
        record_progress(dataset, str(index), correct)
        explanation = entry.get("explanation", "No explanation provided.")
        return render_template("feedback.html",
                               question=entry,
                               user_answer=user_answer,
                               correct=correct,
                               explanation=explanation,
                               next_index=index+1,
                               dataset=dataset,
                               qtype=qtype)

    return render_template("question.html", question=entry, index=index,
                           dataset=dataset, qtype=qtype)

@app.route("/source/<dataset>/<filename>/<snippet>")
def show_source(dataset, filename, snippet):
    path = os.path.join("datasets", filename)
    if not os.path.exists(path):
        return "❌ Source file not found."
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # Highlight the snippet in the source text
    safe_snippet = snippet.strip()
    highlighted = content.replace(safe_snippet, f"<mark>{safe_snippet}</mark>")

    # Render with basic HTML
    return f"""
    <h2>Source: {filename}</h2>
    <pre style="white-space: pre-wrap; font-family: monospace;">
    {highlighted}
    </pre>
    """

# --- Run locally ---
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
