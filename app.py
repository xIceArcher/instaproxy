import json
import logging
import os
import re
import unicodedata
from typing import Any
from urllib.parse import quote_plus

import esprima
import requests
from flask import Flask, Response
from flask_caching import Cache


GRAPHQL_QUERY_ID = "27060936386852803"
DEFAULT_POST_TTL_SECONDS = 24 * 60 * 60
DEFAULT_USER_TTL_SECONDS = 7 * 24 * 60 * 60
DEFAULT_STORY_TTL_SECONDS = 24 * 60 * 60

EMBED_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "max-age=0",
    "referer": "https://www.instagram.com/",
    "sec-fetch-mode": "navigate",
    "upgrade-insecure-requests": "1",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.60 Safari/537.36",
    "viewport-width": "1280",
}

GRAPHQL_HEADERS = {
    **EMBED_HEADERS,
    "accept": "application/json, text/javascript, */*; q=0.01",
    "X-IG-App-ID": "936619743392459",
    "X-Requested-With": "XMLHttpRequest",
}

cache = Cache()


class InstagramService:
    STORY_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

    def __init__(self, session: requests.Session | None = None, proxy_url: str | None = None) -> None:
        self.session = session or requests.Session()
        self.proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

    # Public API

    def load_post(self, shortcode: str) -> dict[str, Any]:
        raw_post = self._load_raw_post(shortcode)
        if raw_post is None:
            raise LookupError(f"Instagram post not found: {shortcode}")
        return self._normalize_post(raw_post)

    def load_story(self, username: str, story_id: str) -> dict[str, Any]:
        shortcode = self._story_id_to_shortcode(story_id)
        raw_post = self._load_raw_post(shortcode)
        if raw_post is None:
            raise LookupError(f"Instagram story not found: {username}, {story_id}")
        return self._normalize_story(raw_post, story_id)

    def load_user(self, username: str) -> dict[str, Any]:
        response = self._request(f"https://www.instagram.com/{username}/embed", EMBED_HEADERS)
        if response is None:
            raise LookupError(f"Instagram user not found: {username}")

        match = re.search(r'contextJSON":"((?:\\.|[^"])*)"', response.text)
        if match:
            try:
                context = json.loads(json.loads(f'"{match.group(1)}"')).get("context", {})
                if isinstance(context, dict):
                    return self._build_user_payload(context, "owner_id")
            except json.JSONDecodeError:
                pass

        raise LookupError(f"Instagram user not found: {username}")

    # Network helpers

    def _request(self, url: str, headers: dict[str, str]) -> requests.Response | None:
        try:
            response = self.session.get(url, headers=headers, timeout=30, proxies=self.proxies or None)
            response.raise_for_status()
            return response
        except requests.RequestException:
            return None

    @classmethod
    def _story_id_to_shortcode(cls, story_id: str) -> str:
        story_id = str(story_id)
        value = int(story_id)
        shortcode = ""
        while value > 0:
            value, remainder = divmod(value, 64)
            shortcode = cls.STORY_ALPHABET[remainder] + shortcode
        return shortcode

    def _load_raw_post(self, shortcode: str) -> dict[str, Any] | None:
        return self._load_embed_post(shortcode) or self._load_graphql_post(shortcode)

    def _load_embed_post(self, shortcode: str) -> dict[str, Any] | None:
        response = self._request(
            f"https://www.instagram.com/p/{shortcode}/embed/captioned",
            EMBED_HEADERS,
        )
        if response is None:
            return None
        return self._extract_raw_post(self._parse_embed_payload(response.text))

    def _load_graphql_post(self, shortcode: str) -> dict[str, Any] | None:
        variables = quote_plus(json.dumps({"shortcode": shortcode}))
        url = f"https://www.instagram.com/graphql/query/?doc_id={GRAPHQL_QUERY_ID}&variables={variables}"
        response = self._request(url, GRAPHQL_HEADERS)
        if response is None:
            return None
        try:
            items = response.json().get("data", {}).get("xdt_api__v1__media__shortcode__web_info", {}).get("items")
        except ValueError:
            return None
        return items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else None

    # Parsing helpers

    @staticmethod
    def _parse_embed_payload(html: str) -> dict[str, Any] | None:
        for snippet in re.findall(r'(requireLazy\(\["TimeSliceImpl".*?\)\);)', html, re.S):
            if "shortcode_media" not in snippet and "full_name" not in snippet:
                continue

            try:
                tokens = esprima.tokenize(snippet)
            except Exception:
                continue

            for token in tokens:
                if "shortcode_media" not in token.value and "full_name" not in token.value:
                    continue
                try:
                    parsed = json.loads(json.loads(token.value))
                    if isinstance(parsed, dict):
                        return parsed
                except (TypeError, json.JSONDecodeError):
                    continue

        return None

    @staticmethod
    def _extract_raw_post(parsed: dict[str, Any] | None) -> dict[str, Any] | None:
        if not parsed:
            return None
        if "shortcode_media" in parsed:
            return parsed["shortcode_media"]

        gql_data = parsed.get("gql_data")
        if isinstance(gql_data, dict) and "shortcode_media" in gql_data:
            return gql_data["shortcode_media"]

        return None

    # Normalization helpers

    @staticmethod
    def _coerce_int(value: Any) -> Any:
        return int(value) if isinstance(value, str) and value.isdigit() else value

    @staticmethod
    def _normalize_user_data(source: dict[str, Any], id_field: str) -> dict[str, Any]:
        return {
            "full_name": unicodedata.normalize("NFC", source.get("full_name", "")),
            "username": source.get("username", ""),
            "pk": InstagramService._coerce_int(source.get(id_field)),
            "profile_pic_url": source.get("profile_pic_url", ""),
        }

    @staticmethod
    def _build_user_payload(source: dict[str, Any], id_field: str) -> dict[str, Any]:
        return {"user": InstagramService._normalize_user_data(source, id_field)}

    @staticmethod
    def _normalize_caption(raw_post: dict[str, Any]) -> str:
        if "edge_media_to_caption" in raw_post:
            edges = raw_post["edge_media_to_caption"].get("edges") or []
            if edges:
                return edges[0].get("node", {}).get("text", "") or ""
            return ""

        caption = raw_post.get("caption")
        if isinstance(caption, dict):
            return caption.get("text", "") or ""
        return ""

    @staticmethod
    def _normalize_media_child(child: dict[str, Any]) -> dict[str, Any]:
        child = child.get("node", child)

        if "video_versions" in child and child["video_versions"]:
            return {"video_versions": child["video_versions"]}
        if "image_versions2" in child and child["image_versions2"]:
            return {"image_versions2": child["image_versions2"]}
        raise ValueError("Unsupported Instagram media payload")

    @classmethod
    def _normalize_carousel_media(cls, raw_post: dict[str, Any]) -> list[dict[str, Any]]:
        sidecar = raw_post.get("edge_sidecar_to_children")
        if isinstance(sidecar, dict):
            edges = sidecar.get("edges") or []
            return [cls._normalize_media_child(edge) for edge in edges if isinstance(edge, dict)]

        carousel_media = raw_post.get("carousel_media")
        if isinstance(carousel_media, list) and carousel_media:
            return [cls._normalize_media_child(child) for child in carousel_media if isinstance(child, dict)]

        return [cls._normalize_media_child(raw_post)]

    @classmethod
    def _normalize_story_media(cls, raw_post: dict[str, Any]) -> dict[str, Any]:
        return cls._normalize_carousel_media(raw_post)[0]

    @classmethod
    def _normalize_post(cls, raw_post: dict[str, Any]) -> dict[str, Any]:
        shortcode = raw_post.get("shortcode") or raw_post.get("code")
        taken_at = raw_post.get("taken_at") or raw_post.get("taken_at_timestamp")
        owner = raw_post.get("owner") or raw_post.get("user") or {}
        if not isinstance(owner, dict):
            owner = {}

        return {
            "items": [
                {
                    "code": shortcode,
                    "user": cls._normalize_user_data(owner, "id"),
                    "caption": {"text": cls._normalize_caption(raw_post)},
                    "carousel_media": cls._normalize_carousel_media(raw_post),
                    "taken_at": cls._coerce_int(taken_at),
                }
            ]
        }

    @classmethod
    def _normalize_story(cls, raw_post: dict[str, Any], story_id: str) -> dict[str, Any]:
        media = cls._normalize_story_media(raw_post)
        taken_at = raw_post.get("taken_at") or raw_post.get("taken_at_timestamp")
        media_type = 1 if "image_versions2" in media else 2

        return {
            **media,
            "id": story_id if media_type == 2 else raw_post["id"],
            "media_type": media_type,
            "taken_at": cls._coerce_int(taken_at),
        }


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        CACHE_TYPE=os.environ.get("CACHE_TYPE", "RedisCache"),
        CACHE_REDIS_HOST=os.environ.get("REDIS_HOST", "localhost"),
        CACHE_REDIS_PORT=int(os.environ.get("REDIS_PORT", "6379")),
        CACHE_REDIS_DB=int(os.environ.get("REDIS_DB", "0")),
        PROXY_URL=os.environ.get("PROXY_URL", ""),
        CACHE_KEY_PREFIX="instaproxy:v2:",
        CACHE_DEFAULT_TIMEOUT=DEFAULT_POST_TTL_SECONDS,
    )
    cache.init_app(app)
    service = InstagramService(proxy_url=app.config["PROXY_URL"] or None)

    @cache.memoize(timeout=DEFAULT_POST_TTL_SECONDS)
    def get_post(shortcode: str) -> dict[str, Any]:
        return service.load_post(shortcode)

    @cache.memoize(timeout=DEFAULT_USER_TTL_SECONDS)
    def get_user(username: str) -> dict[str, Any]:
        return service.load_user(username)

    @cache.memoize(timeout=DEFAULT_STORY_TTL_SECONDS)
    def get_story(username: str, story_id: str) -> dict[str, Any]:
        return service.load_story(username, story_id)

    @app.get("/instagram/p/<shortcode>")
    def get_post_handler(shortcode: str) -> Response:
        return Response(json.dumps(get_post(shortcode)), mimetype="application/json")

    @app.get("/instagram/s/<username>/<story_id>")
    def get_story_handler(username: str, story_id: str) -> Response:
        return Response(json.dumps(get_story(username, story_id)), mimetype="application/json")

    @app.get("/instagram/u/<username>")
    def get_user_handler(username: str) -> Response:
        return Response(json.dumps(get_user(username)), mimetype="application/json")

    return app


app = create_app()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
