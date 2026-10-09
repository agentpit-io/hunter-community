import unittest
from unittest.mock import Mock, patch
from app.services import us_daily_fallback as f, market_source


class DailyFallbackTests(unittest.TestCase):
    def setUp(self):
        f._CACHE.clear()
        f._NEXT_REQUEST = 0

    def chart(self):
        return {"timestamp": [1728432000, 1728518400], "indicators": {"quote": [{
            "open": [10, None], "high": [12, 12], "low": [9, 9],
            "close": [11, 11], "volume": [100, 200]}]}}

    def test_missing_price_is_skipped(self):
        rows = f.parse_chart(self.chart())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['open'], 10)

    def test_invalid_and_nonfinite_price_is_rejected(self):
        row = {'open': 10, 'high': 12, 'low': 9, 'close': 11, 'volume': 100}
        for key, value in [('open', 0), ('close', float('nan')), ('high', 8), ('volume', -1)]:
            self.assertFalse(f.valid_bar(dict(row, **{key: value})))

    def test_otc_symbol_cache_and_host_fallback(self):
        bad = Mock(status_code=404)
        import requests
        bad.raise_for_status.side_effect = requests.HTTPError()
        good = Mock(status_code=200)
        good.json.return_value = {'chart': {'result': [self.chart()]}}
        with patch.object(f.requests, 'get', side_effect=[bad, good]) as get, patch.object(f.time, 'sleep'):
            self.assertEqual(len(f.daily('NODB.US', 250)), 1)
            self.assertIn('/chart/NODB', get.call_args.args[0])
            self.assertEqual(len(f.daily('NODB', 1)), 1)
            self.assertEqual(get.call_count, 2)

    def test_rate_limit_does_not_switch_hosts(self):
        with patch.object(f.requests, 'get', return_value=Mock(status_code=429)) as get:
            self.assertEqual(f.daily('RVRF'), [])
            self.assertEqual(get.call_count, 1)

    def test_sina_empty_uses_backup(self):
        import akshare
        with patch.object(akshare, 'stock_us_daily', return_value=None), patch.object(f, 'daily', return_value=[{'close': 1}]) as backup:
            self.assertEqual(market_source.us_daily('RVRF.US', 250), [{'close': 1}])
            backup.assert_called_once_with('RVRF.US', 250)

    def test_class_suffix_not_truncated(self):
        self.assertEqual(market_source._us_symbol('BRK.B.US'), 'BRK.B')
        with patch.object(f.requests, 'get', return_value=Mock(status_code=429)) as get:
            f.daily('BRK.B.US')
            self.assertIn('/chart/BRK-B', get.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
