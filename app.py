import datetime
import logging
import os
try:
  from cStringIO import StringIO
except ImportError:
  from StringIO import StringIO
import mimetypes
import tempfile

# Tornado imports
import tornado.auth
import tornado.httpserver
import tornado.ioloop
import tornado.options
import tornado.web

from tornado.options import define, options
from tornado.web import url, _O

import hotqueue
import pylibmc
import yaml
import pymongo

import boto.s3.connection
from pymongo.objectid import ObjectId
from PIL import Image

# App imports
import forms
import pubnub
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
    )
    tornado.web.Application.__init__(self, handlers, **settings)
    self.connection = pymongo.Connection()
    self.db = self.connection[self.config.mongodb_database]
    self.upload_queue = hotqueue.HotQueue('upload', host='localhost', port=6379, db=0)
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
        self._rooms =(_O(r) for r in self.db.rooms.find({'members': self.current_user._id}))
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
    rooms = list(_O(r) for r in self.db.rooms.find({'members': self.current_user._id}))
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


def room_required(method):
  @tornado.web.authenticated
  def _wrapper(self, id, *args, **kwds):
    try:
      id = ObjectId(id)
    except:
      raise tornado.web.HTTPError(400)
    room = self.db.rooms.find_one({'_id': ObjectId(id)})
    if room is None:
      raise tornado.web.HTTPError(404)
    room = _O(room)
    if self.current_user._id not in room.members:
      raise tornado.web.HTTPError(403)
    self.room = room
    return method(self, *args, **kwds)
  return _wrapper


def room_admin_required(method):
  @room_required
  def _wrapper(self, *args, **kwds):
    if self.current_user._id != self.room.owner and self.current_user._id not in self.room.admins:
      raise tornado.web.HTTPError(403)
    return method(self, *args, **kwds)
  return _wrapper


class RoomHandler(BaseHandler):
  @room_required
  def get(self):
    recent_messages = (_O(m) for m in self.db.messages.find({
      'room': self.room._id,
    }))
    files = (_O(m) for m in self.db.messages.find({
      'room': self.room._id,
      'type': {'$in': ['file', 'image']},
    }))
    self.render('room.html',
                room=self.room,
                recent_messages=recent_messages,
                files=files)


class NewMessageHandler(BaseHandler):
  @room_required
  def post(self):
    content = self.get_argument('content')
    message = {
      'room': self.room._id,
      'user_id': self.current_user._id,
      'user_name': self.current_user.name or self.current_user.email,
      'type': 'text',
      'content': content,
      'created_at': datetime.datetime.utcnow(),
    }
    self.db.messages.insert(message)
    self.pubnub.publish({
      'channel': self.room.token,
      'message': {
        'content': message['content'],
        'user_id': str(message['user_id']),
        'user_name': message['user_name'],
        'type': 'text',
      }
    })
    self.finish()


class DeleteRoomHandler(BaseHandler):
  @room_admin_required
  def post(self):
    self.db.rooms.remove({'_id': self.room._id})
    self.db.messages.remove({'room': self.room._id})
    # TODO Remove s3 resources
    self.finish()

class UploadHandler(BaseHandler):
  # Flash workaround here
  def initialize(self):
    is_flash = self.request.headers.get('X-Flash-Version', None)
    if is_flash:
      auth_token = self.get_argument('auth_token', None)
      xsrf = self.get_argument('_xsrf', None)
      self.request.headers['Cookie'] = 'auth_token=%s; _xsrf=%s' % (auth_token, xsrf)

  @room_required
  def post(self):
    file = self.request.files['file'][0]
    tmpfile = tempfile.NamedTemporaryFile(delete=False)
    tmpfile.file.write(file['body'])
    tmpfile.file.close()
    self.application.upload_queue.put({
      'file': tmpfile.name,
      'filename': file['filename'],
      'room_id': self.room._id,
      'user_id': self.current_user._id,
      'username': self.current_user.name or self.current_user.email,
      'room_token': self.room.token,
    })
    self.write(dict(jsonrpc='2.0', result=None, id='id'))

def main():
  tornado.options.parse_command_line()
  http_server = tornado.httpserver.HTTPServer(Application())
  http_server.listen(options.port)
  tornado.ioloop.IOLoop.instance().start()

if __name__ == '__main__':
  main()

