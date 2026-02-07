
def market_round(price):
    if price < 1.0:
        return round(price, 4)
    return round(price, 2)
