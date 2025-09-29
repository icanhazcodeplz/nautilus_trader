from typing import Callable, List

from custom.app_utils.viz import load_metrics_from_txt_file
from custom.utils.load_catalog_data import get_tbbo


def round_time_to_ms_and_increment_dup_times(list_of_data: list, data_to_dict_fn: Callable | None = None) -> List[dict]:
    """
    Rounds timestamps to the nearest millisecond and increments them to ensure no duplicates.

    This function processes a list of data, rounds the timestamp of each data point to the
    nearest millisecond, and ensures that timestamps are unique by incrementing duplicate
    values by small amounts.

    Parameters:
    list_of_data: list
        A list containing the data items to be processed.
    data_to_dict_fn: Callable
        A callable that converts a single data item to a dictionary. The dictionary must
        contain a "time" key representing the timestamp of the data in seconds since the
        epoch.

    Returns:
    list
        A list of dictionaries with the processed and adjusted timestamps.
    """
    rounding_factor = 3
    divisor = 10 ** rounding_factor
    last_adjusted = None
    data_list_of_dicts = []
    for data in list_of_data:
        data_dict = data_to_dict_fn(data) if data_to_dict_fn else data
        rounded = int(round(data_dict["time"], rounding_factor) * divisor)

        if last_adjusted is not None and rounded <= last_adjusted:
            diff = last_adjusted - rounded
            adjusted = rounded + diff + 1
        else:
            adjusted = rounded
        last_adjusted = adjusted
        secs = adjusted / divisor
        data_dict["time"] = secs

        data_list_of_dicts.append(data_dict)
    return data_list_of_dicts



def _tbbo_to_dict(tbbo):
    tbbo_json = {
        "time": tbbo.ts_event / 1e9,
        "price": (float(tbbo.price)),
        "size": int(tbbo.size),
        # "bid_size": int(tbbo.bid_size),
        # "ask_size": int(tbbo.ask_size),
    }
    if str(tbbo.bid) != 'nan':
        tbbo_json["bid"] = tbbo.bid
    if str(tbbo.ask) != 'nan':
        tbbo_json["ask"] = tbbo.ask
    return tbbo_json


def get_and_convert_tbbo():
    data = get_tbbo()
    return round_time_to_ms_and_increment_dup_times(data, _tbbo_to_dict)

def get_metrics_data():
    mets = load_metrics_from_txt_file()
    return round_time_to_ms_and_increment_dup_times(mets)

def combine_tbbo_and_metrics_data():
    # FIXME: This ignores metrics data points if they do not have "time" that matches a TBBO data point.

    tbbo_list = get_and_convert_tbbo()
    mets = get_metrics_data()


    mets_dict = {m["time"]: m for m in mets}

    for tbbo in tbbo_list:
        if tbbo["time"] in mets_dict:
            tbbo.update(mets_dict[tbbo["time"]])
    return tbbo_list

if __name__ == "__main__":
    mets_data = combine_tbbo_and_metrics_data()
    print()