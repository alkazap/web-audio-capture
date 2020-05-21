import asyncio
import json
import logging
import logging.config
import logging.handlers
import multiprocessing
import os
import threading
import time
import uuid

import psutil
import tornado.httpserver
import tornado.ioloop
import tornado.web
import tornado.websocket
import yaml

from decoder_worker import DecoderWorker
from decoder_mproc import get_logger, get_workers, term_logger, term_workers


class Application(tornado.web.Application):
    def __init__(self):
        handlers = [
            (r'/', IndexHandler),
            (r'/webclient', ClientSocketHandler),
            (r'/decoder', DecoderSocketHandler),
            (r'/sysinfo', SysInfoSocketHandler)
        ]
        settings = dict(
            template_path=os.path.join(os.path.dirname(__file__), 'templates'),
            static_path=os.path.join(os.path.dirname(__file__), 'static')
        )
        tornado.web.Application.__init__(self, handlers, **settings)

        self.decoder_socket_list = set()
        self.client_socket_list = set()
        self.sys_info_socket_list = set()

    def get_sys_info(self):
        sys_info = {cpu_num: {'procs': {}, 'cpu_percent': 0, 'memory_percent': 0}
                    for cpu_num in range(psutil.cpu_count())}
        for proc in psutil.process_iter(attrs=['cmdline', 'cpu_num', 'memory_percent']):
            proc_name = ''
            if len(proc.info['cmdline']) > 1 and 'python' in proc.info['cmdline'][0]:
                cmdline = ' '.join(proc.info['cmdline'])
                if 'multiprocessing.spawn' in cmdline:
                    proc_name = 'mproc'
                elif 'multiprocessing.semaphore_tracker' in cmdline:
                    proc_name = 'mp_tracker'
                elif '.py' in proc.info['cmdline'][1]:
                    proc_name = proc.info['cmdline'][1].split('.')[0]
            if len(proc_name) > 0:
                proc_info = proc.as_dict(
                    attrs=['pid', 'cpu_percent', 'memory_percent', 'num_threads'])
                proc_info['name'] = proc_name
                # if len(proc_info['cpu_affinity']) == psutil.cpu_count():
                #    proc_info['cpu_affinity'] = "all"
                #import datetime
                #proc_info['create_time'] = datetime.datetime.fromtimestamp(proc_info['create_time']).strftime("%Y-%m-%d %H:%M:%S")
                proc_info['memory_percent'] = "%.1f" % proc_info['memory_percent']
                proc_num = len(sys_info[proc.info['cpu_num']]['procs'])
                sys_info[proc.info['cpu_num']]['procs'][proc_num] = proc_info
            sys_info[proc.info['cpu_num']
                     ]['memory_percent'] += proc.info['memory_percent']
        cpu_percent = psutil.cpu_percent(percpu=True)
        for cpu_num in range(psutil.cpu_count()):
            sys_info[cpu_num]['cpu_percent'] = cpu_percent[cpu_num]
            sys_info[cpu_num]['memory_percent'] = "%.1f" % sys_info[cpu_num]['memory_percent']
        return sys_info

    def send_sys_info_update(self):
        asyncio.set_event_loop(asyncio.new_event_loop())
        while True:
            if len(self.sys_info_socket_list) > 0:
                sys_info = self.get_sys_info()
                for ws in self.sys_info_socket_list:
                    ws.write_message(json.dumps(sys_info))
            time.sleep(1)


class IndexHandler(tornado.web.RequestHandler):
    # def prepare(self):
    #     if self.request.protocol == 'http':
    #         self.redirect(self.request.full_url().replace('http', 'https', 1), permanent=True)

    def get(self):
        self.render('index.html')


class SysInfoSocketHandler(tornado.websocket.WebSocketHandler):
    def initialize(self):
        self.log = logging.getLogger(self.__class__.__name__)
        self.ip = None

    def open(self):
        self.ip = self.request.remote_ip
        self.log.info("A new sys info listener WebSocket(%s) is opened" %
                      (self.ip))
        self.application.sys_info_socket_list.add(self)

    def on_close(self):
        self.log.info("The sys info listener WebSocket(%s) is closed" %
                      (self.ip))
        self.application.sys_info_socket_list.discard(self)


class ClientSocketHandler(tornado.websocket.WebSocketHandler):
    def initialize(self):
        self.log = logging.getLogger(self.__class__.__name__)
        self.id = str(uuid.uuid4())
        self.ip = None
        self.decoder_socket = None

    def open(self):
        self.ip = None
        self.decoder_socket = None
        if self.request.remote_ip in {client.ip for client in self.application.client_socket_list}:
            self.log.warn(
                "%s: Multiple clients with the same IP(%s) are not allowed" % (self.id, self.request.remote_ip))
            message = dict(
                type='warning', data='Already processing request from this IP')
            self.write_message(json.dumps(message))
            self.close()
        else:
            self.ip = self.request.remote_ip
            self.application.client_socket_list.add(self)
            self.log.info("%s: A new client WebSocket(%s) is opened" %
                          (self.id, self.ip))
            try:
                self.decoder_socket = self.application.decoder_socket_list.pop()
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
        self.application.client_socket_list.discard(self)
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
        self.application.decoder_socket_list.add(self)

    def on_close(self):
        self.log.info("%s: The decoder WebSocket is closed" % self.id)
        self.application.decoder_socket_list.discard(self)
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


def main():
    # Parse arguments
    from arg_parser import parse_args
    try:
        args = parse_args(server=True)
    except SystemExit:
        return

    # Configure logger
    config_logger = {}
    with open(args.log) as f:
        config_logger = yaml.safe_load(f)
    logging.config.dictConfig(config_logger)

    # Initialize web application
    app = Application()
    # Check SSL options
    ssl = False
    if args.certfile and args.keyfile:
        ssl = True
        ssl_options = {
            'certfile': args.certfile,
            'keyfile': args.keyfile,
        }
        logging.info("Using SSL for serving requests")
        # Starts an HTTP server for this app
        app.listen(args.port, ssl_options=ssl_options)
    else:
        # Start an HTTP server for this app
        app.listen(args.port)

    # Send sys info updates
    threading.Thread(target=app.send_sys_info_update, args=(),
                     name='SysInfoUpdate', daemon=True).start()

    # Only start server
    if args.nproc == 0:
        try:
            # I/O event loop for non-blocking sockets
            tornado.ioloop.IOLoop.current().start()
        except KeyboardInterrupt:
            tornado.ioloop.IOLoop.current().stop()
            tornado.ioloop.IOLoop.current().close()
    # Start decoder
    elif args.nproc > 0:
        # Construct decoder websocket url
        protocol = 'ws'
        if ssl:
            protocol += 's'
        host = 'localhost' + ':' + str(args.port)
        url = protocol + '://' + host + '/decoder'
        logging.info("Decoder websocket url=%s" % url)
        # One decoder in same process
        if args.nproc == 1:
            # Run decoder worker
            decoder_worker = DecoderWorker(conf_file=args.conf, url=url)
            try:
                decoder_worker.run()
            except SystemExit:
                return

            try:
                # I/O event loop for non-blocking sockets
                tornado.ioloop.IOLoop.current().start()
            except KeyboardInterrupt:
                decoder_worker.stop()
                decoder_worker.cleanup()
                tornado.ioloop.IOLoop.current().stop()
                tornado.ioloop.IOLoop.current().close()
        # Multiple decoders with multiprocessing
        else:
            multiprocessing.set_start_method('spawn')
            queue = multiprocessing.Queue(-1)

            stop_workers = multiprocessing.Event()
            workers = get_workers(queue=queue, conf_file=args.conf,
                                  url=url, stop_event=stop_workers, num_workers=args.nproc)

            stop_logger = multiprocessing.Event()
            logger = get_logger(queue=queue, conf_file=args.mplog,
                                stop_event=stop_logger)

            # Send sys info updates
            #threading.Thread(target=app.send_sys_info_update, args=(os.getpid(), [worker.pid for worker in workers], logger.pid), name='SysInfoUpdate', daemon=True).start()

            try:
                # I/O event loop for non-blocking sockets
                tornado.ioloop.IOLoop.current().start()
            except KeyboardInterrupt:
                stop_workers.set()
                term_workers(workers)
            finally:
                stop_logger.set()
                term_logger(logger)
                queue.close()
                tornado.ioloop.IOLoop.current().stop()
                tornado.ioloop.IOLoop.current().close()


if __name__ == '__main__':
    main()
