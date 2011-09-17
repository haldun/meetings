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
import tornado.escape
import tornado.httpserver
import tornado.ioloop
import tornado.options
import tornado.web

from tornado.options import define, options
from tornado.web import url

import hotqueue
import pylibmc
import yaml
import pymongo
import redis

import boto.s3.connection
from pymongo.objectid import ObjectId

# App imports
import forms
import pubnub
import uimodules
import util

define("port", default=8888, type=int)
define("config_file", default="app_config.yml", help="app_config file")

class Model(dict):
  """Like tornado.web._O but does not whine for non-existent attributes"""
  def __getattr__(self, name):
    try:
      return self[name]
    except KeyError:
      return None

  def __setattr__(self, name, value):
    self[name] = value


class Application(tornado.web.Application):
  def __init__(self):
    self.config = self._get_config()
    handlers = [
      url(r'/', IndexHandler, name='index'),
      url(r'/auth/google', GoogleAuthHandler, name='auth_google'),
      url(r'/logout', LogoutHandler, name='logout'),
      url(r'/home', HomeHandler, name='home'),
      url(r'/new', NewRoomHandler, name='new'),
      url(r'/rooms/(?P<id>\w+)', MessagesHandler, name='room'),
      url(r'/rooms/(?P<id>\w+)/messages', MessagesHandler, name='messages'),
      url(r'/rooms/(?P<id>\w+)/files', FilesHandler, name='files'),
      url(r'/rooms/(?P<id>\w+)/transcripts', TranscriptsHandler, name='transcripts'),
      url(r'/rooms/(?P<id>\w+)/settings', SettingsHandler, name='settings'),
      url(r'/rooms/(?P<id>\w+)/say', NewMessageHandler, name='new_message'),
      url(r'/rooms/(?P<id>\w+)/upload', UploadHandler, name='upload'),
      url(r'/rooms/(?P<id>\w+)/leave', LeaveRoomHandler, name='leave_room'),
      url(r'/rooms/(?P<id>\w+)/delete', DeleteRoomHandler, name='delete_room'),
      url(r'/rooms/(?P<id>\w+)/invite', NewInvitationHandler, name='invite'),
      url(r'/rooms/(?P<id>\w+)/invitations', InvitationsHandler, name='invitations'),
      url(r'/i', InvitationHandler, name='invitation'),
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
    # TODO Configurable settings for redis
    self.redis = redis.Redis(host='localhost', port=6379, db=0)
    self.upload_queue = hotqueue.HotQueue('upload', host='localhost', port=6379, db=0)
    # TODO create indexes here
    self.memcache = pylibmc.Client(
        self.config.memcache_servers, binary=True,
        behaviors={"tcp_nodelay": True, "ketama": True})
    self.pubnub = pubnub.Pubnub(self.config.pubnub_publish_key,
                                self.config.pubnub_subscribe_key,
                                self.config.pubnub_secret_key,
                                self.config.pubnub_ssl_on)
    self.s3 = boto.s3.connection.S3Connection(
        self.config.aws_access_key_id, self.config.aws_secret_access_key)

  def _get_config(self):
    stream = file(options.config_file, 'r')
    config = Model(yaml.load(stream))
    stream.close()
    return config


class BaseHandler(tornado.web.RequestHandler):
  @property
  def db(self):
    return self.application.db

  @property
  def redis(self):
    return self.application.redis

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
    return Model(user)

  @property
  def rooms(self):
    if not hasattr(self, '_rooms'):
      if not self.current_user:
        self._rooms = []
      else:
        self._rooms = [Model(r) for r in self.db.rooms.find({'members': self.current_user._id})]
    return self._rooms

  @property
  def is_ajax(self):
    return 'X-Requested-With' in self.request.headers and \
           self.request.headers['X-Requested-With'] == 'XMLHttpRequest'


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
    self.redirect(self.get_argument('next', self.reverse_url('home')))


class LogoutHandler(BaseHandler):
  def get(self):
    self.clear_cookie('user_id')
    self.redirect(self.reverse_url('index'))


class HomeHandler(BaseHandler):
  @tornado.web.authenticated
  def get(self):
    rooms = list(Model(r) for r in self.db.rooms.find({'members': self.current_user._id}))
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
      room = Model(owner=self.current_user._id,
                   admins=[self.current_user._id],
                   members=[self.current_user._id],
                   topic='',
                   current_users=[self.current_user._id])
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
    room = self.db.rooms.find_one({'_id': id})
    if room is None:
      raise tornado.web.HTTPError(404)
    room = Model(room)
    if not room.is_public:
      if self.current_user._id not in room.members:
        raise tornado.web.HTTPError(403)
    self.room = room
    return method(self, *args, **kwds)
  return _wrapper


def room_admin_required(method):
  @room_required
  def _wrapper(self, *args, **kwds):
    if self.current_user._id != self.room.owner and \
       self.current_user._id not in self.room.admins:
      raise tornado.web.HTTPError(403)
    return method(self, *args, **kwds)
  return _wrapper


class BaseRoomHandler(BaseHandler):
  active_menu = 'messages'

  def get_current_users(self):
    # users = self.room.current_users
    return (Model(user) for user in self.db.users.find(
            {'_id': {'$in': list(self.room.current_users)}}))

  def is_admin(self):
    return self.current_user._id in self.room.admins

  def render_string(self, template_name, **kwds):
    kwds.update({
      'room': self.room,
      'current_users': self.get_current_users(),
      'active_menu': self.active_menu,
      'is_admin': self.current_user._id in self.room.admins,
      'js_context': {
        'active_menu': self.active_menu,
        'current_user': {
          'id': str(self.current_user._id),
          'name': self.current_user.name or self.current_user.email,
        },
        'room': {
          'id': str(self.room._id),
          'token': self.room.token
        }
      }
    })
    return super(BaseRoomHandler, self).render_string(template_name, **kwds)


class MessagesHandler(BaseRoomHandler):
  active_menu = 'messages'

  @room_required
  def get(self):
    recent_messages = [Model(m) for m in self.db.messages.find({'room': self.room._id,})]
    if self.is_ajax:
      self.write(self.ui['modules']['Messages'](messages=recent_messages))
    else:
      self.render('messages.html', recent_messages=recent_messages)


class FilesHandler(BaseRoomHandler):
  active_menu = 'files'

  @room_required
  def get(self):
    files = (Model(m) for m in self.db.messages.find({
      'room': self.room._id,
      'type': {'$in': ['file', 'image']},
    }))
    if self.is_ajax:
      self.write(self.ui['modules']['Files'](files=files))
    else:
      self.render('files.html', files=files)


class TranscriptsHandler(BaseRoomHandler):
  active_menu = 'transcripts'

  @room_required
  def get(self):
    if self.is_ajax:
      self.write(self.ui['modules']['Transcripts']())
    else:
      self.render('transcripts.html')


class SettingsHandler(BaseRoomHandler):
  active_menu = 'settings'

  @room_admin_required
  def get(self):
    form = forms.RoomForm(obj=self.room)
    if self.is_ajax:
      self.write(self.ui['modules']['Settings'](form=form))
    else:
      self.render('settings.html', form=form)

  @room_admin_required
  def post(self):
    form = forms.RoomForm(self, obj=self.room)
    if form.validate():
      form.populate_obj(self.room)
      self.db.rooms.save(self.room)
      self.pubnub.publish({
        'channel': self.room.token,
        'message': {
          'type': 'topic_changed',
          'content': self.room.topic,
          'user_name': self.current_user.name or self.current_user.email,
        }
      })
      self.redirect(self.reverse_url('room', self.room._id))
    else:
      self.render('settings.html')


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


class LeaveRoomHandler(BaseHandler):
  @room_required
  def get(self):
    if self.room.current_users:
      try:
        self.room.current_users.remove(self.current_user._id)
        self.db.rooms.save(self.room)
        self.pubnub.publish({
          'channel': self.room.token,
          'message': {
            'type': 'leave',
            'user_id': str(self.current_user._id),
            'user_name': self.current_user.name or self.current_user.email
          }
        })
      except ValueError:
        pass
    self.redirect(self.reverse_url('home'))


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


class InvitationStatus:
  PENDING = 1
  ACCEPTED = 2


class NewInvitationHandler(BaseHandler):
  @room_admin_required
  def get(self):
    form = forms.InvitationForm()
    self.render('new_invitation.html', form=form, room=self.room)

  @room_admin_required
  def post(self):
    form = forms.InvitationForm(self)
    if form.validate():
      token = util.generate_token(32)
      invitation = {
        'inviter': self.current_user._id,
        'room': self.room._id,
        'name': form.name.data,
        'email': form.email.data,
        'token': token,
        'created_at': datetime.datetime.utcnow(),
        'status': InvitationStatus.PENDING,
      }
      self.db.invitations.insert(invitation)
      # TODO Send email here
      self.redirect(self.reverse_url('room', self.room._id))
    else:
      self.render('new_invitation.html', form=form, room=self.room)


class InvitationsHandler(BaseHandler):
  @room_admin_required
  def get(self):
    invitations = (Model(i) for i in self.db.invitations.find({'room': self.room._id}))
    self.render('invitations.html',
                invitations=invitations,
                room=self.room,
                invitation_status=InvitationStatus)


class InvitationHandler(BaseHandler):
  def get(self):
    token = self.get_argument('token')
    invitation = Model(self.db.invitations.find_one({'token': token}))
    if not invitation:
      raise tornado.web.HTTPError(404)
    if invitation.status == InvitationStatus.ACCEPTED:
      raise tornado.web.HTTPError(404)
    room = Model(self.db.rooms.find_one({'_id': invitation.room}))
    if not room:
      logging.error("No room for invitation %s" % invitation._id)
      raise tornado.web.HTTPError(404)
    if self.current_user:
      room.members.append(self.current_user._id)
      self.db.rooms.save(room)
      invitation.status = InvitationStatus.ACCEPTED
      invitation.accepted_by = self.current_user._id
      invitation.accepted_at = datetime.datetime.utcnow()
      self.invitations.save(invitation)
      self.redirect(self.reverse_url('room', room._id))
    else:
      self.redirect(self.reverse_url('auth_google') + '?next=%s' % self.request.uri)


def main():
  tornado.options.parse_command_line()
  http_server = tornado.httpserver.HTTPServer(Application())
  http_server.listen(options.port)
  tornado.ioloop.IOLoop.instance().start()

if __name__ == '__main__':
  main()

