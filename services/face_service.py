import pickle
from html import escape
from pathlib import Path
import re
from db import connect as db_connect


def save_user_with_multiple_embeddings(db_path, embeddings_list, image_paths, name, sr_code, program):
    conn = db_connect(db_path)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE sr_code = ?", (sr_code,))
    existing = c.fetchone()

    if existing:
        user_id = existing[0]
        c.execute("SELECT embeddings FROM users WHERE user_id = ?", (user_id,))
        existing_emb_blob = c.fetchone()[0]
        if existing_emb_blob:
            existing_embeddings = pickle.loads(existing_emb_blob)
            all_embeddings = existing_embeddings + embeddings_list
        else:
            all_embeddings = embeddings_list

        embeddings_blob = pickle.dumps(all_embeddings)
        c.execute(
            """
            UPDATE users
            SET name = ?, course = ?, embeddings = ?, image_paths = ?,
                embedding_dim = ?, last_updated = CURRENT_TIMESTAMP
            WHERE user_id = ?
            """,
            (name, program, embeddings_blob, ";".join(image_paths), len(embeddings_list[0]), user_id),
        )
    else:
        embeddings_blob = pickle.dumps(embeddings_list)
        embedding_dim = len(embeddings_list[0])
        if getattr(conn, "dialect", "sqlite") == "postgres":
            c.execute(
                """
                INSERT INTO users (name, sr_code, course, embeddings, image_paths, embedding_dim)
                VALUES (?, ?, ?, ?, ?, ?)
                RETURNING user_id
                """,
                (name, sr_code, program, embeddings_blob, ";".join(image_paths), embedding_dim),
            )
            user_id = c.fetchone()[0]
        else:
            c.execute(
                """
                INSERT INTO users (name, sr_code, course, embeddings, image_paths, embedding_dim)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (name, sr_code, program, embeddings_blob, ";".join(image_paths), embedding_dim),
            )
            user_id = c.lastrowid

    conn.commit()
    conn.close()
    return user_id


def load_all_embeddings(db_path):
    conn = db_connect(db_path)
    c = conn.cursor()
    c.execute("SELECT user_id, name, sr_code, embeddings FROM users")
    rows = c.fetchall()
    conn.close()

    all_embeddings = []
    user_info = []
    for user_id, name, sr_code, emb_blob in rows:
        if emb_blob:
            embeddings_list = pickle.loads(emb_blob)
            all_embeddings.append(embeddings_list)
            user_info.append({"id": user_id, "name": name, "sr_code": sr_code})
    return all_embeddings, user_info


def render_markdown_as_html(markdown_file: Path) -> str:
    if not markdown_file.exists():
        return "<p>Policy file not found.</p>"

    raw = markdown_file.read_text(encoding="utf-8", errors="ignore")
    lines = raw.splitlines()
    html_lines = []
    in_list = False

    def format_inline(text: str) -> str:
        safe = escape(text)
        # **bold**
        safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
        # `inline code`
        safe = re.sub(r"`([^`]+)`", r"<code>\1</code>", safe)
        return safe

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            continue
        if stripped.startswith("### "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h3>{format_inline(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h2>{format_inline(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h1>{format_inline(stripped[2:])}</h1>")
        elif stripped.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{format_inline(stripped[2:])}</li>")
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<p>{format_inline(stripped)}</p>")

    if in_list:
        html_lines.append("</ul>")

    return "\n".join(html_lines) if html_lines else "<p>No policy content found.</p>"
