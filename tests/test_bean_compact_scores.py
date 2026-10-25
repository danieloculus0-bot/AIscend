import tempfile,unittest
from pathlib import Path
from src.bean_compact_scores import CompactScores,pair,score

def metrics(value):return dict(net_return=value,drawdown=.1,start=100,end=200,markets=389,starting_equity=1000.)
def keys(identity='trial1'):return dict(method='linked',dataset='candles-sha',window='train-2025',seed=41,cost=.01,configuration='config-sha',evidence=identity)

class CompactScoreContracts(unittest.TestCase):
    def test_eight_pairs_are_reversible_and_exact_scores_keep_losses(self):
        with tempfile.TemporaryDirectory() as d:
            ledger=CompactScores(d);k=keys();r=ledger.record(keys=k,candidate=metrics(-.1),baseline=metrics(-.2),role='training',available_at=200,source='immutable-source')
            self.assertEqual(len(r['mnemonic']),16);self.assertEqual(ledger.decode(r['mnemonic']),dict(**k,reason='L0'))
            row=ledger.rank(9999999999,window='train-2025')[0]
            self.assertEqual(row['net_return'],-.1);self.assertAlmostEqual(row['incremental_return_bps'],1000.)
            self.assertTrue(r['new_credit']);ledger.close()
    def test_duplicate_receipt_cannot_mint_credit_and_index_rebuilds(self):
        with tempfile.TemporaryDirectory() as d:
            ledger=CompactScores(d);args=dict(keys=keys(),candidate=metrics(.1),baseline=metrics(.05),role='training',available_at=200,source='source')
            first=ledger.record(**args);self.assertFalse(ledger.record(**args)['new_credit']);ledger.close()
            (Path(d)/'scores.sqlite3').unlink();ledger=CompactScores(d)
            self.assertEqual(ledger.rank(9999999999,window='train-2025')[0]['mnemonic'],first['mnemonic'])
            self.assertFalse(ledger.record(**args)['new_record']);ledger.close()
    def test_future_and_evaluation_results_cannot_enter_training_rank(self):
        with tempfile.TemporaryDirectory() as d:
            ledger=CompactScores(d)
            ledger.record(keys=keys('future'),candidate=metrics(100),baseline=metrics(0),role='training',available_at=300,source='a')
            r=ledger.record(keys=keys('audit'),candidate=metrics(1000),baseline=metrics(0),role='evaluation',available_at=200,source='b')
            self.assertFalse(r['new_credit']);self.assertEqual(ledger.rank(250,window='train-2025',historical=True),[])
            self.assertEqual(ledger.rank(1000,window='train-2025'),[])
            self.assertEqual(len(ledger.rank(400,window='train-2025',historical=True)),1);ledger.close()
    def test_unmatched_and_conflicting_evidence_are_explicit(self):
        self.assertEqual(score(metrics(.1),dict(metrics(.2),markets=3))['reason'],'D0')
        with tempfile.TemporaryDirectory() as d:
            ledger=CompactScores(d);args=dict(keys=keys(),baseline=metrics(0),role='training',available_at=200,source='a')
            ledger.record(candidate=metrics(.1),**args)
            with self.assertRaises(ValueError):ledger.record(candidate=metrics(.2),**args)
            self.assertEqual(ledger.events.seq,1);ledger.close()
    def test_dictionary_overflow_cannot_wrap_and_corrupt_old_codes(self):
        self.assertNotEqual(pair(0),pair(3843))
        with self.assertRaises(OverflowError):pair(3844)
    def test_exact_ties_use_declared_method_order_not_dictionary_insertion(self):
        with tempfile.TemporaryDirectory() as d:
            ledger=CompactScores(d)
            for name in ('zulu','alpha'):
                ledger.record(keys=dict(keys(name),method=name),candidate=metrics(.1),baseline=metrics(0),role='training',available_at=200,source='source')
            self.assertEqual(ledger.rank(9999999999,window='train-2025')[0]['method'],'alpha');ledger.close()

if __name__=='__main__':unittest.main()
