
def color_scale(size):
    if size == 1:
        return "white"
    elif size < 100:
        return "#e9d9fa"
    else:
        return "#7602fa"

def convert_tbbo(data, rounding_factor=3):
    divisor = 10 ** rounding_factor
    last_adjusted = None
    tbbo_list = []
    for tbbo in data:
        rounded = int(round(tbbo.ts_event / 1e9, rounding_factor) * divisor)

        if last_adjusted is not None and rounded <= last_adjusted:
            diff = last_adjusted - rounded
            adjusted = rounded + diff + 1
        else:
            adjusted = rounded
        last_adjusted = adjusted
        secs = adjusted / divisor

        price = float(tbbo.price)
        bid = float(tbbo.bid if str(tbbo.ask) != 'nan' else price - 1.0)
        ask = float(tbbo.ask if str(tbbo.ask) != 'nan' else price + 1.0)
        tbbo_json = {
            "time": secs,
            "price": price,
            "size": int(tbbo.size),
            "bid": bid,
            # "bid_size": int(tbbo.bid_size),
            "ask": ask,
            # "ask_size": int(tbbo.ask_size),
            "color":color_scale(tbbo.size)
        }
        tbbo_list.append(tbbo_json)
    return tbbo_list
