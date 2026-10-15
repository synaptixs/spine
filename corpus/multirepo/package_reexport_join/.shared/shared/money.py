def to_cents(amount):
    return int(amount * 100)


class Money:
    @classmethod
    def of(cls, text):
        return cls()
