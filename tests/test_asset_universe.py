import time
import unittest

from src.asset_universe import CoinbaseAssetUniverse, UniverseAsset


class FakeUniverse(CoinbaseAssetUniverse):
    def __init__(self):
        super().__init__(
            timeout=0.1,
            batch_size=3,
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

    def test_discovers_and_scores_every_quote_currency(self):
        data=FakeUniverse().collect()
        self.assertTrue(data["available"])
        self.assertEqual(data["product_count"],3)
        products={row["product"] for row in data["top"]}
        self.assertEqual(products,{"AAA-USD","BBB-USDC","CCC-EUR"})

    def test_cold_start_batch_spreads_across_catalog(self):
        universe=FakeUniverse()
        products=[
            {"id":"AAA-USD"},{"id":"BBB-USD"},{"id":"CCC-USD"},{"id":"DDD-USD"},{"id":"EEE-USD"},
        ]
        batch=universe._next_batch(products)
        self.assertEqual([row["id"] for row in batch],["AAA-USD","CCC-USD","EEE-USD"])

    def test_buy_top_prefers_positive_signal_over_large_negative_move(self):
        class RankingUniverse(FakeUniverse):
            def _scan_product(self, product):
                score={"AAA-USD":-0.80,"BBB-USDC":0.20,"CCC-EUR":0.60}[product["id"]]
                return UniverseAsset(
                    product=product["id"],
                    base=product["base_currency"],
                    quote=product["quote_currency"],
                    price=1.0,
                    return_5m=0.0,
                    return_1h=score/10.0,
                    return_6h=0.0,
                    volume_ratio=1.0,
                    spread_bps=1.0,
                    score=score,
                    scanned_at=time.time(),
                )

        data=RankingUniverse().collect()
        self.assertEqual(data["top"][0]["product"],"AAA-USD")
        self.assertEqual(data["buy_top"][0]["product"],"CCC-EUR")
        self.assertEqual(len(data["scored"]),3)

    def test_scan_result_is_cached_between_ui_refreshes(self):
        universe=FakeUniverse()
        first=universe.collect()
        second=universe.collect()
        self.assertEqual(first,second)

    def test_priority_product_bypasses_asset_cache(self):
        universe=FakeUniverse()
        counts={}

        original=universe._scan_product
        def counted(product):
            product_id=product["id"]
            counts[product_id]=counts.get(product_id,0)+1
            return original(product)

        universe._scan_product=counted
        universe.collect()
        self.assertEqual(counts.get("BBB-USDC"),1)

        universe.collect(priority_products=["BBB-USDC"])
        self.assertEqual(counts.get("BBB-USDC"),2)
        self.assertEqual(counts.get("AAA-USD"),1)
        self.assertEqual(counts.get("CCC-EUR"),1)

    def test_liquidity_factor_hammers_thin_markets(self):
        universe=FakeUniverse()
        self.assertLess(universe._liquidity_factor(0.04),0.20)
        self.assertLess(universe._liquidity_factor(0.40),0.70)
        self.assertEqual(universe._liquidity_factor(1.0),1.0)
        self.assertEqual(universe._liquidity_factor(3.0),1.0)

    def test_fresh_listing_does_not_need_six_hours_of_candles(self):
        universe=FakeUniverse()

        def fresh_get(path):
            if "candles" in path:
                return [[1, 0.9, 1.1, 1.0, 1.0, 4.0]]
            if "ticker" in path:
                return {"price":"1.0"}
            if "book" in path:
                return {"bids":[["0.99","1",1]],"asks":[["1.01","1",1]]}
            raise AssertionError(path)

        universe._get_json=fresh_get
        asset=universe._scan_product(
            {"id":"SHIT-EUR","base_currency":"SHIT","quote_currency":"EUR"}
        )
        self.assertIsNotNone(asset)
        self.assertEqual(asset.product,"SHIT-EUR")
        self.assertEqual(asset.return_6h,0.0)


if __name__ == "__main__":
    unittest.main()
