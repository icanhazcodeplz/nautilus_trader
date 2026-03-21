from nautilus_trader.model import Price


def make_Price(val: float) -> Price:
    return Price(val, precision=2 if val >= 1 else 4)
