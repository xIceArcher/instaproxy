import json
import os
import sys
from pathlib import Path
import unittest


os.environ["CACHE_TYPE"] = "SimpleCache"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app


def fetch_shortcode(shortcode):
    with app.test_client() as client:
        response = client.get(f"/instagram/p/{shortcode}")
        assert response.status_code == 200, response.data.decode("utf-8", errors="replace")
        return json.loads(response.data.decode("utf-8"))


class InstagramPostEndpointTests(unittest.TestCase):
    def assert_single_media_post(self, payload, shortcode, username, media_key):
        self.assertIn("items", payload)
        self.assertEqual(len(payload["items"]), 1)

        item = payload["items"][0]
        self.assertEqual(item["code"], shortcode)
        self.assertEqual(item["user"]["username"], username)
        self.assertIn("caption", item)
        self.assertIn("taken_at", item)
        self.assertIsInstance(item["taken_at"], int)

        self.assertIn("carousel_media", item)
        self.assertEqual(len(item["carousel_media"]), 1)
        self.assertIn(media_key, item["carousel_media"][0])

    def test_single_image_post(self):
        payload = fetch_shortcode("DXuPylBE1zZ")
        self.assert_single_media_post(
            payload,
            "DXuPylBE1zZ",
            "aoyamanagisa_official",
            "image_versions2",
        )
        self.assertNotIn("video_versions", payload["items"][0]["carousel_media"][0])

    def test_single_video_post(self):
        payload = fetch_shortcode("DYBYaWXzWBO")
        self.assert_single_media_post(
            payload,
            "DYBYaWXzWBO",
            "lovelive_superstar_staff",
            "video_versions",
        )
        self.assertNotIn("image_versions2", payload["items"][0]["carousel_media"][0])

    def test_multiple_images_post(self):
        payload = fetch_shortcode("DYQuvTyCaFt")

        self.assertIn("items", payload)
        self.assertEqual(len(payload["items"]), 1)

        item = payload["items"][0]
        self.assertEqual(item["code"], "DYQuvTyCaFt")
        self.assertEqual(item["user"]["username"], "doradora_comic")
        self.assertEqual(len(item["carousel_media"]), 2)
        for media in item["carousel_media"]:
            self.assertIn("image_versions2", media)
            self.assertNotIn("video_versions", media)

    def test_mixed_media_post(self):
        payload = fetch_shortcode("DZXoywOE_M2")

        self.assertIn("items", payload)
        self.assertEqual(len(payload["items"]), 1)

        item = payload["items"][0]
        self.assertEqual(item["code"], "DZXoywOE_M2")
        self.assertEqual(item["user"]["username"], "sayuridate_official")
        self.assertEqual(len(item["carousel_media"]), 3)

        self.assertIn("video_versions", item["carousel_media"][0])
        self.assertIn("image_versions2", item["carousel_media"][1])
        self.assertIn("image_versions2", item["carousel_media"][2])

    def test_unobtainable_single_video_post(self):
        payload = fetch_shortcode("DZue5tNtYQX")

        self.assertIn("items", payload)
        self.assertEqual(len(payload["items"]), 1)

        item = payload["items"][0]
        self.assertEqual(item["code"], "DZue5tNtYQX")
        self.assertEqual(item["user"]["username"], "iamcoffeeartist")
        self.assertEqual(item["caption"]["text"], "")
        self.assertEqual(len(item["carousel_media"]), 1)
        self.assertIn("video_versions", item["carousel_media"][0])
        self.assertNotIn("image_versions2", item["carousel_media"][0])


if __name__ == "__main__":
    unittest.main()
