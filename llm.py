"""Turn a natural-language question into a read-only SQLite query.

The generated SQL is untrusted, so :func:`validate_select_only` rejects
anything that is not a single ``SELECT``/``WITH ... SELECT`` statement before
it ever reaches the database. This is defense in depth: ``db.py`` also connects
with a read-only role/session.
"""

from __future__ import annotations

import os
import re
from typing import Any

from openai import OpenAI

DEFAULT_MODEL = "gpt-4o-mini"

SYSTEM_PROMPT = """You are an expert SQLite analyst. Translate the user's question into a single read-only SQLite query.

Rules:
- Target SQLite syntax only (not PostgreSQL or MySQL).
- Use only the tables and columns listed in the provided schema.
- Generate exactly one statement: SELECT, or WITH followed by SELECT.
- Never modify data or schema (no INSERT, UPDATE, DELETE, DROP, ALTER, etc.).
- Prefer explicit columns over SELECT * when reasonable.
- Do not include comments, explanations, or markdown fences.
- Return only the SQL query.
"""

PROMPT_TEMPLATE = """Database schema:
{schema}

Question: {question}

SQL:"""

_ALLOWED_FIRST_KEYWORDS = {"select", "with"}
_FORBIDDEN_KEYWORDS = {
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "truncate",
    "attach",
    "detach",
    "pragma",
    "vacuum",
    "reindex",
    "grant",
    "revoke",
    "begin",
    "commit",
    "rollback",
    "savepoint",
    "release",
    "analyze",
    "load_extension",
}

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FENCE_RE = re.compile(r"^```(?:sql|sqlite)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


class UnsafeSQLError(ValueError):
    """Raised when generated SQL is not a single, read-only SELECT statement."""


def format_schema(schema: dict[str, list[dict]]) -> str:
    """Render the schema mapping from ``db.get_schema`` as compact DDL text."""
    lines = []
    for table_name, columns in schema.items():
        parts = []
        for column in columns:
            definition = f"{column['name']} {column.get('type', '')}".strip()
            if column.get("primary_key"):
                definition += " PRIMARY KEY"
            elif column.get("nullable") is False:
                definition += " NOT NULL"
            parts.append(definition)
        lines.append(f"TABLE {table_name} ({', '.join(parts)});")
    return "\n".join(lines)


def _mask_string_literals(sql: str) -> str:
    """Replace the contents of quoted literals/identifiers with spaces."""
    out: list[str] = []
    i, length = 0, len(sql)
    while i < length:
        char = sql[i]
        if char in ("'", '"', "`", "["):
            closing = "]" if char == "[" else char
            out.append(char)
            i += 1
            while i < length:
                if sql[i] == closing:
                    out.append(closing)
                    if closing != "]" and i + 1 < length and sql[i + 1] == closing:
                        out.append(" ")
                        i += 2
                        continue
                    i += 1
                    break
                out.append(" ")
                i += 1
            continue
        out.append(char)
        i += 1
    return "".join(out)


def _split_statements(sql: str) -> list[str]:
    """Split on semicolons outside strings/comments; drop comments."""
    statements: list[str] = []
    buffer: list[str] = []
    i, length = 0, len(sql)
    while i < length:
        char = sql[i]
        if char in ("'", '"', "`", "["):
            closing = "]" if char == "[" else char
            buffer.append(char)
            i += 1
            while i < length:
                buffer.append(sql[i])
                if sql[i] == closing:
                    if closing != "]" and i + 1 < length and sql[i + 1] == closing:
                        buffer.append(sql[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if char == "-" and i + 1 < length and sql[i + 1] == "-":
            while i < length and sql[i] != "\n":
                i += 1
            continue
        if char == "/" and i + 1 < length and sql[i + 1] == "*":
            i += 2
            while i + 1 < length and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i += 2
            continue
        if char == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
            i += 1
            continue
        buffer.append(char)
        i += 1

    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


def validate_select_only(sql: str) -> str:
    """Return the normalized SQL if it is a single read-only statement.

    Raises :class:`UnsafeSQLError` otherwise. Fails closed: anything ambiguous
    or containing a write/DDL keyword is rejected.
    """
    if not sql or not sql.strip():
        raise UnsafeSQLError("The model returned an empty query.")

    statements = _split_statements(sql)
    if len(statements) != 1:
        raise UnsafeSQLError("Only a single SQL statement is allowed.")

    words = _WORD_RE.findall(_mask_string_literals(statements[0]).lower())
    if not words or words[0] not in _ALLOWED_FIRST_KEYWORDS:
        raise UnsafeSQLError("Only SELECT queries are allowed.")

    forbidden = sorted(set(words) & _FORBIDDEN_KEYWORDS)
    if forbidden:
        raise UnsafeSQLError(
            f"Query contains disallowed keyword(s): {', '.join(forbidden)}."
        )

    return statements[0].strip().rstrip(";").strip()


def _clean_model_output(raw: str) -> str:
    sql = (raw or "").strip()
    fence = _FENCE_RE.match(sql)
    if fence:
        sql = fence.group(1).strip()
    if sql.lower().startswith("sql:"):
        sql = sql[4:].strip()
    return sql


def generate_sql(
    question: str,
    schema: dict[str, list[dict]],
    *,
    model: str | None = None,
    client: Any | None = None,
) -> str:
    """Generate a validated, read-only SQLite SELECT query for ``question``."""
    if not question or not question.strip():
        raise ValueError("question must be a non-empty string")
    if not schema:
        raise ValueError("schema must contain at least one table")

    prompt = PROMPT_TEMPLATE.replace("{schema}", format_schema(schema)).replace(
        "{question}", question.strip()
    )

    client = client or OpenAI()
    response = client.chat.completions.create(
        model=model or os.getenv("LLM_MODEL", DEFAULT_MODEL),
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )

    raw = response.choices[0].message.content
    return validate_select_only(_clean_model_output(raw or ""))
