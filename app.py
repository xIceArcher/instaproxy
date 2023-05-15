from flask import Flask
from flask.wrappers import Response
import json
import codecs
import os.path
import redis
from datetime import timedelta
from instagram_private_api import (
    Client, ClientCookieExpiredError, ClientLoginRequiredError
)

class API:
    b64alphabetmap = {a: i for i, a in enumerate('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_')}

    def __init__(self, username: str, password: str, settings_file_name: str) -> None:
        self.username: str = username
        self.password: str = password
        self.settings_file_name: str = settings_file_name
        self.device_id: str
        self.raw_api: Client

        try:
            self.login()
        except Exception as e:
            print(e)
            exit(9)

    def get_post(self, shortcode):
        return self.perform_api_action(lambda: self.raw_api.media_info(self.shortcode_to_id(shortcode)))

    def get_story(self, user_name, story_id):
        for item in self.get_stories(user_name)['items']:
            if item['id'].startswith(story_id):
                return item

    def get_stories(self, user_name):
        user_id = self.get_user(user_name)['user']['pk']
        return self.perform_api_action(lambda: self.raw_api.reels_media([user_id]))['reels'][str(user_id)]

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
            id += API.b64alphabetmap[c] * 64 ** i
        return id

class CachedAPI(API):
    def __init__(self, username: str, password: str, settings_file_name: str, host: str, port: int, db: int) -> None:
        super().__init__(username, password, settings_file_name)

        self.cache = redis.Redis(host=host, port=port, db=db)

    def get_post(self, shortcode):
        return self.with_cache(f'instagram:post:{shortcode}', lambda: super(CachedAPI, self).get_post(shortcode), ex=timedelta(hours=24))

    def get_user(self, user_name):
        return self.with_cache(f'instagram:user:{user_name}', lambda: super(CachedAPI, self).get_user(user_name), ex=timedelta(weeks=1))

    def get_story(self, user_name, story_id):
        return self.with_cache(f'instagram:story:{story_id}', lambda: super(CachedAPI, self).get_story(user_name, story_id), ex=timedelta(hours=24))

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

with open(config_file_path) as f:
    cfg = json.load(f)
    instagram_cfg = cfg['instagram']
    redis_cfg = cfg['redis']

api = CachedAPI(
    username=instagram_cfg['username'], password=instagram_cfg['password'], settings_file_name=instagram_cfg['settings_cache_file_path'],
    host=redis_cfg['host'], port=redis_cfg['port'], db=redis_cfg['db'],
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
