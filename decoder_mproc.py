import os
import logging
import logging.config
import multiprocessing
import threading
import time
from typing import Any, List, Mapping

import yaml
from ws4py.exc import HandshakeError

from decoder_worker import DecoderWorker


class DecoderProcess(multiprocessing.Process):
    def __init__(self, queue: multiprocessing.Queue, conf_file: str, url: str, stop_event: multiprocessing.Event, **kwargs: Mapping[Any, Any]) -> None:
        multiprocessing.Process.__init__(self, **kwargs)
        self.queue = queue
        self.conf_file = conf_file
        self.url = url
        self.stop_event = stop_event

    def log_config(self) -> None:
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

    def run(self) -> None:
        self.log_config()
        decoder_worker = DecoderWorker(conf_file=self.conf_file, url=self.url)
        try:
            decoder_worker.run()
        except SystemExit:
            return
        try:
            self.log.info("Waiting for stop event")
            self.stop_event.wait()
            # Will never reach here
            self.log.info("Got stop event")
        except KeyboardInterrupt:
            self.log.info("Waiting for stop event")
            self.stop_event.wait()
            self.log.info("Got stop event")
            decoder_worker.stop()
            decoder_worker.cleanup()


class LoggerProcess(multiprocessing.Process):
    def __init__(self, queue: multiprocessing.Queue, conf_file: str, stop_event: multiprocessing.Event, **kwargs: Mapping[Any, Any]) -> None:
        multiprocessing.Process.__init__(self, **kwargs)
        self.queue = queue
        self.conf = {}
        with open(conf_file) as file:
            self.conf = yaml.safe_load(file)
        self.stop_event = stop_event

    def run(self) -> None:
        logging.config.dictConfig(self.conf)
        self.log = logging.getLogger(self.__class__.__name__)
        self.listener = logging.handlers.QueueListener(
            self.queue, *logging.getLogger().handlers, respect_handler_level=True)
        self.listener.start()
        try:
            self.log.info("Waiting for stop event")
            self.stop_event.wait()
            # Will never reach here
            self.log.info("Got stop event")
        except KeyboardInterrupt:
            self.log.info("Waiting for stop event")
            self.stop_event.wait()
            self.log.info("Got stop event")
            self.listener.stop()


def get_workers(queue: multiprocessing.Queue, conf_file: str, url: str, stop_event: multiprocessing.Event, num_workers: int = 1) -> List[DecoderProcess]:
    if (num_workers > os.cpu_count()):
        logging.warning(
            "Not allowed number of worker processes: %d" % num_workers)
        num_workers = os.cpu_count()
        logging.info("Set number of worker processes to %d" % num_workers)

    workers = []
    for i in range(num_workers):
        worker = DecoderProcess(queue=queue, conf_file=conf_file,
                                url=url, stop_event=stop_event, name='Worker%d' % (i+1))
        workers.append(worker)
        worker.start()

    return workers


def term_workers(workers: List[DecoderProcess]) -> None:
    for worker in workers:
        worker.join()
    while workers:
        worker = workers.pop()
        if worker.is_alive():
            print("%s(%s) process is alive" % (worker.name, worker.pid))
            worker.terminate()
        logging.info("%s(%s) process exitcode=%s" %
                     (worker.name, worker.pid, worker.exitcode))


def get_logger(queue: multiprocessing.Queue, conf_file: str, stop_event: multiprocessing.Event) -> LoggerProcess:
    logger = LoggerProcess(queue=queue, conf_file=conf_file,
                           stop_event=stop_event, name='Logger')
    logger.start()
    return logger


def term_logger(logger: LoggerProcess) -> None:
    logger.join()
    if logger.is_alive():
        print("%s(%s) process is alive" % (logger.name, logger.pid))
        logger.terminate()
    logging.info("%s(%s) process exitcode=%s" %
                 (logger.name, logger.pid, logger.exitcode))


def main():
    # Parse arguments
    from arg_parser import parse_args
    try:
        args = parse_args()
    except SystemExit:
        return

    # Multiprocessing
    multiprocessing.set_start_method('spawn')
    queue = multiprocessing.Queue(-1)
    stop_logger = multiprocessing.Event()
    stop_workers = multiprocessing.Event()
    logger = get_logger(queue=queue, conf_file=args.mplog,
                        stop_event=stop_logger)
    workers = get_workers(queue=queue, conf_file=args.conf,
                          url=args.url, stop_event=stop_workers, num_workers=args.nproc)
    try:
        term_workers(workers)
    except KeyboardInterrupt:
        stop_workers.set()
        term_workers(workers)
    finally:
        stop_logger.set()
        term_logger(logger)


if __name__ == '__main__':
    main()
