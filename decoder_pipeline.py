import logging
import os
import sys

import gi
# Ensure Gst gets loaded with version 1.0
gi.require_version('Gst', '1.0')
from gi.repository import GObject, Gst
# Initialize thread support in PyGObject
GObject.threads_init()
# Initialize GStreamer
Gst.init(None)

# Return a logger with the specified name, creating it if necessary
logger = logging.getLogger(__name__)


class DecoderPipeline():
    def __init__(self, conf=dict(), is_sync=True):
        logger.info("Initializing DecoderPipeline using conf: %s" % conf)
        # If use-vad is True: use energy-based VAD (Voice Activity Detection)
        # to apply frame weights for i-vector stats extraction
        self.use_cutter = conf.get("use-vad", False)
        self.create_pipeline(conf, is_sync)
        self.outdir = conf.get("out-dir", None)
        if self.outdir:
            if not os.path.exists(self.outdir):
                os.mkdir(self.outdir)
            elif not os.path.isdir(self.outdir):
                raise Exception(
                    "Output directory %s already exists as a file" % self.outdir)

        # Will be assigned by worker.py:
        self.word_handler = None
        self.eos_handler = None
        self.error_handler = None
        self.request_id = "<undefined>"

    def gst_plugin_path_checker(self):
        print(
            "ERROR: Could not create the 'onlinegmmdecodefaster' element", file=sys.stderr)
        gst_plugin_path = os.environ.get("GST_PLUGIN_PATH")
        if (gst_plugin_path and os.path.isdir(gst_plugin_path)):
            if not os.path.isfile(os.path.join(gst_plugin_path, "libgstonlinegmmdecodefaster.so")):
                print("ERROR: Could not find libgstonlinegmmdecodefaster.so at %s\n"
                      "       Compile Kaldi GStreamer plugin and try again" % gst_plugin_path, file=sys.stderr)
        else:
            print("ERROR: The environment variable GST_PLUGIN_PATH wasn't set correctly\n"
                  "           GST_PLUGIN_PATH=%s\n"
                  "       Make sure it includes Kaldi's 'src/gst-plugin' directory:\n"
                  "           export GST_PLUGIN_PATH=~/kaldi/src/gst-plugin\n"
                  "       Test if it worked:\n"
                  "           gst-inspect-1.0 onlinegmmdecodefaster" % gst_plugin_path, file=sys.stderr)

    def create_pipeline(self, conf, is_sync):
        """
        PIPELINE:
            if use_cutter:
                [appsrc]->[decodebin]->[cutter]->[audioconvert]->[audioresample]->[tee(src1)]->[queue1]->[filesink]
                                                                                  [tee(src2)]->[queue2]->[asr]->[fakesink]
            else:
                [appsrc]->[decodebin]->[audioconvert]->[audioresample]->[tee(src1)]->[queue1]->[filesink]
                                                                        [tee(src2)]->[queue2]->[asr]->[fakesink]
        """
        logger.info("Creating GStreamer pipeline")

        ##########################################
        # Create new GStreamer element instances #
        ##########################################
        # [appsrc]: used by app to insert data into pipeline
        self.appsrc = Gst.ElementFactory.make("appsrc", "appsrc")
        # [decodebin]: constructs a decoding pipeline
        self.decodebin = Gst.ElementFactory.make(
            "decodebin", "decodebin")
        # [cutter]: analyses the audio signal for periods of silence
        # the start and end of silence is signalled by bus messages named "cutter"
        # that contain two fields:
        #   "timestamp": the timestamp of the buffer that triggered the message
        #   "above": TRUE for begin of silence and FALSE for end of silence
        # if self.use_cutter:
        self.cutter = Gst.ElementFactory.make("cutter", "cutter")
        # [audioconvert]: converts raw audio buffers between various possible formats
        self.audioconvert = Gst.ElementFactory.make(
            "audioconvert", "audioconvert")
        # [audioresample]: resamples raw audio buffers to different sample rates
        self.audioresample = Gst.ElementFactory.make(
            "audioresample", "audioresample")
        # [tee]: using a configurable windowing function to enhance quality
        # split data to multiple pads (branchcning the data flow)
        self.tee = Gst.ElementFactory.make("tee", "tee")
        # [queue]: queue data to provide separate threads for each branch
        self.queue1 = Gst.ElementFactory.make("queue", "queue1")
        self.queue2 = Gst.ElementFactory.make("queue", "queue2")
        # [filesink]: write incoming data to a file in the local file system
        self.filesink = Gst.ElementFactory.make("filesink", "filesink")
        # [fakesink]: dummy sink that swallows everything
        self.fakesink = Gst.ElementFactory.make("fakesink", "fakesink")

        # Kaldi's ASR GStreamer plugin that acts as a filter,
        # taking raw audio as input and producing recognized word as output
        self.asr = Gst.ElementFactory.make(
            "onlinegmmdecodefaster", "asr")
        if not self.asr:
            self.gst_plugin_path_checker()
            """
            gst_plugin_path = conf.get("gst-plugin-path", None)
            if gst_plugin_path:
                gst_plugin_path = os.path.expanduser(gst_plugin_path)
                logger.info("Found GST_PLUGIN_PATH=%s in config file" % gst_plugin_path)
                os.environ["GST_PLUGIN_PATH"] = gst_plugin_path
                self.asr = Gst.ElementFactory.make(
                    "onlinegmmdecodefaster", "asr")
                if not self.asr:
                    self.gst_plugin_path_checker()
                    sys.exit(-1)
            else:
            """
            sys.exit(-1)

        ######################
        # Build the pipeline #
        ######################
        # Create the empty pipeline
        self.pipeline = Gst.Pipeline.new("pipeline")
        if not self.pipeline:
            print("ERROR: Could not create pipeline", file=sys.stderr)
            sys.exit(-1)

        # Add elements to the pipeline
        for element in [self.appsrc, self.decodebin, self.cutter, self.audioconvert, self.audioresample,
                        self.tee, self.queue1, self.queue2, self.asr, self.filesink, self.fakesink]:
            if element:
                logger.info("Adding '%s' to the pipeline" % element.get_name())
                self.pipeline.add(element)
            else:
                print("ERROR: Could not create the element '%s'" %
                      element.get_name(), file=sys.stderr)
                sys.exit(-1)

        # Link elements with each other (src to dst)
        logger.info("Linking GStreamer elements")
        if (not self.appsrc.link(self.decodebin) or
            not self.audioconvert.link(self.audioresample) or
            not self.audioresample.link(self.tee) or
            not self.tee.link(self.queue1) or
            not self.tee.link(self.queue2) or
            not self.queue1.link(self.filesink) or
            not self.queue2.link(self.asr) or
                not self.asr.link(self.fakesink)):
            print("ERROR: Elements could not be linked", file=sys.stderr)
            sys.exit(-1)
        # Add handler for 'pad-added' signal of decodebin
        self.decodebin.connect('pad-added', self._connect_decoder)

        ########################
        # Customize properties #
        ########################
        # Whether to act as a live source (default: false)
        self.appsrc.set_property("is-live", True)

        if self.use_cutter:
            # Determines whether incoming audio is sent to the decoder or not (default: false)
            self.asr.set_property("silent", True)
            # Do we leak buffers when below threshold? (default: false)
            self.cutter.set_property("leaky", False)
            # Length of pre-recording buffer (default: 200*1000*1000 nanosecs)
            self.cutter.set_property("pre-length", 1000 * 1000000)
            # Length of drop below threshold before cut_stop (default: 500*1000*1000 nanosecs)
            self.cutter.set_property("run-length", 1000 * 1000000)
            # Volume threshold before trigger (default: 0.1)
            self.cutter.set_property("threshold", 0.01)

        # Location of the file to write (default: null)
        self.filesink.set_property("location", "/dev/null")

        for (name, value) in conf.get("decoder", dict()).items():
            logger.info("Setting Kaldi's decoder property: %s = %s" %
                        (name, value))
            self.asr.set_property(name, value)

        ###################################
        # Create bus and connect handlers #
        ###################################
        # Retrieve bus to receive Gst.Message from the elemetins in the pipeline
        self.bus = self.pipeline.get_bus()

        # Add a bus signal watch to the default main context with the default priority
        # the bus will emit the 'message' signal for each message posted on the bus
        self.bus.add_signal_watch()

        # Instruct GStreamer to emit the 'sync-message' signal after running the bus's sync handler
        # 'sync-message' signal comes from the thread of whatever object posted the message
        self.bus.enable_sync_message_emission()

        # If the default GLib mainloop integration is used, it is possible
        # to connect to the 'message' signal on the bus in form of 'message::<type>'
        self.bus.connect('message::eos', self._on_eos)
        self.bus.connect('message::error', self._on_error)
        self.bus.connect('sync-message', self._on_sync_message)
        # Сalls '_on_element_message' method every time a new message is posted on the bus
        if is_sync:
            # self.bus.set_sync_handler(self.bus.sync_signal_handler)
            self.bus.connect('sync-message::element',
                             self._on_element_message)
        else:
            self.bus.connect('message::element', self._on_element_message)

        # Calls '_on_word' method whenever decoding plugin produces a new recognized word
        self.asr.connect('hyp-word', self._on_word)

        ret = self.pipeline.set_state(Gst.State.READY)
        if ret == Gst.StateChangeReturn.FAILURE:
            print("ERROR: Unable to set the pipeline to the READY state",
                  file=sys.stderr)
            sys.exit(-1)
        else:
            logger.info("Setting pipeline to READY: %s" %
                        Gst.Element.state_change_return_get_name(ret))

    def _connect_decoder(self, element, pad):
        """
        'pad-added' signal callback
        """
        logger.info("%s: Connecting audio decoder" % self.request_id)
        if self.use_cutter:
            # Link decodebin's src pad to cutter's sink
            if pad.link(self.cutter.get_static_pad("sink")) is not Gst.PadLinkReturn.OK:
                print("ERROR: 'decoder' and 'cutter' could not be linked",
                      file=sys.stderr)
                self.pipeline.set_state(Gst.State.NULL)
                sys.exit(-1)
            if not self.cutter.link(self.audioconvert):
                print(
                    "ERROR: 'cutter' and 'audioconvert' could not be linked", file=sys.stderr)
                self.pipeline.set_state(Gst.State.NULL)
                sys.exit(-1)
        else:
            # Link decodebin's src pad to audioconvert's sink
            if pad.link(self.audioconvert.get_static_pad("sink")) is not Gst.PadLinkReturn.OK:
                print(
                    "ERROR: 'decoder' and 'audioconvert' could not be linked", file=sys.stderr)
                self.pipeline.set_state(Gst.State.NULL)
                sys.exit(-1)

        logger.info("%s: Connected audio decoder" % self.request_id)

        ret = self.decodebin.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            print("ERROR: Unable to set the pipeline to the READY state",
                  file=sys.stderr)
            sys.exit(-1)
        else:
            logger.info("Setting decodebin to PLAYING: %s" %
                        Gst.Element.state_change_return_get_name(ret))

        Gst.debug_bin_to_dot_file(
            self.pipeline, Gst.DebugGraphDetails.ALL, "%s_connect_decoder" % self.request_id)

    def _on_sync_message(self, bus, msg):
        if msg.type is Gst.MessageType.STATE_CHANGED:
            old_state, new_state, pending_state = msg.parse_state_changed()
            logger.info("Got sync-msg of type %s from %s: %s -> %s" % (Gst.message_type_get_name(msg.type), msg.src.get_name(),
                                                                       Gst.Element.state_get_name(old_state), Gst.Element.state_get_name(new_state)))
        elif msg.type is Gst.MessageType.STREAM_STATUS:
            status_type, owner = msg.parse_stream_status()
            logger.info("Got sync-msg of type %s from %s: status_type=%s" %
                        (Gst.message_type_get_name(msg.type), owner.get_name(), status_type))
        elif msg.type is Gst.MessageType.TAG:
            tag_list = msg.parse_tag()
            logger.info("Got sync-msg of type %s from %s: %s" %
                        (Gst.message_type_get_name(msg.type), msg.src.get_name(), tag_list.to_string()))
        else:
            logger.info("Got sync-msg of type %s from %s" %
                        (Gst.message_type_get_name(msg.type), msg.src.get_name()))

    def _on_element_message(self, bus, msg):
        """
        'sync-message::element' message callback
        """
        if msg.has_name("cutter"):
            if msg.get_structure().get_value('above'):  # for begin of silence
                logger.info("LEVEL ABOVE")
                # don't send incoming audio to the decoder
                self.asr.set_property("silent", False)
            else:  # for end of silence
                logger.info("LEVEL BELOW")
                # send incoming audio to the decoder
                self.asr.set_property("silent", True)

    def _on_word(self, asr, word):
        """
        'hyp-word' signal callback
        """
        logger.info("%s: Got word: %s" %
                    (self.request_id, word))
        if self.word_handler:  # from worker.py
            self.word_handler(word)

    def _on_error(self, bus, msg):
        """
        'message::error' message callback
        """
        self.error, debug_info = msg.parse_error()
        logger.error("Error received from element %s: %s" %
                     (msg.src.get_name(), self.error.message))
        if debug_info:
            logger.debug("Debugging information: %s" % debug_info)
        self.finish_request()
        if self.error_handler:  # from worker.py
            self.error_handler(self.error.message)
        # GLib.clear_error()

    def _on_eos(self, bus, msg):
        """
        'message::eos' message callback
        """
        logger.info("%s: End-Of-Stream reached." % self.request_id)
        self.finish_request()
        if self.eos_handler:
            self.eos_handler()

    def finish_request(self):
        """
        Called by '_on_error' and '_on_eos' methods
        Also called from worker.py's finish_request()
        """
        logger.info('%s: Finishing request' % self.request_id)
        if self.outdir:  # change filesink location property outdir -> /dev/null
            self.filesink.set_state(Gst.State.NULL)
            self.filesink.set_property('location', "/dev/null")
            self.filesink.set_state(Gst.State.PLAYING)
        logger.info("Setting pipeline to NULL")
        self.pipeline.set_state(Gst.State.NULL)
        self.request_id = "<undefined>"

    def init_request(self, id, caps_str):
        """
        Called from worker.py's recieved_message() if STATE=CONNECTED
        """
        self.request_id = id
        logger.info("%s: Initializing pipeline" % (self.request_id))

        Gst.debug_bin_to_dot_file(
            self.pipeline, Gst.DebugGraphDetails.ALL, "%s_init_start" % self.request_id)

        # caps (capabilities) is media type (or content type)
        if caps_str and len(caps_str) > 0:
            self.appsrc.set_property("caps", Gst.caps_from_string(caps_str))
            logger.info("%s: Set caps to %s" % (self.request_id,
                                                self.appsrc.get_property("caps").to_string()))
        else:
            self.appsrc.set_property("caps", None)

        Gst.debug_bin_to_dot_file(
            self.pipeline, Gst.DebugGraphDetails.ALL, "%s_init_set_caps" % self.request_id)

        if self.outdir:  # change filesink location property /dev/null -> outdir
            self.pipeline.set_state(Gst.State.PAUSED)
            self.filesink.set_state(Gst.State.NULL)
            self.filesink.set_property(
                'location', "%s/%s.raw" % (self.outdir, id))
            self.filesink.set_state(Gst.State.PLAYING)

        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            print("ERROR: Unable to set the pipeline to the PLAYING state",
                  file=sys.stderr)
            sys.exit(-1)
        else:
            logger.info("Setting pipeline to PLAYING: %s" %
                        Gst.Element.state_change_return_get_name(ret))

        Gst.debug_bin_to_dot_file(
            self.pipeline, Gst.DebugGraphDetails.ALL, "%s_init_play_pipeline" % self.request_id)

        ret = self.filesink.set_state(Gst.State.PLAYING)
        if self.filesink.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            print("ERROR: Unable to set the filesink to the PLAYING state",
                  file=sys.stderr)
            sys.exit(-1)
        else:
            logger.info("Setting filesink to PLAYING: %s" %
                        Gst.Element.state_change_return_get_name(ret))

        Gst.debug_bin_to_dot_file(
            self.pipeline, Gst.DebugGraphDetails.ALL, "%s_init_play_filesink" % self.request_id)

        # Create a new empty buffer
        buf = Gst.Buffer.new_allocate(None, 0, None)
        if buf:
            logger.info("Pushing empty buffer to pipeline")
            # Push empty buffer into the appsrc (to avoid hang on client diconnect)
            self.appsrc.emit("push-buffer", buf)
        else:
            print("ERROR:init_request():the memory couldn’t be allocated",
                  file=sys.stderr)

        logger.info("%s: Pipeline initialized" % (self.request_id))
        Gst.debug_bin_to_dot_file(
            self.pipeline, Gst.DebugGraphDetails.ALL, "%s_init_push_empty_buf" % self.request_id)

    def process_data(self, data):
        """
        Passes data to appsrc with the "push-buffer" action signal.
        Called from worker.py's recieved_message() (default case)
        """
        logger.info('%s: Pushing buffer of size %d to pipeline' %
                    (self.request_id, len(data)))
        # Create a new empty buffer
        buf = Gst.Buffer.new_allocate(None, len(data), None)
        # Copy data to buffer at 0 offset
        buf.fill(0, data)
        if buf:
            # Push empty buffer into the appsrc
            self.appsrc.emit("push-buffer", buf)
        else:
            print("ERROR:process_data():the memory couldn’t be allocated",
                  file=sys.stderr)

    def end_request(self):
        """
        Emits "end-of-stream" action signal after the app has finished putting data into appsrc.
        After this call, no more buffers can be pushed into appsrc until a flushing seek occurs 
        or the state of the appsrc has gone through READY.
        Called from worker.py's recieved_message() if message is "EOS".
        """
        logger.info("%s: Pushing EOS to pipeline" % self.request_id)
        # Notify appsrc that no more buffer are available
        self.appsrc.emit("end-of-stream")

    def set_word_handler(self, handler):
        """
        Called from worker.py's __init__()
        """
        self.word_handler = handler

    def set_eos_handler(self, handler):
        """
        Called from worker.py's __init__()
        """
        self.eos_handler = handler

    def set_error_handler(self, handler):
        """
        Called from worker.py's __init__()
        """
        self.error_handler = handler

    def cancel(self):
        """
        Sends EOS (downstream) event to the pipeline. No more data is to be expected to follow 
        without either a STREAM_START event, or a FLUSH_STOP and a SEGMENT event.
        Called from worker.py's finish_request() if STATE!=FINISHED
        """
        logger.info("%s: Cancelling pipeline" % self.request_id)
        ret = self.pipeline.send_event(Gst.Event.new_eos())
        logger.info("%s: Cancelled pipeline, ret = %s" %
                    (self.request_id, str(ret)))
