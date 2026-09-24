"""Every caller reaches its target through a re-export, never through the defining module."""

import app
from app import Base, Engine, Order, Store, area, assist
from app.relay import helper


class Special(Base):
    """Inherits a class the package re-exports."""


def via_package():
    return Store()


def via_rename():
    return assist()


def via_chain():
    return Engine()


def via_star_all():
    return Order()


def via_star_public():
    return area()


def via_module():
    return helper()


def via_attribute():
    return app.Store()


def via_member():
    return Engine.start(Engine())
