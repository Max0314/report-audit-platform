import asyncio
import unittest

from app import storage
from app.object_store import ObjectStoreError, check_key


class FakeStore:
    def __init__(self):
        self.values = {}

    def put_bytes(self, key, data, content_type=""):
        self.values[key] = data

    def get_bytes(self, key):
        return self.values.get(key)

    def delete(self, key):
        self.values.pop(key, None)


class StorageTests(unittest.TestCase):
    def tearDown(self):
        storage.reset_store_for_tests()

    def test_persist_upload_uses_isolated_key_and_sanitizes_filename(self):
        fake = FakeStore()
        storage._store = fake
        storage._initialized = True
        key = asyncio.run(
            storage.persist_upload("review", "review_123", "../../测试.xlsx", b"excel")
        )
        self.assertEqual(key, "uploads/review/review_123/测试.xlsx")
        self.assertEqual(fake.values[key], b"excel")

    def test_rejects_path_traversal_key(self):
        with self.assertRaises(ObjectStoreError):
            check_key("uploads/../secret.xlsx")

    def test_startup_probe_is_removed(self):
        fake = FakeStore()
        storage._store = fake
        storage._initialized = True
        asyncio.run(storage.check_storage_ready())
        self.assertEqual(fake.values, {})


if __name__ == "__main__":
    unittest.main()
