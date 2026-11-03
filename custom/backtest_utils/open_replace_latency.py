"""
Backtest model of Alpaca's slow replaces in the first seconds after the open.

Alpaca implements a modify as cancel-and-replace. A replace normally lands in ~20ms, but right
after 09:30:00 it can sit pending for seconds (up to ~5s in custom/scripts/venue_latency.py).
While it is pending the original order keeps working at its old price, and Alpaca refuses a cancel
of it with "original order pending replacement". The simulated exchange applies one fixed latency
to every modify and cancel, so neither shows up in a backtest without this.

`OpenReplaceLatency` sits on the "ExecEngine.execute" endpoint, between the strategy (and risk
engine) and the execution engine:

- A `ModifyOrder` sent inside a window is held for that window's latency, then passed on. A
  latency no longer than the venue's own modify latency is passed on at once, so outside the
  burst nothing changes. While held, the order stays PENDING_UPDATE locally
  and rests at its old price at the venue, so it can still fill there; the modify is then rejected
  as it is live.
- A `CancelOrder` that would reach the venue while a modify of that order is still held is
  refused with Alpaca's reason, after the venue's cancel latency, and never reaches the venue.

Every other command passes straight through.
"""
from __future__ import annotations

from datetime import time

import pandas as pd

from custom.strategies.base import parse_et_time
from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.messages import CancelOrder
from nautilus_trader.execution.messages import ModifyOrder
from nautilus_trader.model.events import OrderCancelRejected

EXEC_ENGINE_ENDPOINT = "ExecEngine.execute"
PENDING_REPLACEMENT_REASON = 'HTTP 422: {"code":42210000,"message":"original order pending replacement"}'


class OpenReplaceLatency(Actor):
    """
    Hold modifies and refuse cancels like Alpaca does at the open. See the module docstring.

    Parameters
    ----------
    exec_engine : ExecutionEngine
        The backtest kernel's execution engine, which held modifies are passed on to.
    windows : iterable of (start, stop, latency_secs)
        US/Eastern wall-clock windows [start, stop) and the replace latency for modifies sent
        inside each, in the format of `set_data_delay`. Windows may not overlap.
    modify_latency_ns : int
        The simulated venue's own modify latency, already applied to every modify.
    cancel_latency_ns : int
        The simulated venue's cancel latency, used to time a refused cancel's rejection.

    """

    def __init__(self, exec_engine, windows, modify_latency_ns: int, cancel_latency_ns: int):
        super().__init__(ActorConfig(component_id="OpenReplaceLatency"))
        self._exec_engine = exec_engine
        self._windows: list[tuple[time, time, int]] = []
        for start, stop, latency_secs in windows:
            if latency_secs < 0:
                raise ValueError(f"Window {start}-{stop} has a negative latency: {latency_secs!r}")
            self._windows.append((parse_et_time(start), parse_et_time(stop), int(latency_secs * 1e9)))
        self._windows.sort(key=lambda w: w[0])
        for (_, stop, _), (start, _, _) in zip(self._windows, self._windows[1:], strict=False):
            if start < stop:
                raise ValueError(f"Windows overlap at {start}")
        self._modify_latency_ns = int(modify_latency_ns)
        self._cancel_latency_ns = int(cancel_latency_ns)
        # ClientOrderId -> ns when its held modify is passed on to the venue
        self._held_until_ns: dict = {}
        self._installed = False
        self.num_held = 0
        self.num_cancels_refused = 0

    def on_start(self) -> None:
        # The endpoint is a plain handler lookup on every send, so swapping it puts this in front
        # of every command bound for the execution engine.
        self.msgbus.deregister(EXEC_ENGINE_ENDPOINT, self._exec_engine.execute)
        self.msgbus.register(EXEC_ENGINE_ENDPOINT, self._execute)
        self._installed = True

    def on_stop(self) -> None:
        if self._installed:
            self.msgbus.deregister(EXEC_ENGINE_ENDPOINT, self._execute)
            self.msgbus.register(EXEC_ENGINE_ENDPOINT, self._exec_engine.execute)
            self._installed = False
        self.log.info(
            f"Held {self.num_held} modifies and refused {self.num_cancels_refused} cancels at the open",
        )

    def _latency_ns_for(self, ts_ns: int) -> int | None:
        now = pd.Timestamp(ts_ns, unit="ns", tz="UTC").tz_convert("US/Eastern").time()
        for start, stop, latency_ns in self._windows:
            if start <= now < stop:
                return latency_ns
        return None

    def _execute(self, command) -> None:
        if isinstance(command, ModifyOrder):
            self._on_modify(command)
        elif isinstance(command, CancelOrder):
            self._on_cancel(command)
        else:
            self._exec_engine.execute(command)

    def _on_modify(self, command: ModifyOrder) -> None:
        coid = command.client_order_id
        held_until_ns = self._held_until_ns.get(coid)
        if held_until_ns is None:
            latency_ns = self._latency_ns_for(command.ts_init)
            if latency_ns is not None:
                if latency_ns > self._modify_latency_ns:
                    held_until_ns = command.ts_init + latency_ns
        if held_until_ns is None:
            self._exec_engine.execute(command)
            return

        # A second modify behind a held one queues after it rather than overtaking it.
        self._held_until_ns[coid] = held_until_ns
        self.num_held += 1
        self.log.debug(
            f"Holding modify of {coid} for {(held_until_ns - command.ts_init) / 1e6:.0f}ms (slow replace at the open)",
        )
        self.clock.set_time_alert_ns(
            name=f"release-modify-{command.id}",
            alert_time_ns=held_until_ns,
            callback=lambda _event: self._release(command),
        )

    def _release(self, command: ModifyOrder) -> None:
        coid = command.client_order_id
        if self._held_until_ns.get(coid, 0) <= self.clock.timestamp_ns():
            self._held_until_ns.pop(coid, None)
        # The command keeps its original ts_init, so the venue applies it without adding its own
        # modify latency on top of the hold.
        self._exec_engine.execute(command)

    def _on_cancel(self, command: CancelOrder) -> None:
        coid = command.client_order_id
        held_until_ns = self._held_until_ns.get(coid)
        arrives_ns = command.ts_init + self._cancel_latency_ns
        order = self.cache.order(coid)
        if held_until_ns is None or held_until_ns <= arrives_ns or order is None or order.is_closed:
            self._exec_engine.execute(command)
            return

        self.num_cancels_refused += 1
        self.log.debug(f"Refusing cancel of {coid}: its replace is still pending")
        self.clock.set_time_alert_ns(
            name=f"refuse-cancel-{command.id}",
            alert_time_ns=arrives_ns,
            callback=lambda _event: self._refuse_cancel(command),
        )

    def _refuse_cancel(self, command: CancelOrder) -> None:
        order = self.cache.order(command.client_order_id)
        ts_now = self.clock.timestamp_ns()
        event = OrderCancelRejected(
            trader_id=command.trader_id,
            strategy_id=command.strategy_id,
            instrument_id=command.instrument_id,
            client_order_id=command.client_order_id,
            venue_order_id=order.venue_order_id if order is not None else command.venue_order_id,
            account_id=order.account_id if order is not None else None,
            reason=PENDING_REPLACEMENT_REASON,
            event_id=UUID4(),
            ts_event=ts_now,
            ts_init=ts_now,
        )
        self._exec_engine.process(event)
