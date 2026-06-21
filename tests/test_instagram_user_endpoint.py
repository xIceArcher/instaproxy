import json
import os
import sys
from pathlib import Path
import unittest


os.environ["CACHE_TYPE"] = "SimpleCache"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app


def fetch_user(username):
    with app.test_client() as client:
        response = client.get(f"/instagram/u/{username}")
        assert response.status_code == 200, response.data.decode("utf-8", errors="replace")
        return json.loads(response.data.decode("utf-8"))


class InstagramUserEndpointTests(unittest.TestCase):
    def assert_user(self, payload, full_name, username, pk):
        self.assertEqual(set(payload.keys()), {"user"})
        user = payload["user"]
        self.assertEqual(user["full_name"], full_name)
        self.assertEqual(user["username"], username)
        self.assertEqual(user["pk"], pk)
        self.assertIsInstance(user["profile_pic_url"], str)
        self.assertTrue(user["profile_pic_url"])

    def test_aoyamanagisa_official(self):
        payload = fetch_user("aoyamanagisa_official")
        self.assert_user(
            payload,
            "青山なぎさ",
            "aoyamanagisa_official",
            43854027228,
        )

    def test_lovelive_superstar_staff(self):
        payload = fetch_user("lovelive_superstar_staff")
        self.assert_user(
            payload,
            "ラブライブ！スーパースター!! / Liella! 公式",
            "lovelive_superstar_staff",
            30739612676,
        )

    def test_doradora_comic(self):
        payload = fetch_user("doradora_comic")
        self.assert_user(
            payload,
            "ドラゴンエイジ編集部",
            "doradora_comic",
            62915730120,
        )


if __name__ == "__main__":
    unittest.main()
