from abc import ABC, abstractmethod
import codecs
from datetime import timedelta
import json
import logging
import requests
import re
import os

import esprima
from flask import Flask
from flask.wrappers import Response
from logdecorator import log_on_start, log_on_error
import orjson
import redis
from selectolax.parser import HTMLParser
from instagram_private_api import (
    Client, ClientCookieExpiredError, ClientLoginRequiredError
)


class AbstractInstagramAPI(ABC):
    @abstractmethod
    def get_post(self, shortcode): ...

    @abstractmethod
    def get_story(self, user_name, story_id): ...

    @abstractmethod
    def get_stories(self, user_name): ...

    @abstractmethod
    def get_user(self, user_name): ...


class InstagramAPIByPrivateAPI(AbstractInstagramAPI):
    b64alphabetmap = {a: i for i, a in enumerate('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_')}

    def __init__(self, username: str, password: str, settings_file_name: str) -> None:
        self.username: str = username
        self.password: str = password
        self.settings_file_name: str = settings_file_name
        self.device_id: str
        self.raw_api: Client

        self.login()

    @log_on_start(logging.INFO, "Called {callable.__qualname__:s}: {shortcode}")
    @log_on_error(logging.ERROR, "Called {callable.__qualname__:s} failed: {shortcode}, err: {e!r}", on_exceptions=Exception)
    def get_post(self, shortcode):
        return self.perform_api_action(lambda: self.raw_api.media_info(self.shortcode_to_id(shortcode)))

    @log_on_start(logging.INFO, "Called {callable.__qualname__:s}: {user_name}, {story_id}")
    @log_on_error(logging.ERROR, "Called {callable.__qualname__:s} failed: {user_name}, {story_id}, err: {e!r}", on_exceptions=Exception)
    def get_story(self, user_name, story_id):
        for item in self.get_stories(user_name)['items']:
            if item['id'].startswith(story_id):
                return item

    @log_on_start(logging.INFO, "Called {callable.__qualname__:s}: {user_name}")
    @log_on_error(logging.ERROR, "Called {callable.__qualname__:s} failed: {user_name}, err: {e!r}", on_exceptions=Exception)
    def get_stories(self, user_name):
        user_id = self.get_user(user_name)['user']['pk']
        return self.perform_api_action(lambda: self.raw_api.reels_media([user_id]))['reels'][str(user_id)]

    @log_on_start(logging.INFO, "Called {callable.__qualname__:s}: {user_name}")
    @log_on_error(logging.ERROR, "Called {callable.__qualname__:s} failed: {user_name}, err: {e!r}", on_exceptions=Exception)
    def get_user(self, user_name):
        return self.perform_api_action(lambda: self.raw_api.username_info(user_name))

    def perform_api_action(self, f):
        try:
            return f()
        except (ClientCookieExpiredError, ClientLoginRequiredError):
            self.relogin()
            return f()

    def login(self):
        try:
            if not os.path.isfile(self.settings_file_name):
                self.raw_api = Client(
                    self.username, self.password,
                    on_login=lambda x: self.login_callback(x))
            else:
                with open(self.settings_file_name) as file_data:
                    cached_settings = json.load(file_data, object_hook=self.from_json)

                self.device_id = cached_settings.get('device_id')
                self.raw_api = Client(
                    self.username, self.password,
                    settings=cached_settings)
        except (ClientCookieExpiredError, ClientLoginRequiredError):
            self.relogin()

    def relogin(self):
        self.raw_api = Client(
            self.username, self.password,
            device_id=self.device_id,
            on_login=lambda x: self.login_callback(x))

    def login_callback(self, api):
        self.device_id = api.settings.get('device_id')
        with open(self.settings_file_name, 'w') as outfile:
            json.dump(api.settings, outfile, default=self.to_json)

    def to_json(self, python_object):
        if isinstance(python_object, bytes):
            return {'__class__': 'bytes',
                    '__value__': codecs.encode(python_object, 'base64').decode()}
        raise TypeError(repr(python_object) + ' is not JSON serializable')

    def from_json(self, json_object):
        if '__class__' in json_object and json_object['__class__'] == 'bytes':
            return codecs.decode(json_object['__value__'].encode(), 'base64')
        return json_object

    def shortcode_to_id(self, shortcode):
        id = 0
        for i, c in enumerate(shortcode[::-1]):
            id += InstagramAPIByPrivateAPI.b64alphabetmap[c] * 64 ** i
        return id


class InstagramAPIByEmbedAPI(InstagramAPIByPrivateAPI):
    def __init__(self, username: str, password: str, proxies: dict[str, str], settings_file_name: str) -> None:
        super().__init__(username, password, settings_file_name)

        self.headers = {
            "authority": "www.instagram.com",
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
            "accept-language": "en-US,en;q=0.9",
            "cache-control": "max-age=0",
            "sec-fetch-mode": "navigate",
            "upgrade-insecure-requests": "1",
            "referer": "https://www.instagram.com/",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.60 Safari/537.36",
            "viewport-width": "1280",
        }
        self.proxies = proxies

    @log_on_start(logging.INFO, "Called {callable.__qualname__:s}: {shortcode}")
    def get_post(self, shortcode):
        try:
            return self._get_post(shortcode)
        except:
            return super().get_post(shortcode)

    @log_on_error(logging.ERROR, "Called {callable.__qualname__:s} failed: {shortcode}, err: {e!r}", on_exceptions=Exception)
    def _get_post(self, shortcode):
        post = self._transform_to_post(self._get_post_data(shortcode))
        post["items"][0]["user"] = self.get_user(post["items"][0]["user"]["username"])["user"]

        return post

    @log_on_start(logging.INFO, "Called {callable.__qualname__:s}: {user_name}, {story_id}")
    def get_story(self, user_name, story_id):
        try:
            return self._get_story(user_name, story_id)
        except:
            return super().get_story(user_name, story_id)

    @log_on_error(logging.ERROR, "Called {callable.__qualname__:s} failed: {user_name}, {story_id}, err: {e!r}", on_exceptions=Exception)
    def _get_story(self, user_name, story_id):
        if isinstance(story_id, str):
            story_id = int(story_id)

        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        shortcode = ""
        while story_id > 0:
            story_id, remainder = divmod(story_id, 64)
            shortcode = alphabet[remainder] + shortcode

        return self._transform_to_reel(self._get_post_data(shortcode))

    @log_on_start(logging.INFO, "Called {callable.__qualname__:s}: {user_name}")
    def get_user(self, user_name):
        try:
            return self._get_user(user_name)
        except:
            return super().get_user(user_name)

    @log_on_error(logging.ERROR, "Called {callable.__qualname__:s} failed: {user_name}, err: {e!r}", on_exceptions=Exception)
    def _get_user(self, user_name):
        return self._get_user_data(user_name)

    def _get_post_data(self, post_id):
        api_resp = requests.get(
            f"https://www.instagram.com/p/{post_id}/embed/captioned",
            headers=self.headers
        ).text

        # additionalDataLoaded
        data = re.findall(
            r"window\.__additionalDataLoaded\('extra',(.*)\);<\/script>", api_resp
        )
        if data:
            gql_data = json.loads(data[0])
            if gql_data and gql_data.get("shortcode_media"):
                return gql_data

        # TimeSliceImpl
        data = re.findall(r'<script>(requireLazy\(\["TimeSliceImpl".*)<\/script>', api_resp)
        if data and "shortcode_media" in data[0]:
            tokenized = esprima.tokenize(data[0])
            for token in tokenized:
                if "shortcode_media" in token.value:
                    # json.loads to unescape the JSON
                    return json.loads(json.loads(token.value))["gql_data"]

        # GraphQL
        params = {
            "query_hash": "b3055c01b4b222b8a47dc12b090e4e64",
            "variables": orjson.dumps({"shortcode": post_id}).decode(),
        }

        if self.proxies:
            try:
                response = requests.get(
                    "https://www.instagram.com/graphql/query/", params=params, proxies=self.proxies
                )
                return response.json()["data"]
            except:
                pass

        response = requests.get(
            "https://www.instagram.com/graphql/query/", params=params
        )

        return response.json()["data"]

    def _parse_embed(self, shortcode, html: str) -> dict:
        tree = HTMLParser(html)
        typename = "GraphImage"
        display_url = tree.css_first(".EmbeddedMediaImage")
        if not display_url:
            typename = "GraphVideo"
            display_url = tree.css_first("video")
        if not display_url:
            return {"error": "Not found"}
        display_url = display_url.attrs["src"]
        username = tree.css_first(".UsernameText").text()

        # Remove div class CaptionComments, CaptionUsername
        caption_comments = tree.css_first(".CaptionComments")
        if caption_comments:
            caption_comments.remove()
        caption_username = tree.css_first(".CaptionUsername")
        if caption_username:
            caption_username.remove()

        caption_text = ""
        caption = tree.css_first(".Caption")
        if caption:
            for node in caption.css("br"):
                node.replace_with("\n")
            caption_text = caption.text().strip()

        return {
            "shortcode_media": {
                "shortcode": shortcode,
                "owner": {"username": username},
                "node": {"__typename": typename, "display_resources": [{"config_width": 0, "config_height": 0, "src": display_url}]},
                "edge_media_to_caption": {"edges": [{"node": {"text": caption_text}}]},
                "dimensions": {"height": 1, "width": 1},
                "video_blocked": "WatchOnInstagram" in html,
            }
        }

    def _get_user_data(self, user_name):
        api_resp = requests.get(
            f"https://www.instagram.com/{user_name}/embed",
            headers=self.headers
        ).text

        data = re.findall(r'<script>(requireLazy\(\["TimeSliceImpl".*)<\/script>', api_resp)
        if data and "full_name" in data[0]:
            tokenized = esprima.tokenize(data[0])
            for token in tokenized:
                if "full_name" in token.value:
                    # json.loads to unescape the JSON
                    data = json.loads(json.loads(token.value))["context"]
                    return {
                        "user": {
                            "full_name": data["full_name"],
                            "username": data["username"],
                            "pk": data["owner_id"],
                            "profile_pic_url": data["graphql_media"][0]["shortcode_media"]["owner"]["profile_pic_url"] if "graphql_media" in data and len(data["graphql_media"]) > 0 else ""
                        }
                    }

        if self.proxies:
            try:
                response = requests.get(
                    "https://i.instagram.com/api/v1/users/web_profile_info", params={"username": user_name}, headers={"User-Agent": "iphone_ua", "x-ig-app-id": "936619743392459"}, proxies=self.proxies
                )
                data = response.json()["data"]["user"]
                return {
                    "user": {
                        "full_name": data.get("full_name", ""),
                        "username": data.get("username", user_name),
                        "pk": int(data["id"]),
                        "profile_pic_url": data.get("profile_pic_url", ""),
                    }
                }
            except:
                pass

        return {}

    def _transform_to_post(self, data):
        data = data["shortcode_media"]
        description = data["edge_media_to_caption"]["edges"] or [{"node": {"text": ""}}]

        return {
            "items": [
                {
                    "code": data["shortcode"],
                    "user": self._transform_to_user(data),
                    "caption": {
                        "text": description[0]["node"]["text"]
                    },
                    "carousel_media": self._transform_to_carousel_media(data),
                    "taken_at": data["taken_at_timestamp"]
                }
            ]
        }

    def _transform_to_reel(self, data):
        data = data["shortcode_media"]

        media = self._transform_to_carousel_media(data)[0]
        meta = {
            "id": data["id"],
            "media_type": 1 if "image_versions2" in media else 2,
            "taken_at": data["taken_at_timestamp"],
        }

        return media | meta

    def _transform_to_user(self, data):
        ret = {
            "username": data["owner"]["username"],
        }

        if "id" in data["owner"]:
            ret["pk"] = data["owner"]["id"]

        if "profile_pic_url" in data["owner"]:
            ret["profile_pic_url"] = data["owner"]["profile_pic_url"]

        return ret

    def _transform_to_carousel_media(self, post) -> list:
        if "edge_sidecar_to_children" in post:
            return [
                self._transform_gql_child(child) for child in post["edge_sidecar_to_children"]["edges"]
            ]

        return [
            self._transform_gql_child(post)
        ]

    def _transform_gql_child(self, child):
        child = child.get("node", child)

        if child["__typename"] in ("GraphImage", "StoryImage"):
            return {
                "image_versions2": {
                    "candidates": [
                        {
                            "width": display_resource["config_width"],
                            "height": display_resource["config_height"],
                            "url": display_resource["src"],
                        }
                        for display_resource in child["display_resources"]
                    ]
                }
            }
        elif child["__typename"] in ("GraphVideo",):
            return {
                "video_versions": [
                    {
                        "width": child["dimensions"]["height"],
                        "height": child["dimensions"]["width"],
                        "url": child["video_url"]
                    }
                ]
            }
        elif child["__typename"] == "StoryVideo":
            raise Exception("StoryVideo type not supported")

        raise Exception(f"Unknown child type {child['__typename']}")

class InstagramAPIByCache(InstagramAPIByEmbedAPI):
    def __init__(self, username: str, password: str, proxies: dict[str, str], settings_file_name: str, host: str, port: int, db: int) -> None:
        super().__init__(username, password, proxies, settings_file_name)

        self.cache = redis.Redis(host=host, port=port, db=db)

    @log_on_start(logging.INFO, "Called {callable.__qualname__:s}: {shortcode}")
    @log_on_error(logging.ERROR, "Called {callable.__qualname__:s} failed: {shortcode}, err: {e!r}", on_exceptions=Exception)
    def get_post(self, shortcode):
        return self.with_cache(f'instagram:post:{shortcode}', lambda: super(InstagramAPIByCache, self).get_post(shortcode), ex=timedelta(hours=24))

    @log_on_start(logging.INFO, "Called {callable.__qualname__:s}: {user_name}")
    @log_on_error(logging.ERROR, "Called {callable.__qualname__:s} failed: {user_name}, err: {e!r}", on_exceptions=Exception)
    def get_user(self, user_name):
        return self.with_cache(f'instagram:user:{user_name}', lambda: super(InstagramAPIByCache, self).get_user(user_name), ex=timedelta(weeks=1))

    @log_on_start(logging.INFO, "Called {callable.__qualname__:s}: {user_name}, {story_id}")
    @log_on_error(logging.ERROR, "Called {callable.__qualname__:s} failed: {user_name}, {story_id}, err: {e!r}", on_exceptions=Exception)
    def get_story(self, user_name, story_id):
        return self.with_cache(f'instagram:story:{story_id}', lambda: super(InstagramAPIByCache, self).get_story(user_name, story_id), ex=timedelta(hours=24))

    def with_cache(self, key, f, ex=None):
        val = self.cache.get(key)
        if val is not None:
            return json.loads(val)

        val = f()
        if ex:
            self.cache.set(key, json.dumps(val), ex=ex)
        else:
            self.cache.set(key, json.dumps(val))

        return val


config_file_path = './config.json'
logging.basicConfig(level=logging.INFO, format='%(message)s')

with open(config_file_path) as f:
    cfg = json.load(f)
    instagram_cfg = cfg['instagram']
    redis_cfg = cfg['redis']

api = InstagramAPIByCache(
    username=instagram_cfg['username'], password=instagram_cfg['password'], proxies=instagram_cfg['proxies'], settings_file_name=instagram_cfg['settings_cache_file_path'],
    host=redis_cfg['host'], port=redis_cfg['port'], db=redis_cfg['db']
)
app = Flask(__name__)

@app.route("/instagram/p/<shortcode>")
def get_post_handler(shortcode):
    return Response(json.dumps(api.get_post(shortcode)), content_type='application/json')

@app.route("/instagram/s/<user_name>")
def get_stories_handler(user_name):
    return Response(json.dumps(api.get_stories(user_name)), content_type='application/json')

@app.route("/instagram/s/<user_name>/<story_id>")
def get_story_handler(user_name, story_id):
    return Response(json.dumps(api.get_story(user_name, story_id)), content_type='application/json')

@app.route("/instagram/u/<user_name>")
def get_user_handler(user_name):
    return Response(json.dumps(api.get_user(user_name)), content_type='application/json')
