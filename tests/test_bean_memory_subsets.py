"""Exact user-specified two-character type/subset and nested table contracts."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.bean_pair_index import parse, pair_type, character_components, matches
from src.bean_memory_subsets import SubsetLedger, validate_code


class CompactHierarchyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "subsets.sqlite3"
        self.ledger = SubsetLedger(self.path)
        self.addCleanup(self.ledger.close)
        self.at = "2026-10-22T00:00:00Z"
        self.sha = "a" * 64

    def test_exact_pair_syntax_and_wildcard(self):
        self.assertEqual(parse("AAbb11_\\32", pattern=True),
                         ("AA", "bb", "11", "_\\", "32"))
        self.assertEqual(parse("A ab", pattern=True), ("A ", "ab"))
        self.assertEqual(pair_type("-/"), "symbol")
        self.assertEqual(character_components("01")["data_type_character"], "0")
        self.assertEqual(character_components("01")["subset_character"], "1")
        self.assertTrue(matches("A ab", "AAab010101010101"))
        for bad in ("Aa", "A2", "aA", "1B", "-1"):
            with self.assertRaises(ValueError):
                parse(bad, pattern=True)
        with self.assertRaises(ValueError):
            validate_code("A ")
        self.assertEqual(len("AAbb110102030405".encode("ascii")), 16)

    def test_one_character_is_type_next_is_subset_in_scoped_table(self):
        self.ledger.define_set("hair_color", "Hair color")
        self.ledger.define_type(set_id="hair_color",slot=1,
                                character="0",meaning="hair color")
        self.ledger.define_subset(set_id="hair_color",slot=1,
                                  code="01",meaning="blonde hair")
        self.ledger.define_set("hair_origin", "Hair curl origin")
        self.ledger.define_type(set_id="hair_origin",slot=1,
                                character="0",meaning="curl origin")
        self.ledger.define_subset(set_id="hair_origin",slot=1,
                                  code="04",meaning="natural curls")
        blonde=self.ledger.decode(set_id="hair_color",code="01")
        curls=self.ledger.decode(set_id="hair_origin",code="04")
        self.assertEqual(blonde[0]["type_meaning"], "hair color")
        self.assertEqual(blonde[0]["subset_meaning"], "blonde hair")
        self.assertEqual(curls[0]["subset_meaning"], "natural curls")
        with self.assertRaises(ValueError):
            self.ledger.define_subset(set_id="hair_color",slot=1,
                                      code="01",meaning="red hair")
        with self.assertRaises(ValueError):
            self.ledger.decode(set_id="hair_color",code="04")

    def test_parent_can_contain_several_independent_subset_sets(self):
        l=self.ledger
        l.define_set("root", "Root memory index")
        l.define_type(set_id="root",slot=1,character="A",meaning="top-level")
        l.define_subset(set_id="root",slot=1,code="AA",meaning="root type")
        l.define_type(set_id="root",slot=2,character="0",meaning="root subset")
        l.define_subset(set_id="root",slot=2,code="01",meaning="first subset")
        parent=l.add(set_id="root",code="AA01",meaning="original data reference",
                     stage="definition",available_at=self.at,
                     evidence_sha256=self.sha,source_episode="sample-1")
        for set_name, typ, pair, meaning in [
                ("length","B","BB","long hair"),
                ("texture","-","-/","curly hair")]:
            l.define_set(set_name,set_name)
            l.define_type(set_id=set_name,slot=1,character=typ,
                          meaning=set_name,parent_id=parent)
            l.define_subset(set_id=set_name,slot=1,code=pair,
                            meaning=meaning,parent_id=parent)
            l.add(set_id=set_name,code=pair,meaning=meaning,
                  stage="definition",available_at="2026-10-23T00:00:00Z",
                  evidence_sha256=self.sha,source_episode="sample-1",
                  parent_id=parent)
        self.assertEqual(l.children(parent,as_of=self.at),[])
        result=l.children(parent,as_of="2026-10-24T00:00:00Z")
        self.assertEqual({row["set_id"] for row in result},{"length","texture"})
        self.assertEqual({row["code"] for row in result},{"BB","-/"})
        self.assertEqual(l.verify()["nodes"],3)
        with self.assertRaises(sqlite3.IntegrityError):
            l.db.execute("UPDATE nodes SET code='AA' WHERE node_id=?", (parent,))
        with self.assertRaises(ValueError):
            l.add(set_id="root",code="AA01",meaning="reinterpretation",
                  stage="definition",available_at=self.at,
                  evidence_sha256=self.sha,source_episode="sample-1")


if __name__ == "__main__":
    unittest.main()
