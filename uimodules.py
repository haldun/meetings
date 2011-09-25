import os
import tornado.web

class BaseUIModule(tornado.web.UIModule):
  @property
  def application(self):
    return self.handler.application

  @property
  def config(self):
    return self.application.config


class Form(tornado.web.UIModule):
  """
  Generic form rendering module. Works with wtforms.
  Use this in your template code as:

  {% module Form(form) %}

  where `form` is a wtforms.Form object. Note that this module does not render
  <form> tag and any buttons.
  """

  def render(self, form):
    """docstring for render"""
    return self.render_string('uimodules/form.html', form=form)


class Messages(BaseUIModule):
  def render(self, messages):
    for message in messages:
      if message.type == 'image':
        name, ext = os.path.splitext(message.s3_key)
        thumbname = '%s_thumb%s'% (name, ext)
        print message.s3_key
        message.thumbnail_url = self.application.s3.generate_url(
            1200, 'GET', self.config.s3_bucket_name, thumbname)
        message.image_url = self.application.s3.generate_url(
            1200, 'GET', self.config.s3_bucket_name, message.s3_key)
      elif message.type == 'file':
        message.url = self.application.s3.generate_url(
            1200, 'GET', self.config.s3_bucket_name, message.s3_key)
      elif message.type == 'text':
        pass
    return self.render_string('uimodules/messages.html', messages=messages)


class MessageComposer(BaseUIModule):
  def render(self, room):
    return self.render_string('uimodules/message_composer.html', room=room)

  # def javascript_files(self):
  #   return ['javascripts/composer.js']

  # def embedded_javascript(self):
  #   return ''

class Files(BaseUIModule):
  def render(self, files):
    return self.render_string('uimodules/files.html', files=files)


class Transcripts(BaseUIModule):
  def render(self, date=None, messages=None):
    return self.render_string('partials/transcripts.html', date=date, messages=messages)


class Settings(BaseUIModule):
  def render(self, form):
    return self.render_string('partials/settings.html', form=form)


class Invitations(BaseUIModule):
  def render(self, invitations, invitation_status):
    return self.render_string('partials/invitations.html',
                              invitations=invitations,
                              invitation_status=invitation_status)


class FileItem(BaseUIModule):
  def render(self, file):
    if file.type == 'image':
      name, ext = os.path.splitext(file.s3_key)
      thumbname = '%s_thumb%s'% (name, ext)
      file.thumbnail_url = self.application.s3.generate_url(
          1200, 'GET', self.config.s3_bucket_name, thumbname)
      file.url = self.application.s3.generate_url(
          1200, 'GET', self.config.s3_bucket_name, file.s3_key)
    elif file.type == 'file':
      file.url = self.application.s3.generate_url(
          1200, 'GET', self.config.s3_bucket_name, file.s3_key)
    return self.render_string('uimodules/file_item.html', file=file)


class RoomItem(BaseUIModule):
  def render(self, room):
    is_admin = self.handler.current_user._id in room.admins
    return self.render_string('uimodules/room_item.html', room=room, is_admin=is_admin)
