"""FastAPI entrypoint for Chat with Your Database."""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException
from openai import OpenAIError
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from db import get_engine, get_schema
from llm import UnsafeSQLError, generate_sql

logger = logging.getLogger(__name__)

DEFAULT_MAX_RESULT_ROWS = 200

app = FastAPI(title="Chat with Your Database", version="0.1.0")


class AskRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="A natural-language question about the data.",
        examples=["Which five countries have the most orders?"],
    )


class AskResponse(BaseModel):
    question: str
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool


def _max_result_rows() -> int:
    try:
        return max(int(os.getenv("MAX_RESULT_ROWS", DEFAULT_MAX_RESULT_ROWS)), 1)
    except ValueError:
        return DEFAULT_MAX_RESULT_ROWS


def _unique_columns(keys: list[str]) -> list[str]:
    """Keep duplicate labels from joins (e.g. two ``id`` columns) distinct."""
    used: set[str] = set()
    columns: list[str] = []
    for key in keys:
        candidate, suffix = key, 2
        while candidate in used:
            candidate = f"{key}_{suffix}"
            suffix += 1
        used.add(candidate)
        columns.append(candidate)
    return columns


@app.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest) -> AskResponse:
    """Answer a question by generating and running a read-only SQL query."""
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="question must not be blank")

    try:
        schema = get_schema()
    except SQLAlchemyError as exc:
        logger.exception("Failed to read the database schema")
        raise HTTPException(
            status_code=503, detail="Could not read the database schema."
        ) from exc

    if not schema:
        raise HTTPException(
            status_code=503,
            detail="No tables are visible. Run `python seed_db.py` to create the demo database.",
        )

    if not os.getenv("OPENAI_API_KEY", "").strip():
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY is not set. Add it to .env, then recreate the API container.",
        )

    try:
        sql = generate_sql(question, schema)
    except UnsafeSQLError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OpenAIError as exc:
        logger.exception(
            "LLM request failed (model=%s, base_url=%s)",
            os.getenv("LLM_MODEL", "unset"),
            os.getenv("OPENAI_BASE_URL", "default"),
        )
        message = str(exc)
        api_key = os.getenv("OPENAI_API_KEY", "")
        if api_key:
            message = message.replace(api_key, "[redacted]")
        raise HTTPException(
            status_code=502,
            detail=f"The language model request failed: {message}",
        ) from exc

    logger.info("Generated SQL: %s", sql)

    limit = _max_result_rows()
    try:
        with get_engine().connect() as conn:
            result = conn.exec_driver_sql(sql)
            columns = _unique_columns(list(result.keys()))
            fetched = result.fetchmany(limit + 1)
    except SQLAlchemyError as exc:
        logger.exception("Generated SQL failed to execute: %s", sql)
        raise HTTPException(
            status_code=400,
            detail={
                "message": "The generated SQL could not be executed.",
                "sql": sql,
                "error": str(exc.orig if exc.orig is not None else exc),
            },
        ) from exc

    truncated = len(fetched) > limit
    rows = [dict(zip(columns, row)) for row in fetched[:limit]]

    return AskResponse(
        question=question,
        sql=sql,
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
