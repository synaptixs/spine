from sqlalchemy import select
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Invoice(Base):
    __tablename__ = "invoices"


class Ledger(Base):
    __tablename__ = "ledger"


def summarise(session):
    return session.execute(select(Invoice))


def audit(session):
    return session.execute(select(Ledger))
