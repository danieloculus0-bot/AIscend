"""Paper-only Python watch continuously invokes real BEAN Core, no orders."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.forward_paper import main


class ForwardPaperWatchTests(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.manifest=Path(self.dir.name)/"manifest.json"
        self.manifest.write_text(json.dumps([
            {"product":"BTC-USD","policy":"trend","training_only_frozen":True}
        ]))
        self.core=Path(self.dir.name)/"BEAN"

    def test_watch_repeats_real_feedback_not_fake_or_live_orders(self):
        arguments=["--manifest",str(self.manifest),"--bean-core",str(self.core),
                   "--watch","--max-cycles","2","--interval-seconds","30"]
        with (patch("scripts.forward_paper.observe_and_score",
                   return_value={"paper_only":True,"products":{}}) as observe,
              patch("src.bean_core_paper_learning.learn_from_paper",
                   return_value={"engine":"BEAN Core LearningRuntime/SpongeTD",
                                 "q_updates":5}) as learn,
              patch("scripts.forward_paper.time.sleep") as sleep,
              patch("builtins.print")):
            self.assertEqual(main(arguments),0)
        self.assertEqual(observe.call_count,2)
        self.assertEqual(learn.call_count,2)
        sleep.assert_called_once()
        self.assertEqual(learn.call_args.kwargs["core_dir"],self.core)

    def test_watch_without_core_refuses_to_pretend_learning(self):
        with self.assertRaises(SystemExit):
            main(["--manifest",str(self.manifest),"--watch"])

    def test_single_paper_cycle_does_not_instantiate_fake_bean(self):
        with (patch("scripts.forward_paper.observe_and_score",
                    return_value={"paper_only":True}) as observe,
              patch("src.bean_core_paper_learning.learn_from_paper") as learn,
              patch("builtins.print")):
            self.assertEqual(main(["--manifest",str(self.manifest)]),0)
        observe.assert_called_once()
        learn.assert_not_called()


if __name__=="__main__":
    unittest.main()
