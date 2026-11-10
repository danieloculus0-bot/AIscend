"""Offline tests for public OHLCV collection, without a network or API credentials."""
import json
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs,urlsplit
from research import coinbase_public_paired_sixhour as c

class TestPublicSnapshot(unittest.TestCase):
    def fixture(self,missing=False,repeat=False,broken=False):
        def getter(url):
            u=urlsplit(url)
            self.assertEqual(u.hostname,"api.exchange.coinbase.com")
            self.assertTrue(u.path.endswith("/candles"))
            q=parse_qs(u.query)
            self.assertEqual(q["granularity"],[str(c.BAR)])
            t0=int(datetime.fromisoformat(q["start"][0].replace("Z","+00:00")).timestamp())
            t1=int(datetime.fromisoformat(q["end"][0].replace("Z","+00:00")).timestamp())
            out=[[t,98.,103.,100.,101.,50000.] for t in range(t0,t1,c.BAR)]
            if missing and "BTC" in u.path:out.pop()
            if repeat:out.append(out[-1])
            if broken:out[0][0]+=.5
            return out
        return getter
    def kw(self):
        return dict(start=c.START,end=c.START+12*c.BAR,now=lambda:c.START+50*c.BAR,sleep=lambda t:None)
    def test_complete_source_hashes_and_preservation(self):
        rows,manifest=c.snapshot(fetcher=self.fixture(),**self.kw())
        self.assertEqual(len(rows),24)
        self.assertEqual(manifest["rows_sha256"],c.digest(rows))
        self.assertEqual(manifest["bars_per_product"],12)
        self.assertEqual(tuple(manifest["products"]),c.PRODUCTS)
        self.assertTrue(manifest["public_only"])
        self.assertFalse(manifest["prospective_profits_verified"])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"snapshot"
            c.write_snapshot(root,rows,manifest)
            self.assertEqual(len((root/"public_btc_eth.jsonl").read_text().splitlines()),24)
            self.assertEqual(json.loads((root/"source_manifest.json").read_text())["rows_sha256"],manifest["rows_sha256"])
            with self.assertRaises(FileExistsError):c.write_snapshot(root,rows,manifest)
    def test_bad_data_fail_closed(self):
        for kind in ("missing","repeat","broken"):
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                c.snapshot(fetcher=self.fixture(**{kind:True}),**self.kw())
    def test_open_candle_blocked_preflight(self):
        with self.assertRaisesRegex(ValueError,"OPEN_CANDLE"):
            c.snapshot(start=c.START,end=c.START+12*c.BAR,now=lambda:c.START+5*c.BAR,
                       fetcher=lambda url:self.fail("NETWORK_MUST_NOT_RUN"))
    def test_product_restriction(self):
        with self.assertRaisesRegex(ValueError,"UNREGISTERED_PRODUCT"):
            c.request_url("UNRELATED-USD",c.START,c.START+c.BAR)

if __name__=="__main__":unittest.main()
