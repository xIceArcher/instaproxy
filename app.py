from abc import ABC, abstractmethod
import codecs
from datetime import timedelta
import json
import logging
import requests
import re
import os
import urllib.parse

import esprima
from flask import Flask
from flask.wrappers import Response
from logdecorator import log_on_start, log_on_error
import redis
from instagram_private_api import (
    Client, ClientCookieExpiredError, ClientLoginRequiredError, constants
)

constants.Constants.APP_VERSION = "410.0.0.0.96"
constants.Constants.ANDROID_VERSION = 33
constants.Constants.ANDROID_RELEASE = "13"
constants.Constants.PHONE_MANUFACTURER = "xiaomi"
constants.Constants.PHONE_DEVICE = "M2007J20CG"
constants.Constants.PHONE_MODEL = "surya"
constants.Constants.PHONE_DPI = "480dpi"
constants.Constants.PHONE_RESOLUTION = "1080x2400"
constants.Constants.PHONE_CHIPSET = "qcom"
constants.Constants.VERSION_CODE = "641123490"
constants.Constants.USER_AGENT = constants.Constants.USER_AGENT_FORMAT.format(**{
    'app_version': constants.Constants.APP_VERSION,
    'android_version': constants.Constants.ANDROID_VERSION,
    'android_release': constants.Constants.ANDROID_RELEASE,
    'brand': constants.Constants.PHONE_MANUFACTURER,
    'device': constants.Constants.PHONE_DEVICE,
    'model': constants.Constants.PHONE_MODEL,
    'dpi': constants.Constants.PHONE_DPI,
    'resolution': constants.Constants.PHONE_RESOLUTION,
    'chipset': constants.Constants.PHONE_CHIPSET,
    'version_code': constants.Constants.VERSION_CODE}
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

    def __init__(self, username: str, password: str, proxies: dict[str, str], settings_file_name: str) -> None:
        self.username: str = username
        self.password: str = password
        self.proxies: dict[str, str] = proxies
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
                    on_login=lambda x: self.login_callback(x), proxy=self.proxies['http'])
            else:
                with open(self.settings_file_name) as file_data:
                    cached_settings = json.load(file_data, object_hook=self.from_json)

                self.device_id = cached_settings.get('device_id')
                self.raw_api = Client(
                    self.username, self.password,
                    settings=cached_settings, proxy=self.proxies['http'])
        except (ClientCookieExpiredError, ClientLoginRequiredError):
            self.relogin()

    def relogin(self):
        self.raw_api = Client(
            self.username, self.password,
            device_id=self.device_id,
            on_login=lambda x: self.login_callback(x), proxy=self.proxies['http'])

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
        super().__init__(username, password, proxies, settings_file_name)

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
            headers=self.headers,
            proxies=self.proxies
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
        data = re.findall(r'(requireLazy\(\["TimeSliceImpl".*)', api_resp)
        for d in data:
            if d and "shortcode_media" in d:
                tokenized = esprima.tokenize(d)
                for token in tokenized:
                    try:
                        if "shortcode_media" in token.value:
                            # json.loads to unescape the JSON
                            return json.loads(json.loads(token.value))["gql_data"]
                    except (json.JSONDecodeError, KeyError):
                        continue


        # Finally fall back to the public GraphQL endpoint.
        return self._get_public_graphql_post_data(post_id)

    def _get_public_graphql_post_data(self, shortcode):
        query_id = "27060936386852803"
        url = (
            f"https://www.instagram.com/graphql/query/?doc_id={query_id}"
            f"&variables={urllib.parse.quote_plus(json.dumps({'shortcode': shortcode}))}"
        )
        headers = {
            **self.headers,
            "X-IG-App-ID": "936619743392459",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"https://www.instagram.com/p/{shortcode}/",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }

        try:
            resp = requests.get(url, headers=headers, timeout=30, proxies=self.proxies)
            if resp.status_code != 200:
                return None
            data = resp.json().get("data", {})
            media_info = data.get("xdt_api__v1__media__shortcode__web_info", {})
            items = media_info.get("items")
            if items and isinstance(items, list) and len(items) > 0:
                return {"shortcode_media": items[0]}
        except Exception:
            return None
        return None

    def _get_user_data(self, user_name):
        api_resp = requests.get(
            f"https://www.instagram.com/{user_name}/embed",
            headers=self.headers,
            proxies=self.proxies
        ).text

        data = re.findall(r'(requireLazy\(\["TimeSliceImpl".*)', api_resp)
        for d in data:
            if d and "full_name" in d:
                tokenized = esprima.tokenize(d)
                for token in tokenized:
                    if "full_name" in token.value:
                        try:
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
                        except (json.JSONDecodeError, KeyError):
                            continue

        # Try contextJSON as fallback
        context_data = self._get_user_data_from_context_json(api_resp)
        if context_data:
            return context_data

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

        raise Exception("Cannot get user")

    def _get_user_data_from_context_json(self, api_resp):
        """Extract user data from contextJSON in the embed page response."""
        try:
            # Find the contextJSON pattern
            match = re.search(r'contextJSON":"([^"\\]+(?:\\.[^"\\]*)*)"', api_resp)
            if match:
                json_str = match.group(1)
                # Unescape the JSON string (it has escaped quotes)
                unescaped = json_str.replace('\\"', '"')
                data = json.loads(unescaped)
                if "context" in data:
                    ctx = data["context"]
                    return {
                        "user": {
                            "full_name": ctx.get("full_name", ""),
                            "username": ctx.get("username", ""),
                            "pk": ctx.get("owner_id"),
                            "profile_pic_url": ctx.get("profile_pic_url", ""),
                        }
                    }
        except (json.JSONDecodeError, KeyError, AttributeError):
            pass
        return None

    def _transform_to_post(self, data):
        try:
            data = data["shortcode_media"]
        except KeyError:
            data = data["xdt_shortcode_media"]

        if "edge_media_to_caption" in data:
            description = data["edge_media_to_caption"]["edges"] or [{"node": {"text": ""}}]
            caption_text = description[0]["node"]["text"]
        else:
            caption_text = data.get("caption", {}).get("text", "")

        shortcode = data.get("shortcode") or data.get("code")
        taken_at = data.get("taken_at") or data.get("taken_at_timestamp")

        return {
            "items": [
                {
                    "code": shortcode,
                    "user": self._transform_to_user(data),
                    "caption": {
                        "text": caption_text
                    },
                    "carousel_media": self._transform_to_carousel_media(data),
                    "taken_at": taken_at,
                }
            ]
        }

    def _transform_to_reel(self, data):
        try:
            data = data["shortcode_media"]
        except KeyError:
            data = data["xdt_shortcode_media"]

        media = self._transform_to_carousel_media(data)[0]
        taken_at = data.get("taken_at") or data.get("taken_at_timestamp")
        meta = {
            "id": data["id"],
            "media_type": 1 if "image_versions2" in media else 2,
            "taken_at": taken_at,
        }

        return media | meta

    def _transform_to_user(self, data):
        owner = data.get("owner") or data.get("user") or {}
        ret = {
            "username": owner.get("username"),
        }

        if "id" in owner:
            ret["pk"] = owner["id"]

        if "profile_pic_url" in owner:
            ret["profile_pic_url"] = owner["profile_pic_url"]

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

        if "video_versions" in child and child["video_versions"]:
            return {"video_versions": child["video_versions"]}
        if "image_versions2" in child and child["image_versions2"]:
            return {"image_versions2": child["image_versions2"]}

        typename = child.get("__typename")
        if typename in ("GraphImage", "StoryImage", "XDTGraphImage"):
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
        elif typename in ("GraphVideo", "XDTGraphVideo"):
            return {
                "video_versions": [
                    {
                        "width": child["dimensions"]["height"],
                        "height": child["dimensions"]["width"],
                        "url": child["video_url"]
                    }
                ]
            }
        elif typename in ("StoryVideo", "GraphStoryVideo", "XDTStoryVideo"):
            raise Exception(f"{typename} type not supported")

        raise Exception(f"Unknown child type {typename}")

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


config_file_path = os.environ.get("CONFIG_PATH")
if not config_file_path:
    config_file_path = "./config.json"

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
