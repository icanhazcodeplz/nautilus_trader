import dataclasses

from custom.app_utils.process_data_DEPRECATED import convert_tbbo


@dataclasses.dataclass
class MockTBBOData:
    ts_event:int = 0
    ts_init:int = 0
    price:float = 10.0
    size:int = 10
    side:str = "N"
    bid:float = 10.0
    bid_size:int = 10
    ask:float = 10.0
    ask_size:int = 10

    def __post_init__(self):
        self.ts_event = self.ts_event + int(1e19)
        self.ts_init = self.ts_init + int(1e19)

def _make_tbbo(nanoseconds=None):
    tbbo_list = []
    for nano in nanoseconds:
        # 1753258683847059968
        mock_tbbo = MockTBBOData(ts_event=nano, ts_init=nano)
        tbbo_list += [mock_tbbo]
    # print(tbbo_list)
    return tbbo_list

def _test_convert_tbbo(millies_list):
    nanos_list = [int(m * 1e6) for m in millies_list]
    tbbo_list = _make_tbbo(nanos_list)
    converted = convert_tbbo(tbbo_list)
    previous_t = 0
    time_values = []
    for tbbo in converted:
        assert tbbo["time"] > previous_t
        previous_t = tbbo["time"]
        time_values.append(tbbo["time"])
    return

def test_more_than_10_same_digit():
    time_values = _test_convert_tbbo([1] * 11)

def test_butt_up_to_next_tens():
    _test_convert_tbbo([8,8,10])

def test_overlap_next_tens():
    _test_convert_tbbo([8,8,8,10])