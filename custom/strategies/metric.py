from dataclasses import dataclass


@dataclass
class Metric:
    obj: object
    name: str
    attrs: list[str]

    def get_vals(self):
        # An empty name records each attr under its own name, for attrs that already read well
        # as a column (e.g. a strategy's `direction_value`).
        prefix = f"{self.name}_" if self.name else ""
        return {f"{prefix}{attr}": getattr(self.obj, attr) for attr in self.attrs}

    @property
    def tick_lookback(self):
        if hasattr(self.obj, "tick_lookback"):
            return self.obj.tick_lookback
        return 0
