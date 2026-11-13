import json
from pathlib import Path
import tempfile
import unittest
from scripts.bean_retained_policy import verify

class RetainedPolicyTemporalTests(unittest.TestCase):
    def test_future_learned_state_cannot_enter_an_earlier_simulation(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            (p/'retention_manifest.json').write_text(json.dumps({'policies':[{'id':'x','knowledge_cutoff':'2026-10-24T00:00:00Z','file':'policy.gz','file_sha256':'a'*64}]}))
            with self.assertRaisesRegex(ValueError,'knowledge later'):
                verify(p,p,'x','2026-10-23T23:59:59Z')

    def test_changed_policy_artifact_is_rejected_before_core_load(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'policy.gz').write_bytes(b'changed')
            (p/'retention_manifest.json').write_text(json.dumps({'policies':[{'id':'x','knowledge_cutoff':'2026-10-24T00:00:00Z','file':'policy.gz','file_sha256':'a'*64}]}))
            with self.assertRaisesRegex(ValueError,'artifact changed'):
                verify(p,p,'x','2026-10-25T00:00:00Z')

if __name__=='__main__':unittest.main()
