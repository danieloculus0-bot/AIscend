import unittest

from web_app import app


class WebAppTests(unittest.TestCase):
    def test_index_loads(self):
        client = app.test_client()
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"AIscend", response.data)


if __name__ == "__main__":
    unittest.main()
