import json
import logging
import os
import threading
import time
import uuid

import tornado.httpserver
import tornado.ioloop
import tornado.options
import tornado.web
import tornado.websocket

import gi
gi.require_version('Gst', '1.0')
from gi.repository import GLib

from decoder_pipeline import DecoderPipeline
from decoder_ws import DecoderSocket



class Application(tornado.web.Application):
    def __init__(self):
        logging.info("Application: __init__")
        handlers = [
            (r"/", IndexHandler),
            (r"/webclient", ClientSocketHandler),
            (r"/decoder", DecoderSocketHandler)
        ]
        settings = dict(
            template_path=os.path.join(os.path.dirname(__file__), "templates"),
            static_path=os.path.join(os.path.dirname(__file__), "static")
        )
        tornado.web.Application.__init__(self, handlers, **settings)

        self.decoder_list = set()


class IndexHandler(tornado.web.RequestHandler):
    def get(self):
        logging.info("IndexHandler: GET request")
        self.render("index.html")


class ClientSocketHandler(tornado.websocket.WebSocketHandler):
    def __init__(self, application, request, **kwargs):
        super(ClientSocketHandler, self).__init__(
            application, request, **kwargs)
        self.id = str(uuid.uuid4())
        self.decoder_socket = None

    def open(self):
        logging.info("ClientSocketHandler[%s]: open()" % self.id)
        self.decoder_socket = None
        try:
            self.decoder_socket = self.application.decoder_list.pop()
            self.decoder_socket.set_client_socket(self)
            logging.info("ClientSocketHandler[%s]: decoder available: %s" % (
                self.id, self.decoder_socket.get_id()))
        except KeyError:
            # Raised when a mapping (dictionary) key is not found in the set of existing keys
            logging.warn(
                "ClientSocketHandler[%s]: no decocoder available" % self.id)
            message = dict(
                type="warning", data="No decoder available, try again later")
            self.write_message(json.dumps(message))
            self.close()

    def on_close(self):
        logging.info("ClientSocketHandler[%s]: on_close()" % self.id)
        if self.decoder_socket:
            self.decoder_socket.close()

    def on_message(self, message):
        logging.info(
            "ClientSocketHandler[%s]: on_message(): message(%s) of len=%s" % (self.id, type(message), len(message)))
        assert self.decoder_socket is not None
        if isinstance(message, bytes):
            self.decoder_socket.write_message(message, binary=True)
        elif isinstance(message, str):
            json_message = json.loads(str(message))
            if json_message["type"] == "eos" or json_message["type"] == "error":
                self.decoder_socket.close()
            else:
                if json_message["type"] == "init_request":
                    json_message["id"] = self.id
                self.decoder_socket.write_message(
                    json.dumps(json_message), binary=False)

    def get_id(self):
        return self.id


class DecoderSocketHandler(tornado.websocket.WebSocketHandler):
    def __init__(self, application, request, **kwargs):
        super(DecoderSocketHandler, self).__init__(
            application, request, **kwargs)
        self.id = str(uuid.uuid4())
        self.client_socket = None

    def open(self):
        logging.info("DecoderSocketHandler[%s]: open()" % self.id)
        self.application.decoder_list.add(self)
        self.client_socket = None

    def on_close(self):
        logging.info("DecoderSocketHandler[%s]: on_close()" % self.id)
        self.application.decoder_list.discard(self)
        if self.client_socket:
            self.client_socket.close()
        self.set_client_socket(None)

    def on_message(self, message):
        assert self.client_socket is not None
        logging.info(
            "DecoderSocketHandler[%s]: on_message(): message = %s" % (self.id, message))
        self.client_socket.write_message(message)

    def get_id(self):
        return self.id

    def set_client_socket(self, client_socket):
        if client_socket is not None:
            logging.info("DecoderSocketHandler[%s]: set_client_socket(): %s" % (
                self.id, client_socket.get_id()))
        else:
            logging.info(
                "DecoderSocketHandler[%s]: set_client_socket(None)" % self.id)
        self.client_socket = client_socket


def decoder_loop(url, decoder_pipeline):
    while True:
        ws = DecoderSocket(url, decoder_pipeline)
        try:
            logging.info("Connecting decoder websocket to the server")
            ws.connect()
            # Block the thread until the websocket has terminated
            ws.run_forever()
        except Exception:  # HandshakeError
            logging.error("Could not connect decoder websocket to the server")
            # Connection timeout
            time.sleep(5)
        # Fixes race condition
        time.sleep(1)


def main():
    logging.basicConfig(filename="server.log", filemode="w", level=logging.DEBUG,
                        format="%(asctime)s: %(levelname)8s: %(name)s: %(message)s ")
    # change filemode="a" to append to the end of the file
    logging.info("main()")

    # Parse global options from the command line
    from tornado.options import define, options
    define("port", default=20005, help="run on the given port", type=int)
    tornado.options.parse_command_line()

    # Initialize web application
    app = Application()

    # Start an HTTP server for this app
    app.listen(options.port)

    # DecoderPipeline
    conf = {"decoder": {"model": "test/models/english/voxforge/tri2b_mmi_b0.05/final.mdl",  # Acoustic model "final.mdl"
                        "lda-mat": "test/models/english/voxforge/tri2b_mmi_b0.05/final.mat",  # LDA transform data
                        # Word symbols "words.txt"
                        "word-syms": "test/models/english/voxforge/tri2b_mmi_b0.05/words.txt",
                        "fst": "test/models/english/voxforge/tri2b_mmi_b0.05/HCLG.fst",  # HCLG FST "HCLG.fst"
                        "silence-phones": "1:2:3:4:5"},  # Colon-separated IDs of silence phones "1:2:3:4:5"
            "use-vad": False,
            "out-dir": "tmp"}
    decoder_pipeline = DecoderPipeline(conf)

    # GLib MainLoop doesn't steal SIGINGT (unlike GObject)
    main_loop = GLib.MainLoop.new(None, False)
    # _thread.start_new_thread(main_loop.run, ())
    threading.Thread(target=main_loop.run, args=()).start()

    # Create decoder websocket
    host = "localhost" + ":" + str(options.port)
    url = "ws://" + host + "/decoder"
    #_thread.start_new_thread(decoder_loop, (url,))
    threading.Thread(target=decoder_loop, args=(url, decoder_pipeline)).start()

    # I/O event loop for non-blocking sockets
    tornado.ioloop.IOLoop.instance().start()


if __name__ == "__main__":
    main()
