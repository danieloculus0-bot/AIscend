import unittest

from src.asset_universe import CoinbaseAssetUniverse


class FakeUniverse(CoinbaseAssetUniverse):
    def __init__(self):
        super().__init__(
            timeout=0.1,
            batch_size=2,
            product_refresh_seconds=900,
            asset_cache_seconds=600,
            scan_interval_seconds=55,
        )

    def _get_json(self, path):
        if path == "/products":
            return [
                {"id":"AAA-USD","base_currency":"AAA","quote_currency":"USD","status":"online","trading_disabled":False},
                {"id":"BBB-USDC","base_currency":"BBB","quote_currency":"USDC","status":"online","trading_disabled":False},
                {"id":"CCC-EUR","base_currency":"CCC","quote_currency":"EUR","status":"online","trading_disabled":False},
            ]
        if "candles" in path:
            rows=[]
            for i in range(100):
                price=100.0+i
                rows.append([i,price-1,price+1,price,price,10+i])
            return list(reversed(rows))
        if "ticker" in path:
            return {"price":"199.0"}
        if "book" in path:
            return {"bids":[["198.9","1",1]],"asks":[["199.1","1",1]]}
        raise AssertionError(path)


class AssetUniverseTests(unittest.TestCase):
    def setUp(self):
        FakeUniverse._products=[]
        FakeUniverse._products_until=0.0
        FakeUniverse._cursor=0
        FakeUniverse._cache={}
        FakeUniverse._last_result=None
        FakeUniverse._next_scan_at=0.0

    def test_discovers_and_scores_usd_usdc_products(self):
        data=FakeUniverse().collect()
        self.assertTrue(data["available"])
        self.assertEqual(data["product_count"],2)
        products={row["product"] for row in data["top"]}
        self.assertEqual(products,{"AAA-USD","BBB-USDC"})

    def test_scan_result_is_cached_between_ui_refreshes(self):
        universe=FakeUniverse()
        first=universe.collect()
        second=universe.collect()
        self.assertEqual(first,second)


if __name__ == "__main__":
    unittest.main()
