import json
import logging
import time

from ws4py.client.threadedclient import WebSocketClient
import ws4py.messaging

from decoder_pipeline import DecoderPipeline


class DecoderSocket(WebSocketClient):
    def __init__(self, url, decoder_pipeline):
        logging.info("DecoderSocket: __init__(url=%s)" % url)
        self.decoder_pipeline = decoder_pipeline
        super(DecoderSocket, self).__init__(url)
        self.decoder_pipeline.set_word_handler(self.word_handler)
        self.decoder_pipeline.set_eos_handler(self.eos_handler)
        self.decoder_pipeline.set_error_handler(self.error_handler)
        self.request_id = "<undefined>"

    def opened(self):
        logging.info(
            "DecoderSocket: opened(): called by the server when the upgrade handshake has succeeded")

    def closed(self, code=1000, reason=""):
        logging.info(
            "DecoderSocket: closed(): WebSocket stream and connection are finally closed")
        self.decoder_pipeline.finish_request()

    def received_message(self, message):
        logging.info(
            "DecoderSocket: received_message(): message(%s) of len=%s" % (type(message), len(message)))
        if isinstance(message, ws4py.messaging.BinaryMessage):
            self.decoder_pipeline.process_data(message.data)
        elif isinstance(message, ws4py.messaging.TextMessage):
            json_message = json.loads(str(message))
            logging.info(
                "DecoderSocket: received_message(): json_message=%s" % json_message)
            if json_message["type"] == "caps":
                self.request_id = json_message["id"]
                caps = json_message["data"]
                self.decoder_pipeline.init_request(self.request_id, caps)
            elif json_message["type"] == "eos":
                self.decoder_pipeline.end_request()

    def word_handler(self, word):
        logging.info("DecoderSocket: word_handler(): %s" % word)
        message = dict(type="word", data=word)
        self.send(json.dumps(message))

    def eos_handler(self):
        logging.info("DecoderSocket: eos_handler()")
        message = dict(type="eos")
        self.send(json.dumps(message))
        self.close()

    def error_handler(self, error):
        logging.info("DecoderSocket: error_handler(): %s" % error)
        message = dict(type="error", data=error)
        self.send(json.dumps(message))
        self.close()
