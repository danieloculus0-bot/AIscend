"""Full Coinbase USD/USDC research coverage and non-promotion gates."""
import unittest

from src.universe_research import (
    aggregate_shards, discover_public_spot, run_shard, shard_for,
)


def product(base, quote="USD", **changes):
    return {
        "id": f"{base}-{quote}", "base_currency": base, "quote_currency": quote,
        "status": "online", "product_type": "SPOT", **changes,
    }


class CoinbaseUniverseResearchTests(unittest.TestCase):
    def setUp(self):
        self.catalog = [
            product("BTC"), product("ETH", "USDC"),
            product("NEWCOIN"), product("NEWCOIN", "USDC"),
            product("LONGTAIL"), product("SLOW"),
            {**product("OFF"), "status": "offline"},
            product("MARGIN", "EUR"), product("USD", "USDC"),
            {**product("LOCKED"), "trading_disabled": True},
            {**product("POST"), "post_only": True},
            {**product("FUTURE"), "product_type": "FUTURE"},
        ]

    def test_full_catalog_has_no_major_coin_preference_or_usd_dedup(self):
        c = discover_public_spot(fetcher=lambda _: self.catalog)
        self.assertEqual(
            c["eligible_quote_markets"],
            ["BTC-USD", "ETH-USDC", "LONGTAIL-USD", "NEWCOIN-USD",
             "NEWCOIN-USDC", "SLOW-USD"])
        self.assertEqual(c["eligible_count"], 6)
        self.assertEqual(c["public_spot_count"], 11)
        self.assertFalse(c["account_executable_verified"])
        self.assertEqual(c["exclusions"]["requires_other_quote_route"], 1)
        self.assertEqual(c["exclusions"]["not_market_orderable"], 2)

    def test_catalog_does_not_treat_other_quotes_as_usdc_funded(self):
        c = discover_public_spot(fetcher=lambda _: [
            product("TOKEN", "BTC"), product("TOKEN", "EUR"),
            product("TOKEN", "USDC")])
        self.assertEqual(c["eligible_quote_markets"], ["TOKEN-USDC"])
        self.assertEqual(c["public_spot_count"], 3)

    def test_catalog_rejects_ambiguous_or_invalid_payload(self):
        with self.assertRaises(ValueError):
            discover_public_spot(fetcher=lambda _: {"products": []})
        c = discover_public_spot(fetcher=lambda _: [
            {"id": "DUP-USD", "base_currency": "OTHER", "quote_currency": "USD"},
            product("GOOD")])
        self.assertEqual(c["eligible_quote_markets"], ["GOOD-USD"])

    def test_all_shards_cover_each_market_once(self):
        products = discover_public_spot(fetcher=lambda _: self.catalog)[
            "eligible_quote_markets"]
        for shards in (1, 2, 8, 16):
            selection = [[p for p in products if shard_for(p, shards) == i]
                         for i in range(shards)]
            self.assertEqual(sorted(p for group in selection for p in group),
                             products)

    def _reports(self, *, shards=3):
        def historical(product_id, *, days):
            self.assertEqual(days, 21)
            return {"product": product_id}

        def judge(bars, *, fee_bps):
            self.assertEqual(fee_bps, 60)
            return {
                "status": "OOS_PASS", "champion": "trend",
                "training": {"trend": {"trades": 5, "net_return": 0.08}},
                "holdout": {"trend": {"trades": 5, "net_return": 0.03}},
            }

        return [
            run_shard(shards=shards, shard_index=i, workers=1,
                      catalog_fetcher=lambda _: self.catalog,
                      history_fetcher=historical, evaluator=judge)
            for i in range(shards)
        ]

    def test_shard_reports_every_eligible_product_with_actual_assumptions(self):
        r = self._reports(shards=1)[0]
        self.assertEqual(r["attempted_count"], 6)
        self.assertEqual(r["status_counts"], {"OOS_PASS": 6})
        self.assertFalse(r["promotion_authorized"])

    def test_complete_aggregate_does_not_equate_a_researched_win_to_profit(self):
        merged = aggregate_shards(self._reports())
        self.assertEqual(merged["evaluated_count"], 6)
        self.assertEqual(merged["data_coverage"], "COMPLETE")
        self.assertEqual(merged["status_counts"], {"OOS_PASS": 6})
        self.assertEqual(merged["shards_verified"], 3)
        self.assertFalse(merged["promotion_authorized"])

    def test_aggregate_detects_missing_shard_and_changed_catalog(self):
        reports = self._reports()
        with self.assertRaises(ValueError):
            aggregate_shards(reports[:-1])
        reports[0]["catalog_hash"] = "not-the-same-catalog"
        with self.assertRaises(ValueError):
            aggregate_shards(reports)

    def test_aggregate_cannot_silently_omit_a_market(self):
        reports = self._reports()
        chosen = next(r for r in reports if r["products"])
        chosen["products"].pop(next(iter(chosen["products"])))
        with self.assertRaises(ValueError):
            aggregate_shards(reports)

    def test_invalid_shard_options_fail_closed(self):
        with self.assertRaises(ValueError):
            run_shard(shards=0, catalog_fetcher=lambda _: [])
        with self.assertRaises(ValueError):
            run_shard(shards=4, shard_index=4,
                      catalog_fetcher=lambda _: [])


if __name__ == "__main__":
    unittest.main()
