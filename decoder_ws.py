import argparse
import json
import logging
import os
import threading
import time
import yaml

import ws4py.messaging
from ws4py.client.threadedclient import WebSocketClient

from decoder_pipeline import DecoderPipeline


SILENCE_TIMEOUT = 10
CONNECT_TIMEOUT = 5


class DecoderSocket(WebSocketClient):
    def __init__(self, url, decoder_pipeline):
        self.url = url
        self.decoder_pipeline = decoder_pipeline
        WebSocketClient.__init__(self, url=url)
        self.daemon = False
        self.log = logging.getLogger(self.__class__.__name__)
        self.decoder_pipeline.set_word_handler(self.word_handler)
        self.decoder_pipeline.set_eos_handler(self.eos_handler)
        self.decoder_pipeline.set_error_handler(self.error_handler)
        self.request_id = '<undefined>'
        self.last_response_time = time.time()
        self.running = threading.Event()
        self.log.info("Created new decoder WebSocket(%s)" % (url))

    def timeout_guard(self):
        while(self.running.is_set()):
            if (time.time() - self.last_response_time) > SILENCE_TIMEOUT:
                self.log.warning(
                    "More than %d secs passed since last decoder response" % (SILENCE_TIMEOUT))
                self.decoder_pipeline.end_request()
                message = dict(type='warning', data="silence timeout")
                try:
                    self.send(json.dumps(message))
                except RuntimeError:
                    self.log.error("Cannot send on a terminated websocket")
                finally:
                    break
            time.sleep(1)

    def opened(self):
        self.log.info("The upgrade handshake has succeeded")
        self.request_id = '<undefined>'
        self.last_response_time = time.time()
        self.running.clear()

    def closed(self, code=1000, reason=''):
        self.log.info(
            "WebSocket stream and connection are finally closed: code=%d, reason=%s" % (code, reason))
        if self.running.is_set:
            self.log.info("DecoderPipeline is still running, end request")
            self.decoder_pipeline.end_request()
        while self.running.is_set():
            if (time.time() - self.last_response_time) > CONNECT_TIMEOUT:
                self.log.info("Giving up after %d secs" % CONNECT_TIMEOUT)
                self.running.clear()
            time.sleep(1)
        if not self.decoder_pipeline.finished:
            self.log.info("Requesting DecoderPipeline to finish")
            self.decoder_pipeline.finish_request()

    def received_message(self, message):
        self.log.info("message(%s) of len=%s" % (type(message), len(message)))
        if isinstance(message, ws4py.messaging.BinaryMessage):
            self.decoder_pipeline.process_data(message.data)
        elif isinstance(message, ws4py.messaging.TextMessage):
            json_message = json.loads(str(message))
            self.log.info("json_message=%s" % (json_message))
            if json_message['type'] == 'caps':
                self.request_id = json_message['id']
                caps = json_message['data']
                self.decoder_pipeline.init_request(self.request_id, caps)
                self.last_response_time = time.time()
                threading.Thread(target=self.timeout_guard,
                                 name='TimeoutGuard', daemon=False).start()
                self.running.set()
            elif json_message['type'] == 'eos':
                self.decoder_pipeline.end_request()

    def word_handler(self, word):
        self.last_response_time = time.time()
        self.log.info("Got word from decoder_pipeline: %s" % (word))
        message = dict(type='word', data=word)
        try:
            self.send(json.dumps(message))
        except RuntimeError:
            self.log.error("Cannot send on a terminated websocket")

    def eos_handler(self):
        self.running.clear()
        self.log.info("Got EOS from decoder_pipeline")
        message = dict(type='eos')
        try:
            self.send(json.dumps(message))
            self.close()
        except RuntimeError:
            self.log.error("Cannot send on a terminated websocket")

    def error_handler(self, error):
        self.running.clear()
        self.log.info("Got error from decoder_pipeline: %s" % (error))
        message = dict(type='error', data=error)
        try:
            self.send(json.dumps(message))
            self.close()
        except RuntimeError:
            self.log.error("Cannot send on a terminated websocket")
