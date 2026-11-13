import gzip,json,tempfile,unittest
from pathlib import Path
from src.bean_wide_history import DAY,candle_rows,collect

class WideHistoryTests(unittest.TestCase):
    def test_outside_requested_range_is_not_imported(self):
        rows=candle_rows([[0,1,2,1,2,10],[DAY,1,2,1,2,10]],DAY,2*DAY)
        self.assertEqual(list(rows),[DAY])
    def test_bad_geometry_and_conflicting_duplicates_rejected(self):
        for data in ([[[0,3,2,1,2,10]]],):
            with self.assertRaises(ValueError):candle_rows(data[0],0,DAY)
        with self.assertRaises(ValueError):candle_rows([[0,1,2,1,2,10],[0,1,3,1,3,10]],0,DAY)
    def test_full_catalog_attempts_missing_data_and_raw_sources_survive(self):
        catalog=[dict(id=p,base_currency=p.split('-')[0],quote_currency='USD',status='online') for p in ('A-USD','B-USD','C-USD')]
        def fetch(url):
            if url.endswith('/products'):return catalog
            if '/B-USD/' in url:raise ValueError('API down')
            if '/C-USD/' in url:return []
            return [[0,1,2,1,2,10],[2*DAY,1,2,1,2,10]]
        with tempfile.TemporaryDirectory() as d:
            r=collect(Path(d)/'out',start=0,end=3*DAY,fetcher=fetch,pause=0)
            self.assertEqual(r['attempted'],3);self.assertEqual(r['markets']['A-USD']['missing_days'],1)
            self.assertEqual(r['markets']['B-USD']['status'],'PARTIAL_API_FAILURE')
            self.assertEqual(r['markets']['C-USD']['status'],'NO_HISTORY')
            body=json.loads(gzip.decompress((Path(d)/'out'/'A-USD.json.gz').read_bytes()))
            self.assertEqual(len(body['candles']),2);self.assertIn('response',body['requests'][0])
