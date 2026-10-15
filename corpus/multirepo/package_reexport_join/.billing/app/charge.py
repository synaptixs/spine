import json

from shared import Money, to_cents


def charge(amount):
    return json.dumps({"cents": to_cents(amount)})


def parse(text):
    return Money.of(text)
