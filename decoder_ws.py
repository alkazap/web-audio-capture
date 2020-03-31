from ws4py.client.threadedclient import WebSocketClient
from decoder_pipeline import DecoderPipeline
import logging
import json
import time

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
        logging.info("DecoderSocket: opened(): called by the server when the upgrade handshake has succeeded")

    def closed(self, code=1000, reason=""):
        logging.info("DecoderSocket: closed(): WebSocket stream and connection are finally closed")
        #self.decoder_pipeline.finish_request()

    def received_message(self, message):
        logging.info("DecoderSocket: received_message(): message = %s" % message)
        json_message = json.loads(str(message))
        if json_message["type"] == "init_request":
            self.request_id = json_message["id"]
            caps = json_message["caps"]
            self.decoder_pipeline.init_request(self.request_id, caps)
            filename = "test/data/" + json_message["file"]
            rate = int(json_message["rate"])
            self.produce_data(filename, rate)

    def produce_data(self, filename, rate):
        logging.info("DecoderSocket: produce_data(): filename = %s, rate = %s" %(filename, rate))
        with open(str(filename), 'rb') as f:
            logging.info("DecoderSocket: produce_data(): started producing data")
            cunk_size = int(rate/2)
            for chunk in iter(lambda: f.read(rate), b''):
                time.sleep(0.25)
                self.decoder_pipeline.process_data(chunk)
            logging.info("DecoderSocket: produce_data(): finished producing data")
            # No more data available
            self.decoder_pipeline.end_request()
            logging.info("DecoderSocket: produce_data(): EOS")

    def word_handler(self, word):
        logging.info("DecoderSocket: word_handler(): %s" %word)
        message = dict(type="word", data=word)
        self.send(json.dumps(message))

    def eos_handler(self):
        logging.info("DecoderSocket: eos_handler()")
        message = dict(type="eos")
        self.send(json.dumps(message))
        #self.close()

    def error_handler(self, error):
        logging.info("DecoderSocket: error_handler(): %s" %error)
        message = dict(type="error", data=error)
        self.send(json.dumps(message))
        self.close()
