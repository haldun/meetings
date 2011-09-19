import datetime
import logging
import mimetypes
import os
import tempfile
from cStringIO import StringIO
import sys

import boto.s3.key
import boto.s3.connection
import hotqueue
import Image
import yaml
import pymongo

import tornado.options
from tornado.options import define, options
import tornado.web

import pubnub_sync

define("config_file", default="app_config.yml", help="app_config file")

queue = hotqueue.HotQueue('upload', host='localhost', port=6379, db=0)

class Uploader(object):
  def __init__(self):
    connection = pymongo.Connection()
    self.db = connection[self.config.mongodb_database]

  @property
  def config(self):
    if not hasattr(self, '_config'):
      logging.debug("Loading app config")
      stream = file(options.config_file, 'r')
      self._config = tornado.web._O(yaml.load(stream))
    return self._config

  @queue.worker
  def run(self, item):
    try:
      self.process_item(item)
    except:
      print sys.exc_info()

  def process_item(self, item):
    filepath = item['file']
    filename = item['filename']
    room_id = item['room_id']
    user_id = item['user_id']
    username = item['username']
    room_token = item['room_token']

    print "got this job: %s" % item

    im = thumbnail = None
    try:
      im = Image.open(filepath)
    except:
      pass

    message_type = im and 'image' or 'file'

    # Generate thumbnail
    if im:
      thumbnail = Image.open(filepath)
      thumbnail.thumbnail((300, 300), Image.ANTIALIAS)

    print im
    print thumbnail

    # Upload thumbnail if necessary
    if thumbnail:
      name, ext = os.path.splitext(filename)
      thumbname = '/uploads/%s/%s_thumb%s' % (room_id, name, ext)
      thumbfile = tempfile.NamedTemporaryFile()
      thumbnail.save(thumbfile, im.format)

    # Determine file mimetype
    if im:
      mime_type = 'image/%s' % im.format.lower()
    else:
      mime_type, _ = mimetypes.guess_type(filename)

    # Create keys for file
    key = boto.s3.key.Key(self.bucket)
    key.key = '/uploads/%s/%s' % (room_id, filename)

    if mime_type:
      key.set_metadata('Content-Type', mime_type)

    file = open(filepath)
    filesize = os.path.getsize(filepath)
    key.set_contents_from_file(file)
    file.close()
    os.remove(filepath)

    print "Uploaded file"

    # Upload thumbnail
    if thumbnail:
      thumb_key = boto.s3.key.Key(self.bucket)
      thumb_key.key = thumbname
      if mime_type:
        thumb_key.set_metadata('Content-Type', mime_type)
      thumb_key.set_contents_from_file(thumbfile.file)

    print "Uploaded thumbnail"

    # Create a message
    content = '%s posted a file' % username
    message = {
      'room': room_id,
      'user_id': user_id,
      'user_name': username,
      'type': message_type,
      'filename': filename,
      's3_key': key.key,
      'content': content,
      'created_at': datetime.datetime.utcnow(),
    }
    if message_type == 'image':
      message['size'] = im.size
      message['s3_thumbnail_key'] = thumb_key.key
      message['thumb_size'] = thumbnail.size

    if mime_type:
      message['mime_type'] = mime_type

    message['filesize'] = filesize

    message_id = self.db.messages.insert(message)

    m = {
      'channel': room_token,
      'message': {
        'id': str(message_id),
        'content': message['content'],
        'user_id': str(message['user_id']),
        'user_name': message['user_name'],
        'type': message_type,
        'url': key.generate_url(3600),
      }
    }

    if message_type == 'image':
      m['message']['size'] = message['size']
      m['message']['thumb_url'] = thumb_key.generate_url(3600)

    self.pubnub.publish(m)


  @property
  def connection(self):
    if not hasattr(self, '_connection'):
      self._connection = boto.s3.connection.S3Connection(
          self.config.aws_access_key_id, self.config.aws_secret_access_key)
    return self._connection

  @property
  def bucket(self):
    if not hasattr(self, '_bucket'):
      self._bucket = self.connection.create_bucket(self.config.s3_bucket_name)
    return self._bucket

  @property
  def pubnub(self):
    if not hasattr(self, '_pubnub'):
      self._pubnub = pubnub_sync.Pubnub(self.config.pubnub_publish_key,
                                        self.config.pubnub_subscribe_key,
                                        self.config.pubnub_secret_key,
                                        self.config.pubnub_ssl_on)
    return self._pubnub

def main():
  tornado.options.parse_command_line()
  uploader = Uploader()
  uploader.run()

if __name__ == '__main__':
  main()
