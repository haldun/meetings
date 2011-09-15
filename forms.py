from wtforms import *
from wtforms.validators import *

import wtforms.fields
import wtforms.widgets

from util import MultiValueDict

class BaseForm(Form):
  def __init__(self, handler=None, obj=None, prefix='', formdata=None, **kwargs):
    if handler:
      formdata = MultiValueDict()
      for name in handler.request.arguments.keys():
        formdata.setlist(name, handler.get_arguments(name))
    Form.__init__(self, formdata, obj=obj, prefix=prefix, **kwargs)


class RoomForm(BaseForm):
  name = TextField('Name', [Required()])
  topic = TextField('Topic')
  is_public = BooleanField('Is public?')


class InvitationForm(BaseForm):
  name = TextField('Name')
  email = TextField('Email', [Required()])
