import json
import logging
import logging.config
import logging.handlers
import multiprocessing
import os
import threading
import time
import uuid
import yaml

import tornado.httpserver
import tornado.ioloop
import tornado.options
import tornado.web
import tornado.websocket

import gi
gi.require_version('Gst', '1.0')
from gi.repository import GLib

from ws4py.exc import HandshakeError

from decoder_pipeline import DecoderPipeline
from decoder_ws import DecoderSocket


CONNECTION_TIMEOUT = 5
NUM_WORKERS = 1


class Application(tornado.web.Application):
    def __init__(self):
        handlers = [
            (r'/', IndexHandler),
            (r'/webclient', ClientSocketHandler),
            (r'/decoder', DecoderSocketHandler)
        ]
        settings = dict(
            template_path=os.path.join(os.path.dirname(__file__), 'templates'),
            static_path=os.path.join(os.path.dirname(__file__), 'static')
        )
        tornado.web.Application.__init__(self, handlers, **settings)

        self.decoder_list = set()
        self.client_list = set()


class IndexHandler(tornado.web.RequestHandler):
    # def prepare(self):
    #     if self.request.protocol == 'http':
    #         self.redirect(self.request.full_url().replace('http', 'https', 1), permanent=True)

    def get(self):
        self.render('index.html')


class ClientSocketHandler(tornado.websocket.WebSocketHandler):
    def initialize(self):
        self.log = logging.getLogger(self.__class__.__name__)
        self.id = str(uuid.uuid4())
        self.ip = None
        self.decoder_socket = None

    def open(self):
        self.ip = None
        self.decoder_socket = None
        # if self.request.remote_ip in {client.ip for client in self.application.client_list}:
        if self.request.remote_ip in self.application.client_list:
            self.log.warn(
                "%s: Multiple clients with the same IP(%s) are not allowed" % (self.id, self.request.remote_ip))
            message = dict(
                type='warning', data='Already processing request from this IP')
            self.write_message(json.dumps(message))
            self.close()
        else:
            self.ip = self.request.remote_ip
            self.application.client_list.add(self)
            self.log.info("%s: A new client WebSocket(%s) is opened" %
                          (self.id, self.ip))

        try:
            self.decoder_socket = self.application.decoder_list.pop()
            self.decoder_socket.set_client_socket(self)
            self.log.info("%s: Decoder available: %s" % (
                self.id, self.decoder_socket.get_id()))
        except KeyError:
            # Raised when a mapping (dictionary) key is not found in the set of existing keys
            self.log.warn(
                "%s: No decocoder available" % self.id)
            message = dict(
                type='warning', data='No decoder available, try again later')
            self.write_message(json.dumps(message))
            self.close()

    def on_close(self):
        self.log.info("%s: The client WebSocket(%s) is closed" %
                      (self.id, self.ip))
        self.application.client_list.discard(self)
        if self.decoder_socket is not None:
            self.decoder_socket.close()

    def on_message(self, message):
        self.log.info(
            "%s: Received client message(%s) of len=%d" % (self.id, type(message), len(message)))
        if self.decoder_socket is not None:
            if isinstance(message, bytes):
                self.decoder_socket.write_message(message, binary=True)
            elif isinstance(message, str):
                json_message = json.loads(str(message))
                if json_message['type'] == 'caps':
                    json_message['id'] = self.id
                self.decoder_socket.write_message(
                    json.dumps(json_message), binary=False)
        else:
            self.log.error(
                "%s: No decoder assigned, closing client WebSocket(%s)" % (self.id, self.ip))
            self.close()

    def get_id(self):
        return self.id


class DecoderSocketHandler(tornado.websocket.WebSocketHandler):
    def initialize(self):
        self.log = logging.getLogger(self.__class__.__name__)
        self.id = str(uuid.uuid4())
        self.client_socket = None

    def open(self):
        self.log.info("%s: A new decoder WebSocket is opened" % self.id)
        self.client_socket = None
        self.application.decoder_list.add(self)

    def on_close(self):
        self.log.info("%s: The decoder WebSocket is closed" % self.id)
        self.application.decoder_list.discard(self)
        if self.client_socket is not None:
            self.client_socket.close()

    def on_message(self, message):
        if self.client_socket is not None:
            self.log.info(
                "%s: Received decoder message = %s" % (self.id, message))
            self.client_socket.write_message(message)
        else:
            self.log.error(
                "%s: No client assigned, closing decoder WebSocket" % self.id)
            self.close()

    def set_client_socket(self, client_socket):
        self.client_socket = client_socket
        self.log.info("%s: New client: %s" %
                      (self.id, self.client_socket.get_id()))

    def get_id(self):
        return self.id


class DecoderSocketLoop(threading.Thread):
    def __init__(self, url, decoder_pipeline, stop_event, **kwargs):
        threading.Thread.__init__(self, **kwargs)
        self.url = url
        self.decoder_pipeline = decoder_pipeline
        self.stop_event = stop_event
        self.log = logging.getLogger(self.__class__.__name__)
        self.ws = None

    def run(self):
        self.log.info("About to enter the loop")
        threading.Thread(target=self.event_listener,
                         name="EventListener").start()
        while not self.stop_event.is_set():
            self.log.info("Entered the loop")
            self.ws = DecoderSocket(self.url, self.decoder_pipeline)
            try:
                self.log.info("Connecting decoder websocket to the server")
                self.ws.connect()
                # Block the thread until the websocket has terminated
                self.ws.run_forever()
            except HandshakeError:
                self.log.error(
                    "Could not connect decoder websocket to the server, waiting for %d sec" % CONNECTION_TIMEOUT)
                # Connection timeout
                time.sleep(CONNECTION_TIMEOUT)
            # Fixes race condition
            time.sleep(1)
        self.log.info("Left the loop")

    def event_listener(self):
        self.log.info("Waiting for stop event")
        self.stop_event.wait()
        self.log.info("Got stop event")
        self.ws.close_connection()


class DecoderProcess(multiprocessing.Process):
    def __init__(self, queue: multiprocessing.Queue, url: str, conf: dict, stop_event: multiprocessing.Event, **kwargs):
        multiprocessing.Process.__init__(self, **kwargs)
        self.url = url
        self.queue = queue
        self.conf = conf
        self.stop_event = stop_event

    def log_config(self):
        config = {
            'version': 1,
            'disable_existing_loggers': True,
            'handlers': {
                'queue': {
                    'class': 'logging.handlers.QueueHandler',
                    'queue': self.queue
                }
            },
            'root': {
                'handlers': ['queue'],
                'level': 'DEBUG'
            }
        }
        logging.config.dictConfig(config)
        self.log = logging.getLogger(self.__class__.__name__)
        self.log.info("Configured logger")

    def run(self):
        self.log_config()
        self.decoder_pipeline = DecoderPipeline(self.conf)
        self.log.info("Configured DecoderPipeline")
        # GLib MainLoop doesn't steal SIGINGT (unlike GObject)
        self.main_loop = GLib.MainLoop.new(None, False)
        self.main_loop_thread = threading.Thread(
            target=self.main_loop.run, name='GLibMainLoop', args=())
        self.main_loop_thread.start()
        # Create decoder websocket
        self.stop_socket_loop = threading.Event()
        self.socket_loop_thread = DecoderSocketLoop(
            self.url, self.decoder_pipeline, self.stop_event)
        self.socket_loop_thread.start()

        self.log.info("Waiting for stop event")
        try:
            self.stop_event.wait()
        except KeyboardInterrupt:
            self.log.info("Got SIGINT")
        finally:
            self.stop_event.wait()
            self.log.info("Got stop event")
            self.stop_socket_loop.set()
            self.socket_loop_thread.join()
            if self.main_loop.is_running():
                self.main_loop.quit()
            self.main_loop_thread.join()


class ListenerProcess(multiprocessing.Process):
    def __init__(self, queue, config, stop_event, **kwargs):
        multiprocessing.Process.__init__(self, **kwargs)
        self.queue = queue
        self.config = config
        self.stop_event = stop_event

    def run(self):
        logging.config.dictConfig(self.config)
        self.log = logging.getLogger(self.__class__.__name__)
        self.listener = logging.handlers.QueueListener(
            self.queue, *logging.getLogger().handlers, respect_handler_level=True)
        self.listener.start()
        self.log.info("Waiting for stop event")

        try:
            self.stop_event.wait()
        except KeyboardInterrupt:
            self.log.info("Got SIGINT")
        finally:
            self.stop_event.wait()
            self.log.info("Got stop event")
            self.listener.stop()


def main():
    config_logger = {}
    with open('config/logging/server.yaml') as f:
        config_logger = yaml.safe_load(f)
    logging.config.dictConfig(config_logger)

    # Parse global options from the command line
    from tornado.options import define, options
    define('port', default=8888, help='run on the given port', type=int)
    define('hostname', default='localhost', help='run on the given hostname')
    define('certfile', default='',
           help='certificate file for secured SSL connection')
    define('keyfile', default='', help='key file for secured SSL connection')
    tornado.options.parse_command_line()

    # Initialize web application
    app = Application()

    ssl = False
    if options.certfile and options.keyfile:
        ssl = True
        ssl_options = {
            'certfile': options.certfile,
            'keyfile': options.keyfile,
        }
        logging.info("Using SSL for serving requests")
        # Starts an HTTP server for this app
        app.listen(options.port, ssl_options=ssl_options)
    else:
        # Start an HTTP server for this app
        app.listen(options.port)

    # Construct decoder websocket url
    protocol = ''
    if ssl:
        protocol = 'wss:'
    else:
        protocol = 'ws:'
    host = options.hostname + ':' + str(options.port)
    url = protocol + '//' + host + '/decoder'
    logging.info("Decoder websocket url=%s" % url)

    # Multiprocessing
    multiprocessing.set_start_method('spawn')
    queue = multiprocessing.Queue(-1)

    num_workers = NUM_WORKERS
    if (NUM_WORKERS > int(multiprocessing.cpu_count()/2)):
        logging.warning(
            "Not allowed number of worker processes: %d" % num_workers)
        num_workers = int(multiprocessing.cpu_count()/2)
        logging.info("Set number of worker processes to %d" % num_workers)

    workers = []

    config_decoder = {}
    with open('config/decoder/voxforge.yaml') as f:
        config_decoder = yaml.safe_load(f)

    stop_workers = multiprocessing.Event()
    for i in range(num_workers):
        worker = DecoderProcess(
            queue, url, config_decoder, stop_workers, name='Worker%d' % (i+1))
        workers.append(worker)
        worker.start()

    stop_listener = multiprocessing.Event()
    config_listener = {}
    with open('config/logging/listener.yaml') as file:
        config_listener = yaml.safe_load(file)
    listener = ListenerProcess(
        queue, config_listener, stop_listener, name='Listener')
    listener.start()

    # I/O event loop for non-blocking sockets
    try:
        tornado.ioloop.IOLoop.current().start()
    except KeyboardInterrupt:
        logging.info("Got SIGINT")
    finally:
        stop_workers.set()
        for worker in workers:
            worker.join()
        while workers:
            worker = workers.pop()
            if worker.is_alive():
                print("%s(%s) process is alive" % (worker.name, worker.pid))
                worker.terminate()
            logging.info("%s(%s) process exitcode=%s" %
                         (worker.name, worker.pid, worker.exitcode))
        stop_listener.set()
        listener.join()
        if listener.is_alive():
            print("%s(%s) process is alive" % (worker.name, worker.pid))
            listener.terminate()
        logging.info("%s(%s) process exitcode=%s" %
                     (listener.name, listener.pid, listener.exitcode))
        queue.close()
        tornado.ioloop.IOLoop.current().stop()
        tornado.ioloop.IOLoop.current().close()


if __name__ == '__main__':
    main()
