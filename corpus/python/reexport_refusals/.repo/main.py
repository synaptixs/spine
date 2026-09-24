"""Calls through every binding a static reader must not follow."""

import mix.store
from lib import Lazy, compute
from mix import rate, speed


def run_compute():
    return compute()


def run_lazy():
    return Lazy()


def run_speed():
    return speed()


def run_rate():
    return rate()


def run_start():
    return mix.start()
