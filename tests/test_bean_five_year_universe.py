"""All-market five-year catalog/shard/retained-ledger regressions."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.bean_five_year_universe import capture_catalog, aggregate, main
from scripts.bean_five_year_walkforward import main as simulate_main
from scripts.bean_five_year_ledger import audit_db
from src.universe_research import shard_for


MARKETS = sorted(["BTC-USD","ETH-USD","SOL-USD","DOGE-USD","ADA-USD",
                  "SHIB-USD","XRP-USD","AVAX-USD","LINK-USD"])


def snapshot(path):
    return capture_catalog(path, discover=lambda: {
        "eligible_quote_markets": list(MARKETS)})


def make_shards(folder, *, count=3):
    c = snapshot(folder/"catalog.json")
    filenames = []
    for i in range(count):
        members = {p: {"product":p, "status":"INSUFFICIENT_HISTORY",
                       "history_status":"PARTIAL", "promotion_authorized":False}
                   for p in MARKETS if shard_for(p, count) == i}
        record={"schema":"aiscend.bean.five_year.walkforward.v2",
                "catalog_hash":c["catalog_hash"],"catalog_size":len(MARKETS),
                "eligible_quote_markets":MARKETS, "shards":count,"shard_index":i,
                "attempted_count":len(members),"markets":members,
                "promotion_authorized":False}
        out=folder/("shard-"+str(i)+".json")
        out.write_text(json.dumps(record))
        filenames.append(out)
    return filenames


class CompleteUniverseResearchTests(unittest.TestCase):
    def test_catalog_contains_all_symbols_no_three_coin_shortlist(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            result=snapshot(root/"catalog.json")
            self.assertEqual(result["count"], len(MARKETS))
            self.assertIn("SHIB-USD",result["eligible_quote_markets"])
            self.assertEqual(len(result["catalog_hash"]),64)
            self.assertFalse(result["promotion_authorized"])

    def test_aggregate_persists_all_normal_incomplete_markets(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            shards=make_shards(root)
            summary=aggregate(root/"catalog.json",shards,
                              root/"bean.sqlite3",root/"summary.json")
            self.assertEqual(summary["catalog_size"],len(MARKETS))
            self.assertEqual(summary["outcomes_saved"],len(MARKETS))
            self.assertEqual(summary["status_counts"]["INSUFFICIENT_HISTORY"],
                             len(MARKETS))
            self.assertEqual(audit_db(root/"bean.sqlite3")["outcome_count"],
                             len(MARKETS))
            # Duplicate experiments remain immutable, not duplicated.
            again=aggregate(root/"catalog.json",shards,
                            root/"bean.sqlite3",root/"summary.json")
            self.assertEqual(again["outcomes_saved"],len(MARKETS))

    def test_a_missing_shard_is_not_rebranded_partial(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            shards=make_shards(root)
            with self.assertRaisesRegex(ValueError,"MISSING_OR_DUPLICATED_SHARDS"):
                aggregate(root/"catalog.json",shards[:-1],
                          root/"db.sqlite3",root/"summary.json")

    def test_missing_asset_is_not_silently_omitted(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            shards=make_shards(root)
            data=json.loads(shards[0].read_text())
            if not data["markets"]:
                self.skipTest("Need nonempty shard")
            del data["markets"][next(iter(data["markets"]))]
            shards[0].write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,"SHARD_MISSING_OR_EXTRA_MARKETS"):
                aggregate(root/"catalog.json",shards,
                          root/"db.sqlite3",root/"summary.json")

    def test_bridge_snapshot_is_read_only_and_all_market(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            summary=aggregate(root/"catalog.json",make_shards(root),
                              root/"bean.sqlite3",root/"summary.json")
            self.assertEqual(main(["--bridge-input",str(root/"summary.json"),
                                   "--bridge-output",str(root/"bridge.json")]),0)
            radar=json.loads((root/"bridge.json").read_text())["market_radar"]
            self.assertEqual(radar["all_market_attempts"],len(MARKETS))
            self.assertEqual(radar["prediction"],"HISTORICAL_ONLY_NOT_LIVE")

    def test_sharded_research_attempts_all_products_in_catalog(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            snapshot(root/"catalog.json")
            attempted=[]
            def no_history(product, *, years):
                attempted.append(product)
                return {"product":product, "expected_bars":7200,
                        "received_bars":0,"data":{}}
            with patch("scripts.bean_five_year_walkforward.fetch_five_year",
                       side_effect=no_history):
                for i in range(3):
                    self.assertEqual(simulate_main([
                        "--catalog",str(root/"catalog.json"),
                        "--shards","3","--shard-index",str(i),
                        "--output",str(root/("r"+str(i)+".json"))]),0)
            self.assertEqual(sorted(attempted),MARKETS)
            self.assertNotEqual(len(attempted),3)


if __name__ == "__main__":
    unittest.main()
