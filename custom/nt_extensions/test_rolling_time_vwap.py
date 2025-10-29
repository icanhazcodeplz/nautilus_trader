import pytest

from custom.nt_extensions.indicators import RollingTimeVWAP


class TestRollingTimeVWAP:
    """Tests created by Claude Code"""

    def setup(self):
        # Fixture Setup
        self.indicator = RollingTimeVWAP(rolling_window=5, bin_ms=1000)

    def test_name_returns_expected_string(self):
        # Act, Assert
        assert self.indicator.name == "RollingTimeVWAP"

    def test_str_repr_returns_expected_string(self):
        # Act, Assert
        assert str(self.indicator) == "RollingTimeVWAP(5, 1000)"
        assert repr(self.indicator) == "RollingTimeVWAP(5, 1000)"

    def test_initialized_without_inputs_returns_false(self):
        # Act, Assert
        assert self.indicator.initialized is False

    def test_initialized_with_required_inputs_returns_true(self):
        # Arrange, Act
        self.indicator.update_raw(ts_init=0, price=1.00000, volume=10000)

        # Assert
        assert self.indicator.initialized is True

    def test_has_inputs_after_first_update(self):
        # Arrange, Act
        self.indicator.update_raw(ts_init=0, price=1.00000, volume=10000)

        # Assert
        assert self.indicator.has_inputs is True

    def test_value_with_one_input_returns_price(self):
        # Arrange, Act
        self.indicator.update_raw(ts_init=0, price=1.00000, volume=10000)

        # Assert
        assert self.indicator.value == 1.00000

    def test_update_with_zero_volume_skips_processing(self):
        # Arrange
        self.indicator.update_raw(ts_init=0, price=1.00000, volume=10000)
        initial_value = self.indicator.value

        # Act - update with zero volume
        self.indicator.update_raw(ts_init=int(500 * 1e6), price=2.00000, volume=0)

        # Assert - value should not change
        assert self.indicator.value == initial_value

    def test_updates_within_same_bin_accumulate(self):
        # Arrange
        ts_start = 0

        # Act - multiple updates within same 1000ms bin
        self.indicator.update_raw(ts_init=ts_start, price=100.0, volume=10)
        self.indicator.update_raw(ts_init=ts_start + int(200 * 1e6), price=101.0, volume=20)
        self.indicator.update_raw(ts_init=ts_start + int(500 * 1e6), price=102.0, volume=30)

        # Assert
        # Current bin VWAP = (100*10 + 101*20 + 102*30) / (10 + 20 + 30)
        # = (1000 + 2020 + 3060) / 60 = 6080 / 60 = 101.333...
        assert self.indicator.vwap_current_bin == pytest.approx(101.33333333333333, rel=1e-9)
        # Overall value should still be the initial price since no bin has closed
        assert self.indicator.value == 100.0

    def test_new_bin_updates_value(self):
        # Arrange
        ts_start = 0

        # Act - first bin
        self.indicator.update_raw(ts_init=ts_start, price=100.0, volume=10)
        self.indicator.update_raw(ts_init=ts_start + int(500 * 1e6), price=101.0, volume=20)

        # New bin (after 1000ms)
        self.indicator.update_raw(ts_init=ts_start + int(1001 * 1e6), price=105.0, volume=15)

        # Assert
        # First bin VWAP = (100*10 + 101*20) / 30 = 3020 / 30 = 100.666...
        # Overall VWAP should now include the completed first bin
        expected_vwap = (100 * 10 + 101 * 20) / 30
        assert self.indicator.value == pytest.approx(expected_vwap, rel=1e-9)
        # Current bin should show the new price
        assert self.indicator.vwap_current_bin == 105.0

    def test_rolling_window_behavior(self):
        # Arrange
        ts_start = 0

        # Act - create 6 bins (rolling window is 5)
        for i in range(6):
            ts = ts_start + int(i * 1001 * 1e6)  # Each bin is > 1000ms apart
            price = 100.0 + i
            self.indicator.update_raw(ts_init=ts, price=price, volume=10)

        # Assert
        # When bin 5 starts, bins 0-4 are in the deque (bin 5 is current)
        # Bins 0-4 (prices 100-104) remain in deque (maxlen=5 so all 5 fit)
        # Bin 5 (price=105) is the current open bin (not in deque)
        # VWAP = (100*10 + 101*10 + 102*10 + 103*10 + 104*10) / 50
        # = (1000 + 1010 + 1020 + 1030 + 1040) / 50 = 5100 / 50 = 102.0
        assert self.indicator.value == pytest.approx(102.0, rel=1e-9)

    def test_multiple_trades_across_multiple_bins(self):
        # Arrange
        ts_start = 0

        # Act - Bin 0 (ts_start to ts_start + 1000ms)
        self.indicator.update_raw(ts_init=ts_start, price=100.0, volume=10)
        self.indicator.update_raw(ts_init=ts_start + int(500 * 1e6), price=102.0, volume=20)

        # Bin 1 (ts_start + 1001ms to ts_start + 2001ms)
        self.indicator.update_raw(ts_init=ts_start + int(1001 * 1e6), price=105.0, volume=30)
        self.indicator.update_raw(ts_init=ts_start + int(1500 * 1e6), price=107.0, volume=40)

        # Bin 2 (ts_start + 2002ms) - starts new bin (needs > not >=)
        self.indicator.update_raw(ts_init=ts_start + int(2002 * 1e6), price=110.0, volume=50)

        # Assert
        # When bin 2 starts:
        # - Bin 0 VWAP = (100*10 + 102*20) / 30 = 3040 / 30 = 101.333...
        # - Bin 1 VWAP = (105*30 + 107*40) / 70 = 7430 / 70 = 106.142857...
        # - Bins 0 and 1 are in the deque
        # - Bin 2 is the current open bin
        # Overall VWAP (completed bins only) = (3040 + 7430) / 100 = 10470 / 100 = 104.7
        expected_vwap = (100 * 10 + 102 * 20 + 105 * 30 + 107 * 40) / 100
        assert self.indicator.value == pytest.approx(expected_vwap, rel=1e-9)
        assert self.indicator.vwap_current_bin == 110.0

    def test_bin_size_parameter(self):
        # Arrange - create indicator with 500ms bins
        indicator_500ms = RollingTimeVWAP(rolling_window=3, bin_ms=500)
        ts_start = 0

        # Act
        indicator_500ms.update_raw(ts_init=ts_start, price=100.0, volume=10)
        indicator_500ms.update_raw(ts_init=ts_start + int(501 * 1e6), price=105.0, volume=20)

        # Assert - after 501ms, should have moved to new bin
        expected_vwap = 100.0  # First bin value
        assert indicator_500ms.value == pytest.approx(expected_vwap, rel=1e-9)

    def test_current_bin_tracking(self):
        # Arrange
        ts_start = 0

        # Act - updates within same bin
        self.indicator.update_raw(ts_init=ts_start, price=100.0, volume=10)
        self.indicator.update_raw(ts_init=ts_start + int(100 * 1e6), price=110.0, volume=10)
        self.indicator.update_raw(ts_init=ts_start + int(200 * 1e6), price=120.0, volume=10)

        # Assert
        # Current bin VWAP = (100*10 + 110*10 + 120*10) / 30 = 3300 / 30 = 110.0
        assert self.indicator.vwap_current_bin == pytest.approx(110.0, rel=1e-9)

    def test_initialization_parameters(self):
        # Act, Assert
        assert self.indicator.rolling_window == 5
        assert self.indicator.bin_ns == 1000 * 1e6

    def test_different_rolling_window_sizes(self):
        # Arrange - test with window size of 2
        small_window_indicator = RollingTimeVWAP(rolling_window=2, bin_ms=1000)
        ts_start = 0

        # Act - create 3 bins
        small_window_indicator.update_raw(ts_init=ts_start, price=100.0, volume=10)
        small_window_indicator.update_raw(ts_init=ts_start + int(1001 * 1e6), price=110.0, volume=10)
        small_window_indicator.update_raw(ts_init=ts_start + int(2002 * 1e6), price=120.0, volume=10)

        # Assert
        # When bin 2 starts (price=120):
        # - Bin 0 (price=100) is in the deque
        # - Bin 1 (price=110) is in the deque
        # - Bin 2 (price=120) is the current open bin
        # With maxlen=2, both bins 0 and 1 fit in the deque
        # VWAP = (100*10 + 110*10) / 20 = 2100 / 20 = 105.0
        assert small_window_indicator.value == pytest.approx(105.0, rel=1e-9)

    def test_reset_raises_exception(self):
        # Arrange
        self.indicator.update_raw(ts_init=0, price=100.0, volume=10)

        # Act, Assert
        with pytest.raises(Exception, match="Not supported"):
            self.indicator._reset()

    def test_large_volume_values(self):
        # Arrange
        ts_start = 0

        # Act - test with large volumes
        self.indicator.update_raw(ts_init=ts_start, price=100.0, volume=1000000)
        self.indicator.update_raw(ts_init=ts_start + int(1001 * 1e6), price=101.0, volume=2000000)

        # Assert
        expected_vwap = 100.0  # First bin completed
        assert self.indicator.value == pytest.approx(expected_vwap, rel=1e-9)

    def test_precision_with_small_prices(self):
        # Arrange
        ts_start = 0

        # Act - test with small decimal prices
        self.indicator.update_raw(ts_init=ts_start, price=0.00001, volume=100)
        self.indicator.update_raw(ts_init=ts_start + int(1001 * 1e6), price=0.00002, volume=200)

        # Assert
        expected_vwap = 0.00001
        assert self.indicator.value == pytest.approx(expected_vwap, rel=1e-5)

    def test_bin_boundary_exactly_at_limit(self):
        # Arrange
        ts_start = 0

        # Act - update exactly at the bin boundary (1000ms)
        self.indicator.update_raw(ts_init=ts_start, price=100.0, volume=10)
        self.indicator.update_raw(ts_init=ts_start + int(1000 * 1e6), price=105.0, volume=20)

        # Assert - at exact boundary, should still be in same bin
        # because condition is ts_init > (start + bin_ns), not >=
        assert self.indicator.vwap_current_bin == pytest.approx((100 * 10 + 105 * 20) / 30, rel=1e-9)
        assert self.indicator.value == 100.0  # No bin closed yet

    def test_single_trade_per_bin(self):
        # Arrange
        ts_start = 0

        # Act - one trade per bin
        self.indicator.update_raw(ts_init=ts_start, price=100.0, volume=10)
        self.indicator.update_raw(ts_init=ts_start + int(1001 * 1e6), price=110.0, volume=10)
        self.indicator.update_raw(ts_init=ts_start + int(2002 * 1e6), price=120.0, volume=10)

        # Assert
        # VWAP = (100*10 + 110*10) / 20 = 2100 / 20 = 105.0
        assert self.indicator.value == pytest.approx(105.0, rel=1e-9)

    def test_handle_trade_tick_integration(self):
        # Arrange
        from nautilus_trader.test_kit.stubs.data import TestDataStubs

        # Act
        tick1 = TestDataStubs.trade_tick(price=100.0, size=10.0)
        self.indicator.handle_trade_tick(tick1)

        # Assert
        assert self.indicator.initialized is True
        assert self.indicator.value == 100.0

    def test_empty_bins_handling(self):
        # Arrange - test with bins that complete with zero accumulated volume
        ts_start = 0

        # Act - first bin with trades
        self.indicator.update_raw(ts_init=ts_start, price=100.0, volume=10)

        # Move to next bin without any trades (volume=0)
        self.indicator.update_raw(ts_init=ts_start + int(1001 * 1e6), price=105.0, volume=0)

        # Assert - value should still be from initialization since no bins with volume have completed
        assert self.indicator.value == 100.0

    def test_varying_volumes_within_bin(self):
        # Arrange
        ts_start = 0

        # Act - trades with very different volumes
        self.indicator.update_raw(ts_init=ts_start, price=100.0, volume=1)
        self.indicator.update_raw(ts_init=ts_start + int(100 * 1e6), price=200.0, volume=99)

        # Assert
        # Current bin VWAP should be weighted heavily toward the larger volume trade
        # (100*1 + 200*99) / 100 = (100 + 19800) / 100 = 199.0
        assert self.indicator.vwap_current_bin == pytest.approx(199.0, rel=1e-9)

    def test_sequential_bin_transitions(self):
        # Arrange
        ts_start = 0

        # Act - create several bins in sequence
        for i in range(3):
            ts = ts_start + int(i * 1100 * 1e6)
            self.indicator.update_raw(ts_init=ts, price=100.0 + i * 10, volume=10 + i * 5)

        # Assert - verify indicator tracks state correctly
        assert self.indicator.initialized is True
        assert len(self.indicator._trade_values) == 2  # 2 completed bins
        assert self.indicator._current_bin_volume > 0  # Current bin has data

    def test_very_long_time_gaps(self):
        # Arrange
        ts_start = 0

        # Act - large time gap between bins (simulating sparse data)
        self.indicator.update_raw(ts_init=ts_start, price=100.0, volume=10)
        # Jump forward by 1 hour
        self.indicator.update_raw(ts_init=ts_start + int(3600 * 1000 * 1e6), price=105.0, volume=10)

        # Assert
        assert self.indicator.value == 100.0  # First bin completed
        assert self.indicator.vwap_current_bin == 105.0  # Second bin is current

    def test_floating_point_prices_and_volumes(self):
        # Arrange
        ts_start = 0

        # Act - test with floating point values
        self.indicator.update_raw(ts_init=ts_start, price=99.99, volume=12.5)
        self.indicator.update_raw(ts_init=ts_start + int(100 * 1e6), price=100.01, volume=7.5)

        # Assert
        # VWAP = (99.99*12.5 + 100.01*7.5) / 20 = (1249.875 + 750.075) / 20 = 99.9975
        expected = (99.99 * 12.5 + 100.01 * 7.5) / 20
        assert self.indicator.vwap_current_bin == pytest.approx(expected, rel=1e-9)
