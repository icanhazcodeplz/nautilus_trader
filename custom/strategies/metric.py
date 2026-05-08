from dataclasses import dataclass


@dataclass
class Metric:
    obj: object
    name: str
    attrs: list[str]

    def get_vals(self):
        return {f"{self.name}_{attr}": getattr(self.obj, attr) for attr in self.attrs}

    @property
    def tick_lookback(self):
        if hasattr(self.obj, "tick_lookback"):
            return self.obj.tick_lookback
        return 0
