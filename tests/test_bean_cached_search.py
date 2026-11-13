import gzip,hashlib,json,os,tempfile,unittest
from pathlib import Path
from src.bean_multistep_environment import teaching_rows,withheld
from src.bean_wide_history import DAY,canonical
from src.bean_wide_multistep import train_models
from src.bean_cached_search import learn_cached
from src.bean_strategy_curriculum import Features
from tests.test_bean_strategy_curriculum import data

@unittest.skipUnless(os.environ.get('BEAN_CORE_DIR'),'Actual Core runs in the pinned GitHub job')
class CachedSearchContracts(unittest.TestCase):
    def test_cached_single_pass_matches_original_gradients_and_rejects_changed_source(self):
        f=Features(data());metadata={'base_increment':'.00000001','min_market_funds':'1'}
        product=next(f'ASSET{i}-USD' for i in range(100) if not withheld(f'ASSET{i}-USD'))
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);corpus=root/'corpus';(corpus/'teaching').mkdir(parents=True);path=corpus/'teaching'/(product+'.jsonl.gz')
            rows=list(teaching_rows(product,f,metadata,400*DAY,('cash','buy_hold','price_trend','donchian_breakout')))
            with gzip.GzipFile(filename=str(path),mode='wb',mtime=0) as dest:
                for row in rows:dest.write((canonical(row)+'\n').encode())
            (corpus/'artifacts_manifest.json').write_text(json.dumps({'files':{str(path.relative_to(corpus)):{'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}}}))
            models,stats,receipts=learn_cached(corpus,Path(os.environ['BEAN_CORE_DIR']),root/'cached',450*DAY,[41])
            original=root/'original';original.mkdir();baseline,_,_=train_models(Path(os.environ['BEAN_CORE_DIR']),{product:f},{product:metadata},original,400*DAY,'protocol')
            self.assertEqual(models['seed41_gamma0.0'].training_state(),baseline['immediate'].training_state())
            self.assertEqual(models['seed41_gamma0.99'].training_state(),baseline['linked'].training_state())
            self.assertEqual(stats['source_rows'],len(rows));self.assertTrue(all(x['full_state_recovered'] for x in receipts))
            _,purged,_=learn_cached(corpus,Path(os.environ['BEAN_CORE_DIR']),root/'purged',300*DAY,[41])
            self.assertEqual(purged['source_rows'],sum(r['outcome_at']+7*DAY<=300*DAY for r in rows))
            path.write_bytes(path.read_bytes()+b'changed')
            with self.assertRaises(ValueError):learn_cached(corpus,Path(os.environ['BEAN_CORE_DIR']),root/'bad',450*DAY,[41])

if __name__=='__main__':unittest.main()
