import json

from shared.money import to_cents


def charge(amount):
    return json.dumps({"cents": to_cents(amount)})
