import argparse
import os
import sys

def file_check(file: str):
    if file is not None and not os.path.isfile(file):
        print("File not found: %s " % (file), file=sys.stderr)

def parse_args(server: bool = False):
    parser = None
    # For server
    if (server):
        parser = argparse.ArgumentParser(
            description='WebSocket Server for Kaldi GStreamer online decoder plugin')
        parser.add_argument('-p', '--port', dest='port', type=int,
                            default=20005, help='Port to listen on')
        parser.add_argument('-c', '--certfile', dest='certfile', type=str, default='cert/cert1.pem',
                            help='Certificate file for secured SSL connection')
        parser.add_argument('-k', '--keyfile', dest='keyfile', type=str, default='cert/privkey1.pem',
                            help='Key file for secured SSL connection')
        parser.add_argument('-l', '--log', dest='log', type=str,
                        default='config/logging/server.yaml', help='Logging configuration YAML file')
    # For worker
    else:
        parser = argparse.ArgumentParser(
            description='Worker that creates GStreamer pipeline with Kaldi online decoder, and a WebSocket client')
        parser.add_argument('-l', '--log', dest='log', type=str,
                            default='config/logging/worker.yaml', help='Logging configuration YAML file')
        parser.add_argument('-u', '--url', dest='url', type=str,
                            default='wss://localhost:20005/decoder', help='URL to which the WebSocket server will respond')
    # For decoder 
    parser.add_argument('-o', '--conf', dest='conf', type=str,
                        default='config/decoder/daglo.yaml', help='Kaldi online decoder configuration YAML file')
    # For multiprocessing
    parser.add_argument('-m', '--mplog', dest='mplog', type=str,
                        default='config/logging/mplog.yaml', help='Logging configuration YAML file for multiprocessing')
    parser.add_argument('-n', '--nproc', dest='nproc', type=int, choices=range(0, os.cpu_count()),
                        default=1, help='Number of worker processes')
    args = parser.parse_args()

    files = [args.conf, args.log, args.mplog]
    if server:
        files.extend([args.certfile, args.keyfile])

    [file_check(file) for file in files]

    return args
