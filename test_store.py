"""
test_store.py

Unit tests for the key-value store, including a persistence /
crash-recovery test. Run with:

    python3 -m pytest test_store.py -v

or, dependency-free:

    python3 test_store.py
"""

import os
import tempfile
import unittest

from store import KVStore
from index import Index


class TestIndex(unittest.TestCase):
    def test_set_get_delete(self):
        idx = Index()
        idx.set("a", 1)
        idx.set("b", 2)
        self.assertEqual(idx.get("a"), 1)
        self.assertTrue(idx.exists("b"))
        self.assertTrue(idx.delete("a"))
        self.assertIsNone(idx.get("a"))
        self.assertFalse(idx.delete("a"))

    def test_last_write_wins(self):
        idx = Index()
        idx.set("a", 1)
        idx.set("a", 2)
        self.assertEqual(idx.get("a"), 2)
        self.assertEqual(len(idx), 1)


class TestKVStore(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp()
        os.close(fd)
        os.remove(self.path)  # KVStore should be fine creating a fresh file
        self.db = KVStore(self.path)

    def tearDown(self):
        self.db.close()
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_set_get(self):
        self.assertEqual(self.db.execute("SET a 1"), "OK")
        self.assertEqual(self.db.execute("GET a"), "1")
        self.assertEqual(self.db.execute("GET missing"), "")

    def test_del_exists(self):
        self.db.execute("SET a 1")
        self.assertEqual(self.db.execute("EXISTS a"), "(integer) 1")
        self.assertEqual(self.db.execute("DEL a"), "(integer) 1")
        self.assertEqual(self.db.execute("DEL a"), "(integer) 0")
        self.assertEqual(self.db.execute("EXISTS a"), "(integer) 0")

    def test_mset_mget(self):
        self.db.execute("MSET x 1 y 2")
        self.assertEqual(self.db.execute("MGET x y z"), "1\n2\n")

    def test_incr_decr(self):
        self.assertEqual(self.db.execute("INCR n"), "(integer) 1")
        self.assertEqual(self.db.execute("INCR n"), "(integer) 2")
        self.assertEqual(self.db.execute("DECR n"), "(integer) 1")

    def test_hash(self):
        self.assertEqual(self.db.execute("HSET h f1 v1"), "(integer) 1")
        self.assertEqual(self.db.execute("HSET h f1 v2"), "(integer) 0")
        self.assertEqual(self.db.execute("HGET h f1"), "v2")
        self.assertEqual(self.db.execute("HGET h nofield"), "")

    def test_list(self):
        self.db.execute("RPUSH l a")
        self.db.execute("RPUSH l b")
        self.db.execute("LPUSH l z")
        self.assertEqual(self.db.execute("LRANGE l 0 -1"), "z\na\nb\nEND")

    def test_range(self):
        self.db.execute("SET a 1")
        self.db.execute("SET b 2")
        self.db.execute("SET z 3")
        self.assertEqual(self.db.execute("RANGE a b"), "a\nb\nEND")

    def test_ttl_expiry(self):
        self.db.execute("SET a 1")
        self.assertEqual(self.db.execute("TTL a"), "(integer) -1")
        self.db.execute("EXPIRE a 100")
        ttl = self.db.execute("TTL a")
        self.assertTrue(ttl.startswith("(integer)"))
        self.assertNotEqual(ttl, "(integer) -1")

    def test_expire_missing_key(self):
        self.assertEqual(self.db.execute("EXPIRE nokey 10"), "(integer) 0")

    def test_transaction_commit(self):
        self.db.execute("BEGIN")
        self.db.execute("SET t 1")
        self.assertEqual(self.db.execute("GET t"), "")  # not committed yet
        self.db.execute("COMMIT")
        self.assertEqual(self.db.execute("GET t"), "1")

    def test_transaction_abort(self):
        self.db.execute("BEGIN")
        self.db.execute("SET t 1")
        self.db.execute("ABORT")
        self.assertEqual(self.db.execute("GET t"), "")

    def test_flushdb(self):
        self.db.execute("SET a 1")
        self.db.execute("FLUSHDB")
        self.assertEqual(self.db.execute("GET a"), "")


class TestPersistence(unittest.TestCase):
    """Data must survive a process restart by replaying the log."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp()
        os.close(fd)
        os.remove(self.path)

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_restart_recovers_state(self):
        db1 = KVStore(self.path)
        db1.execute("SET a 1")
        db1.execute("HSET h f v")
        db1.execute("RPUSH l x")
        db1.close()

        db2 = KVStore(self.path)  # simulates a fresh process replaying the log
        self.assertEqual(db2.execute("GET a"), "1")
        self.assertEqual(db2.execute("HGET h f"), "v")
        self.assertEqual(db2.execute("LRANGE l 0 -1"), "x\nEND")
        db2.close()

    def test_torn_last_write_is_discarded_not_fatal(self):
        db1 = KVStore(self.path)
        db1.execute("SET a 1")
        db1.execute("SET b 2")
        db1.close()

        # simulate a crash mid-append: chop bytes off the end, no trailing \n
        with open(self.path, "rb") as f:
            data = f.read()
        last_nl = data.rfind(b"\n", 0, len(data) - 1)
        torn = data[:last_nl + 1] + data[last_nl + 1:-5]
        with open(self.path, "wb") as f:
            f.write(torn)

        db2 = KVStore(self.path)  # must not crash on corrupted tail
        self.assertEqual(db2.execute("GET a"), "1")
        self.assertEqual(db2.execute("GET b"), "")  # torn write discarded
        db2.execute("SET c 3")
        db2.close()

        db3 = KVStore(self.path)  # new write after recovery must persist too
        self.assertEqual(db3.execute("GET c"), "3")
        db3.close()


if __name__ == "__main__":
    unittest.main()
