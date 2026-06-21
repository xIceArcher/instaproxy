import json
import os
import sys
from pathlib import Path
import unittest


os.environ["CACHE_TYPE"] = "SimpleCache"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app


def fetch_story(username, story_id):
    with app.test_client() as client:
        response = client.get(f"/instagram/s/{username}/{story_id}")
        assert response.status_code == 200, response.data.decode("utf-8", errors="replace")
        return json.loads(response.data.decode("utf-8"))


@unittest.skipUnless(os.environ.get("RUN_STORY_TESTS") == "1", "story fixtures are ephemeral")
class InstagramStoryEndpointTests(unittest.TestCase):
    def assert_story(self, payload, story_id, media_key, media_type):
        self.assertEqual(payload["id"], story_id)
        self.assertEqual(payload["media_type"], media_type)
        self.assertIn("taken_at", payload)
        self.assertIsInstance(payload["taken_at"], int)
        self.assertIn(media_key, payload)

    def test_image_story(self):
        payload = fetch_story("a", "3923622852062620502")
        self.assert_story(payload, "3923622852062620502_62061860156", "image_versions2", 1)
        self.assertNotIn("video_versions", payload)

    def test_video_story(self):
        payload = fetch_story("a", "3924250130580605223")
        self.assert_story(payload, "3924250130580605223", "video_versions", 2)
        self.assertNotIn("image_versions2", payload)


if __name__ == "__main__":
    unittest.main()
