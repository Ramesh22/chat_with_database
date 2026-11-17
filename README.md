# Chat with Your Database

Ask your database questions in plain English and let an LLM generate the SQL.

## Run everything with Docker

With Docker Desktop running, start the whole app (FastAPI API + Streamlit UI +
seeded SQLite demo database) with one command:

```bash
OPENAI_API_KEY=sk-your-key docker compose up --build
```

Or copy `.env.example` to `.env`, fill in your LLM settings, then run
`docker compose up --build`:

```env
OPENAI_API_KEY=sk-your-key
OPENAI_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
```

Any OpenAI-compatible API works. For DeepSeek, use
`OPENAI_BASE_URL=https://api.deepseek.com` and the exact model ID its `/models`
endpoint returns. `LLM_MODEL` is passed through to the provider unchanged.

- Chat UI: <http://localhost:8501>
- API docs: <http://localhost:8000/docs>
- The API seeds the SQLite database on first boot and keeps it in the `app-data` volume.
- Reset everything (and reseed on next start): `docker compose down -v`

## Seed the demo database

The `seed_db.py` script creates the `users` and `orders` tables and fills them
with reproducible fake data (50 users and 200 orders by default). It uses a
local SQLite file out of the box, so no database server is required.

### Quick start (SQLite)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python seed_db.py
```

This creates `chat_with_database.db` in the project root. To inspect it:

```bash
sqlite3 chat_with_database.db "SELECT * FROM users LIMIT 5;"
```

### Run the API and UI locally

```bash
# terminal 1 — force SQLite even if .env points at Postgres
DATABASE_URL=sqlite:///./chat_with_database.db uvicorn main:app --reload

# terminal 2
API_URL=http://localhost:8000 streamlit run ui/streamlit_app.py
```

Open <http://localhost:8501>. The API docs are at <http://localhost:8000/docs>.
Copy `.env.example` to `.env` and fill in the LLM settings described above before
starting the API.

### Custom row counts

```bash
python seed_db.py --users 200 --orders 1000
```

### Seed a Postgres database instead

Override `DATABASE_URL` (or uncomment the Postgres line in `.env`) with a
Postgres connection string:

```bash
export DATABASE_URL="postgresql+psycopg://user:password@localhost:5432/mydb"
python seed_db.py
```

> The app and LLM prompt are SQLite-first. If you run against Postgres, update the
> dialect instructions in `llm.py` so the generated SQL uses Postgres syntax.

> The script **drops and recreates** the `users` and `orders` tables on every
> run, so it is safe to re-run but will overwrite any data in those tables.
> Point it at a read-only demo database, not production.

## Example questions

- How many users and orders are there?
- What is the average order value?
- Which 5 products generate the most revenue?
- Who are the top 5 customers by total spend?
- Show monthly revenue from delivered orders.
- Which users have never placed an order?
- What percentage of orders were cancelled?
- Show the 10 most recent orders with the customer's name and country.

When a result has one label column and one numeric column (up to 50 rows), the UI
also renders a bar chart automatically, or a line chart for date/time columns.

## How malicious queries are blocked

Classic SQL injection is not the primary risk here: the user never supplies SQL.
The question only builds an LLM prompt; the real threat is prompt injection that
coerces the model into emitting a destructive query. The app uses layered defenses:

- **Read-only at the connection level.** `db.py` opens every session read-only:
  `PRAGMA query_only = ON` for the demo SQLite file, and
  `default_transaction_read_only=on` plus a statement timeout for Postgres. Even if
  validation is bypassed, these sessions cannot modify data.
- **Single SELECT statement validation.** `llm.py` strips fences/comments, splits
  statements while respecting string literals, requires exactly one statement that
  starts with `SELECT` or `WITH`, and rejects write/DDL keywords (`INSERT`,
  `UPDATE`, `DELETE`, `DROP`, `PRAGMA`, `ATTACH`, `load_extension`, ...). It fails
  closed.
- **No user-controlled SQL.** The question is never concatenated into a query; only
  the model's validated output is executed. Credentials come from `DATABASE_URL`,
  never source code.


### Screenshots
<img width="2856" height="1200" alt="image" src="https://github.com/user-attachments/assets/e0f860e3-6870-41d4-b4e1-90e170b671aa" />

<img width="1428" height="481" alt="image" src="https://github.com/user-attachments/assets/c4ce4fa2-231c-4fba-ae8f-49ad0ca22e2e" />

<img width="2856" height="1490" alt="image" src="https://github.com/user-attachments/assets/79495296-3046-451a-b81e-adf02c16d2e3" />

### Limitations

- **The validator is a heuristic, not a SQL parser.** It is a conservative blocklist
  plus statement-shape check, so unusual SQLite syntax may slip through, and it can
  reject legitimate queries (e.g. a column named `update`).
- **Read-only is not confidentiality.** Any table or column visible to the role can
  be read, including `sqlite_master`. A user can still ask for emails or PII. Prefer
  scoped views and Postgres column grants / row-level security, and use synthetic data.
- **No parameterization to audit.** Generated values are embedded in the SQL text.
  Safe because only the model writes them, but there are no per-value bind parameters.
- **Resource exhaustion is still possible.** `MAX_RESULT_ROWS` caps the response, not
  the work: expensive joins or recursive CTEs can consume CPU. The statement timeout
  applies only to Postgres; SQLite needs a progress-handler or query budget in production.
- **No auth, rate limiting, or spend cap.** `/ask` is unauthenticated and every call
  costs LLM tokens. Add auth, quotas, and a token budget before exposing it publicly.
- **Prompt injection and misleading answers remain possible.** Valid SELECTs can still
  leak data or return confidently wrong results; treat output as untrusted.
- **Error messages are verbose.** Invalid-SQL responses include the query and database
  error to aid debugging, which also reveals schema details.
