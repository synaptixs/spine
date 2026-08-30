from sqlalchemy import insert, select
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Invoice(Base):
    __tablename__ = "invoices"


def issue(session):
    return session.execute(insert(Invoice))


def latest(session):
    return session.execute(select(Invoice))
