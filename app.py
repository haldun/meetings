import datetime
import logging
import os
try:
  from cStringIO import StringIO
except ImportError:
  from StringIO import StringIO
from multiprocessing import Process, Queue, Pipe, Pool
import mimetypes

# Tornado imports
import tornado.auth
import tornado.httpserver
import tornado.ioloop
import tornado.options
import tornado.web

from tornado.options import define, options
from tornado.web import url, _O

import pylibmc
import yaml
import pymongo

import boto.s3.connection
from pymongo.objectid import ObjectId
from PIL import Image

# App imports
import forms
import pubnub
import pubnub_sync
import uimodules
import util

define("port", default=8888, type=int)
define("config_file", default="app_config.yml", help="app_config file")

class Application(tornado.web.Application):
  def __init__(self):
    handlers = [
      url(r'/', IndexHandler, name='index'),
      url(r'/auth/google', GoogleAuthHandler, name='auth_google'),
      url(r'/logout', LogoutHandler, name='logout'),
      url(r'/home', HomeHandler, name='home'),
      url(r'/new', NewRoomHandler, name='new'),
      url(r'/rooms/(?P<id>\w+)', RoomHandler, name='room'),
      url(r'/rooms/(?P<id>\w+)/say', NewMessageHandler, name='new_message'),
      url(r'/rooms/(?P<id>\w+)/upload', UploadHandler, name='upload'),
      url(r'/rooms/(?P<id>\w+)/delete', DeleteRoomHandler, name='delete_room'),
    ]
    settings = dict(
      debug=self.config.debug,
      login_url='/auth/google',
      static_path=os.path.join(os.path.dirname(__file__), "static"),
      template_path=os.path.join(os.path.dirname(__file__), 'templates'),
      xsrf_cookies=True,
      cookie_secret=self.config.cookie_secret,
      ui_modules=uimodules,
      pool=Pool(1),
    )
    tornado.web.Application.__init__(self, handlers, **settings)
    self.connection = pymongo.Connection()
    self.db = self.connection[self.config.mongodb_database]
    # TODO create indexes here

  @property
  def config(self):
    if not hasattr(self, '_config'):
      logging.debug("Loading app config")
      stream = file(options.config_file, 'r')
      self._config = tornado.web._O(yaml.load(stream))
    return self._config

  @property
  def memcache(self):
    if not hasattr(self, '_memcache'):
      self._memcache = pylibmc.Client(
        self.config.memcache_servers,
        binary=True, behaviors={"tcp_nodelay": True, "ketama": True})
    return self._memcache

  @property
  def pubnub(self):
    if not hasattr(self, '_pubnub'):
      self._pubnub = pubnub.Pubnub(self.config.pubnub_publish_key,
                                   self.config.pubnub_subscribe_key,
                                   self.config.pubnub_secret_key,
                                   self.config.pubnub_ssl_on)
    return self._pubnub

  @property
  def s3(self):
    if not hasattr(self, '_s3'):
      self._s3 = boto.s3.connection.S3Connection(
        self.config.aws_access_key_id, self.config.aws_secret_access_key)
    return self._s3


class BaseHandler(tornado.web.RequestHandler):
  @property
  def db(self):
    return self.application.db

  @property
  def memcache(self):
    return self.application.memcache

  @property
  def pubnub(self):
    return self.application.pubnub

  @property
  def config(self):
    return self.application.config

  @property
  def s3(self):
    return self.application.s3

  def get_current_user(self):
    user_id = self.get_secure_cookie('user_id')
    if not user_id:
      return None
    user = self.db.users.find_one({'_id': pymongo.objectid.ObjectId(user_id)})
    if user is None:
      return None
    return _O(user)

  @property
  def rooms(self):
    if not hasattr(self, '_rooms'):
      if not self.current_user:
        self._rooms = []
      else:
        self._rooms =(_O(r) for r in self.db.rooms.find())
    return self._rooms


class IndexHandler(BaseHandler):
  def get(self):
    self.render('index.html')


class GoogleAuthHandler(BaseHandler, tornado.auth.GoogleMixin):
  @tornado.web.asynchronous
  def get(self):
    if self.get_argument('openid.mode', None):
      self.get_authenticated_user(self.async_callback(self._on_auth))
      return
    self.authenticate_redirect()

  def _on_auth(self, guser):
    if not guser:
      raise tornado.web.HTTPError(500, "Google auth failed")
    user = self.db.users.find_one({'email': guser['email']})
    if user is None:
      user = {
        'email': guser['email'],
        'name': guser['name'],
      }
      self.db.users.insert(user)
    self.set_secure_cookie('user_id', str(user['_id']))
    self.redirect(self.reverse_url('home'))

class LogoutHandler(BaseHandler):
  def get(self):
    self.clear_cookie('user_id')
    self.redirect(self.reverse_url('index'))


class HomeHandler(BaseHandler):
  @tornado.web.authenticated
  def get(self):
    rooms = (_O(r) for r in self.db.rooms.find({'members': self.current_user._id}))
    self.render('home.html', rooms=rooms)


class NewRoomHandler(BaseHandler):
  @tornado.web.authenticated
  def get(self):
    form = forms.RoomForm()
    self.render('new.html', form=form)

  @tornado.web.authenticated
  def post(self):
    form = forms.RoomForm(self)
    if form.validate():
      room = _O(owner=self.current_user._id,
                admins=[self.current_user._id],
                members=[self.current_user._id],
                topic='')
      form.populate_obj(room)
      room.token = util.generate_token(32)
      self.db.rooms.insert(room)
      self.redirect(self.reverse_url('room', room._id))
    else:
      self.render('new.html', form=form)


class RoomHandler(BaseHandler):
  @tornado.web.authenticated
  def get(self, id):
    room = self.db.rooms.find_one({'_id': ObjectId(id)})
    if room is None:
      raise tornado.web.HTTPError(404)
    room = _O(room)
    if room.owner != self.current_user._id and self.current_user._id not in room.members:
      raise tornado.web.HTTPError(403)
    recent_messages = (_O(m) for m in self.db.messages.find({
      'room': room._id,
    }))
    files = (_O(m) for m in self.db.messages.find({
      'room': room._id,
      'type': {'$in': ['file', 'image']},
    }))
    self.render('room.html',
                room=room,
                recent_messages=recent_messages,
                files=files)


class NewMessageHandler(BaseHandler):
  @tornado.web.authenticated
  def post(self, id):
    """docstring for post"""
    room = self.db.rooms.find_one({'_id': ObjectId(id)})
    if room is None:
      raise tornado.web.HTTPError(404)
    room = _O(room)
    if room.owner != self.current_user._id and self.current_user._id not in room.members:
      raise tornado.web.HTTPError(403)
    content = self.get_argument('content')
    message = {
      'room': room._id,
      'user_id': self.current_user._id,
      'user_name': self.current_user.name or self.current_user.email,
      'type': 'text',
      'content': content,
      'created_at': datetime.datetime.utcnow(),
    }
    self.db.messages.insert(message)
    self.pubnub.publish({
      'channel': room.token,
      'message': {
        'content': message['content'],
        'user_id': str(message['user_id']),
        'user_name': message['user_name'],
        'type': 'text',
      }
    })
    self.finish()


class DeleteRoomHandler(BaseHandler):
  @tornado.web.authenticated
  def post(self, id):
    room = self.db.rooms.find_one({'_id': ObjectId(id)})
    if room is None:
      raise tornado.web.HTTPError(404)
    room = _O(room)
    if room.owner == self.current_user._id or self.current_user._id in room.admins:
      self.db.rooms.remove({'_id': room._id})
      self.db.messages.remove({'room': room._id})
      # TODO Remove s3 resources
      self.finish()
    else:
      raise tornado.web.HTTPError(403)


class Uploader(object):
  def __init__(self, config, user, room, file):
    self.config = config
    self.user = user
    self.room = room
    self.file = file

  def __call__(self):
    self.upload()

  def upload(self):
    logging.info("Started uploading...")

    # Is this an image?
    im = None

    try:
      im = Image.open(StringIO(self.file['body']))
    except:
      pass

    # Determine message type
    if im:
      message_type = 'image'
    else:
      message_type = 'file'

    # Thumbnail if image
    if im:
      im.thumbnail((300, 300), Image.ANTIALIAS)

    # Upload file to the S3
    bucket = self.get_bucket()
    key = boto.s3.key.Key(bucket)
    key.key = self.get_keyname()

    if im:
      key.set_metadata('Content-Type', 'image/%s' % im.format.lower())
    else:
      content_type, _ = mimetypes.guess_type(self.file['filename'])
      if content_type is not None:
        key.set_metadata('Content-Type', content_type)

    key.set_contents_from_string(self.file['body'])

    # Upload thumbnail
    if im:
      try:
        name, ext = os.path.splitext(self.file['filename'])
        thumbname = '/rooms/%s/%s_thumb%s' % (self.room._id, name, ext)
        thumbnail = StringIO()
        im.save(thumbnail, "JPEG")
        thumb_key = boto.s3.key.Key(bucket)
        thumb_key.key = thumbname
        thumb_key.set_contents_from_string(thumbnail.getvalue())
        thumbnail.close()
      except:
        traceback.print_exc()
    # Create a message
    content = '%s posted a file' % self.user.name or self.user.email
    message = {
      'room': self.room._id,
      'user_id': self.user._id,
      'user_name': self.user.name or self.user.email,
      'type': message_type,
      'filename': self.file['filename'],
      's3_key': key.key,
      'content': content,
      'created_at': datetime.datetime.utcnow(),
    }
    if message_type == 'image':
      message['s3_thumbnail_key'] = thumb_key.key
      message['size'] = im.size

    message_id = self.db.messages.insert(message)
    self.pubnub.publish({
      'channel': self.room.token,
      'message': {
        'content': message['content'],
        'user_id': str(message['user_id']),
        'user_name': message['user_name'],
        'type': message_type,
        'url': im and thumb_key.generate_url(3600) or key.generate_url(3600),
      }
    })

    logging.info("Finished uploading %s" % os.getpid())
    del self.file['body']

    try:
      del im
      del thumbnail
      del self.file
    except:
      pass
    return 0

  def get_bucket(self):
    conn = boto.s3.connection.S3Connection(
        self.config.aws_access_key_id, self.config.aws_secret_access_key)
    return conn.create_bucket(self.config.s3_bucket_name)

  def get_keyname(self):
    return '/rooms/%s/%s' % (self.room._id, self.file['filename'])

  @property
  def db(self):
    if not hasattr(self, '_db'):
      connection = pymongo.Connection()
      self._db = connection[self.config.mongodb_database]
    return self._db

  @property
  def pubnub(self):
    if not hasattr(self, '_pubnub'):
      self._pubnub = pubnub_sync.Pubnub(self.config.pubnub_publish_key,
                                        self.config.pubnub_subscribe_key,
                                        self.config.pubnub_secret_key,
                                        self.config.pubnub_ssl_on)
    return self._pubnub


class UploadHandler(BaseHandler):
  # Flash workaround here
  def initialize(self):
    is_flash = self.request.headers.get('X-Flash-Version', None)
    if is_flash:
      auth_token = self.get_argument('auth_token', None)
      xsrf = self.get_argument('_xsrf', None)
      self.request.headers['Cookie'] = 'auth_token=%s; _xsrf=%s' % (auth_token, xsrf)

  @tornado.web.asynchronous
  def post(self, id):
    room = self.db.rooms.find_one({'_id': ObjectId(id)})
    if room is None:
      raise tornado.web.HTTPError(404)
    room = _O(room)
    if room.owner != self.current_user._id and self.current_user._id not in room.members:
      raise tornado.web.HTTPError(403)
    file = self.request.files['file'][0]
    p = self.application.settings.get('pool')
    p.apply_async(Uploader(self.config, self.current_user, room, file),
                  [], callback=self.async_callback(self.on_getpid))
    del room

  def on_getpid(self, key):
    self.request.files = None
    self.write(dict(jsonrpc='2.0', result=None, id='id'))
    try:
      self.finish()
    except:
      pass

def main():
  tornado.options.parse_command_line()
  http_server = tornado.httpserver.HTTPServer(Application())
  http_server.listen(options.port)
  tornado.ioloop.IOLoop.instance().start()

if __name__ == '__main__':
  main()

