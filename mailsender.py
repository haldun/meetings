import logging
import sys
import smtplib
from email.MIMEMultipart import MIMEMultipart
from email.MIMEText import MIMEText
from email.header import Header

import hotqueue
import yaml

import tornado.options
import tornado.web

from tornado.options import define, options


define("config_file", default="app_config.yml", help="app_config file")

queue = hotqueue.HotQueue('mail', host='localhost', port=6379, db=0)

class Mailer(object):
  def __init__(self):
    pass

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
    mail_user = self.config.mail_user
    mail_pwd = self.config.mail_pwd

    to = item['to']
    subject = item['subject']
    text = item['text']

    msg = MIMEMultipart()
    msg['From'] = mail_user
    msg['To'] = to
    msg['Subject'] = Header(subject, 'utf-8')
    msg.attach(MIMEText(text.encode('utf-8')))

    server = smtplib.SMTP('smtp.gmail.com', 587)
    server.ehlo()
    server.starttls()
    server.ehlo()
    server.login(mail_user, mail_pwd)
    server.sendmail(mail_user, to, msg.as_string())
    server.close()

    logging.info("Sent invitation to %s" % to)


def main():
  tornado.options.parse_command_line()
  mailer = Mailer()
  mailer.run()

if __name__ == '__main__':
  main()

