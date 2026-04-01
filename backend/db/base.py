"""Declarative base for all PostgreSQL ORM models."""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
