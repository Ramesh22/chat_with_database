"""Seed the demo database with fake users and orders.

By default this creates a local SQLite file (chat_with_database.db), so a
recruiter can try the app with zero setup. Point DATABASE_URL at Postgres to
seed a real Postgres instance instead.

Usage:
    python seed_db.py
    python seed_db.py --users 200 --orders 1000
    DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/mydb python seed_db.py
"""

from __future__ import annotations

import argparse
import os
import random
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from faker import Faker
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    insert,
    select,
)

load_dotenv()

DEFAULT_SQLITE_URL = "sqlite:///./chat_with_database.db"

PRODUCTS = {
    "Mechanical Keyboard": 129.00,
    "Wireless Mouse": 49.99,
    "27-inch Monitor": 329.00,
    "USB-C Hub": 39.50,
    "Noise-Cancelling Headphones": 249.00,
    "Laptop Stand": 59.00,
    "Webcam 1080p": 89.99,
    "Desk Lamp": 34.95,
}

ORDER_STATUSES = ["pending", "paid", "shipped", "delivered", "cancelled"]

metadata = MetaData()

users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(120), nullable=False),
    Column("email", String(255), nullable=False, unique=True),
    Column("city", String(120), nullable=False),
    Column("country", String(120), nullable=False),
    Column("signup_date", DateTime(timezone=True), nullable=False),
)

orders = Table(
    "orders",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False, index=True),
    Column("product", String(120), nullable=False),
    Column("quantity", Integer, nullable=False),
    Column("unit_price", Float, nullable=False),
    Column("status", String(20), nullable=False),
    Column("ordered_at", DateTime(timezone=True), nullable=False),
)


def build_users(fake: Faker, count: int) -> list[dict]:
    fake.unique.clear()
    rows = []
    for _ in range(count):
        rows.append(
            {
                "name": fake.name(),
                "email": fake.unique.email(),
                "city": fake.city(),
                "country": fake.country(),
                "signup_date": fake.date_time_between(
                    start_date="-2y", end_date="now", tzinfo=timezone.utc
                ),
            }
        )
    return rows


def build_orders(fake: Faker, user_rows: list[tuple[int, datetime]], count: int) -> list[dict]:
    now = datetime.now(timezone.utc)
    rows = []
    for _ in range(count):
        user_id, signup_date = random.choice(user_rows)
        product = random.choice(list(PRODUCTS))
        rows.append(
            {
                "user_id": user_id,
                "product": product,
                "quantity": random.randint(1, 5),
                "unit_price": round(PRODUCTS[product] * random.uniform(0.9, 1.1), 2),
                "status": random.choices(
                    ORDER_STATUSES, weights=[10, 30, 20, 35, 5], k=1
                )[0],
                "ordered_at": fake.date_time_between_dates(
                    datetime_start=signup_date,
                    datetime_end=now,
                    tzinfo=timezone.utc,
                )
                or now,
            }
        )
    return rows


def seed(engine, user_count: int, order_count: int) -> None:
    if user_count < 1:
        raise ValueError("--users must be at least 1")

    fake = Faker()
    Faker.seed(42)
    random.seed(42)

    user_rows = build_users(fake, user_count)

    metadata.drop_all(engine)
    metadata.create_all(engine)

    with engine.begin() as conn:
        conn.execute(insert(users), user_rows)
        persisted = conn.execute(select(users.c.id, users.c.signup_date)).all()

        order_rows = build_orders(fake, persisted, order_count)
        if order_rows:
            conn.execute(insert(orders), order_rows)

    print(f"Seeded {len(user_rows)} users and {len(order_rows)} orders into {engine.url.render_as_string(hide_password=True)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the demo database with fake data.")
    parser.add_argument("--users", type=int, default=50, help="number of users (default: 50)")
    parser.add_argument("--orders", type=int, default=200, help="number of orders (default: 200)")
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", DEFAULT_SQLITE_URL),
        help="SQLAlchemy database URL (default: DATABASE_URL env or local SQLite)",
    )
    args = parser.parse_args()

    engine = create_engine(args.database_url, future=True)
    print("Recreating tables: users, orders")
    seed(engine, args.users, args.orders)


if __name__ == "__main__":
    main()
