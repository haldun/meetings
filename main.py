import tornado.auth
import tornado.escape
import tornado.httpserver
import tornado.ioloop
import tornado.options
import tornado.web

from tornado.options import define, options
from tornado.web import url

from app import Application

def main():
  tornado.options.parse_command_line()
  app = Application()
  http_server = tornado.httpserver.HTTPServer(app)
  http_server.bind(options.port)
  http_server.start(app.config.debug and 1 or -1)
  tornado.ioloop.IOLoop.instance().start()

if __name__ == '__main__':
  main()
