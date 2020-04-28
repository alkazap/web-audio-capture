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


class DecoderPipeline():
    def __init__(self, conf: dict):
        self.log = logging.getLogger(self.__class__.__name__)
        self.log.info("Initializing DecoderPipeline using conf: %s" % (conf))

        # Audio cutter to split audio into non-silent bits
        self.use_cutter = conf.get('use-cutter', False)

        # Kaldi online decoder type (NNet if true, GMM if false)
        self.use_nnet = conf.get('use-nnet', False)

        # Location of the file to write
        self.outdir = conf.get('out-dir', None)
        if self.outdir:
            # doesn't exist
            if not os.path.exists(self.outdir):
                os.mkdir(self.outdir)
            # exists but not a dir
            elif not os.path.isdir(self.outdir):
                raise Exception(
                    "Output directory %s already exists as a file" % self.outdir)

        self.create_pipeline(conf)

        self.finished = False

        # To be assigned by the caller
        # For Kaldi GMM online gmm decoder:
        self.word_handler = None
        # For Kaldi NNet online decoder:
        self.result_handler = None
        self.full_result_handler = None
        # For both:
        self.eos_handler = None
        self.error_handler = None
        self.request_id = '<undefined>'

    def gst_plugin_path_checker(self):
        element_name = ''
        so_name = ''
        if self.use_nnet:
            element_name = 'kaldinnet2onlinedecoder'
            so_name = 'libgstkaldinnet2onlinedecoder.so'
        else:
            element_name = 'onlinegmmdecodefaster'
            so_name = 'libgstonlinegmmdecodefaster.so'
        print(
            "ERROR: Could not create the '%s' element" % element_name, file=sys.stderr)
        gst_plugin_path = os.environ.get('GST_PLUGIN_PATH')
        if (gst_plugin_path and os.path.isdir(gst_plugin_path)):
            if not os.path.isfile(os.path.join(gst_plugin_path, so_name)):
                print("ERROR: Could not find %s at %s\n"
                      "       Compile Kaldi GStreamer plugin and try again" % (so_name, gst_plugin_path), file=sys.stderr)
            else:
                print("Found %s at %s\n"
                      "Compile Kaldi GStreamer plugin and try again" % (so_name, gst_plugin_path), file=sys.stderr)
        else:
            print("ERROR: The environment variable GST_PLUGIN_PATH wasn't set correctly\n"
                  "           GST_PLUGIN_PATH=%s\n"
                  "       Make sure it includes Kaldi's 'src/gst-plugin' directory:\n"
                  "           export GST_PLUGIN_PATH=~/kaldi/src/gst-plugin\n"
                  "       Test if it worked:\n"
                  "           gst-inspect-1.0 %s" % (gst_plugin_path, element_name), file=sys.stderr)
        sys.exit(-1)

    def create_pipeline(self, conf):
        """
        PIPELINE:
            if use_cutter:
                [appsrc]->[decodebin]->[cutter]->[audioconvert]->[audioresample]->[tee(src1)]->[queue1]->[filesink]
                                                                                  [tee(src2)]->[queue2]->[asr]->[fakesink]
            else:
                [appsrc]->[decodebin]->[audioconvert]->[audioresample]->[tee(src1)]->[queue1]->[filesink]
                                                                        [tee(src2)]->[queue2]->[asr]->[fakesink]
        """

        ##########################################
        # Create new GStreamer element instances #
        ##########################################
        # Allow the application to feed buffers to a pipeline
        self.appsrc = Gst.ElementFactory.make('appsrc', 'appsrc')
        # Autoplug and decode to raw media
        self.decodebin = Gst.ElementFactory.make(
            'decodebin', 'decodebin')
        # Audio Cutter to split audio into non-silent bits
        self.cutter = Gst.ElementFactory.make('cutter', 'cutter')
        # Convert audio to different formats
        self.audioconvert = Gst.ElementFactory.make(
            'audioconvert', 'audioconvert')
        # Resamples audio
        self.audioresample = Gst.ElementFactory.make(
            'audioresample', 'audioresample')
        # 1-to-N pipe fitting
        self.tee = Gst.ElementFactory.make('tee', 'tee')
        # Simple data queue
        self.queue1 = Gst.ElementFactory.make('queue', 'queue1')
        self.queue2 = Gst.ElementFactory.make('queue', 'queue2')
        # Write stream to a file
        self.filesink = Gst.ElementFactory.make('filesink', 'filesink')
        # Black hole for data
        self.fakesink = Gst.ElementFactory.make('fakesink', 'fakesink')
        # Convert speech to text
        if self.use_nnet:
            self.asr = Gst.ElementFactory.make('kaldinnet2onlinedecoder', 'asr')
        else: 
            self.asr = Gst.ElementFactory.make('onlinegmmdecodefaster', 'asr')

        if not self.asr:
            self.gst_plugin_path_checker()

        ########################
        # Customize properties #
        ########################
        # Whether to act as a live source (default: false)
        self.appsrc.set_property('is-live', True)

        if self.use_cutter:
            # Volume threshold before trigger (default: 0.1)
            self.cutter.set_property('threshold', 0.01)
            # Length of drop below threshold before cut_stop (default: 500*1000*1000 nanosecs)
            self.cutter.set_property('run-length', 1000 * 1000000)
            # Length of pre-recording buffer (default: 200*1000*1000 nanosecs)
            self.cutter.set_property('pre-length', 1000 * 1000000)
            # Do we leak buffers when below threshold? (default: false)
            self.cutter.set_property('leaky', False)

        config_decoder = conf.get('decoder', dict())
        if self.use_nnet:
            # Must be set first
            self.asr.set_property('use-threaded-decoder', config_decoder.pop('use-threaded-decoder', False))
            self.log.info("Set Kaldi's decoder property: %s = %s" % ('use-threaded-decoder', self.asr.get_property('use-threaded-decoder')))
            self.asr.set_property('nnet-mode', config_decoder.pop('nnet-mode', 2))
            self.log.info("Set Kaldi's decoder property: %s = %s" % ('nnet-mode', self.asr.get_property('nnet-mode')))

        for (name, value) in config_decoder.items():
            self.asr.set_property(name, value)
            self.log.info("Set Kaldi's decoder property: %s = %s" % (name, self.asr.get_property(name)))

        # initially silence the decoder
        self.asr.set_property('silent', True)

        # Location of the file to write (default: null)
        self.filesink.set_property('location', '/dev/null')

        ######################
        # Build the pipeline #
        ######################
        # Create the empty pipeline
        self.pipeline = Gst.Pipeline.new('pipeline')
        if not self.pipeline:
            print("ERROR: Could not create pipeline", file=sys.stderr)
            sys.exit(-1)

        # Add elements to the pipeline
        for element in [self.appsrc, self.decodebin, self.cutter, self.audioconvert, self.audioresample,
                        self.tee, self.queue1, self.queue2, self.asr, self.filesink, self.fakesink]:
            if element:
                self.log.info("Adding '%s' to the pipeline" %
                              (element.get_name()))
                self.pipeline.add(element)
            else:
                print("ERROR: Could not create the element '%s'" %
                      element.get_name(), file=sys.stderr)
                sys.exit(-1)

        # Link elements with each other (src to dst)
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


        if self.use_cutter:
            if not self.cutter.link(self.audioconvert):
                print(
                    "ERROR: 'cutter' and 'audioresample' could not be linked", file=sys.stderr)
                sys.exit(-1)

        # Add handlers for 'pad-added' and 'pad-removed' signals of decodebin
        self.decodebin.connect('pad-added', self.pad_added_handler)
        self.decodebin.connect('pad-removed', self.pad_removed_handler)

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
        self.bus.connect('message::eos', self.eos_handler)
        self.bus.connect('message::error', self.error_handler)
        self.bus.connect('sync-message', self.sync_message_handler)

        if self.use_nnet:
            self.asr.connect('partial-result', self.partial_result_handler)
            self.asr.connect('final-result', self.final_result_handler)
            self.asr.connect('full-final-result', self.full_final_result_handler)
        else:
            self.asr.connect('hyp-word', self.hyp_word_handler)

        ret = self.pipeline.set_state(Gst.State.READY)
        if ret == Gst.StateChangeReturn.FAILURE:
            print("ERROR: Unable to set the pipeline to the READY state",
                  file=sys.stderr)
            sys.exit(-1)
        else:
            self.log.info("Setting pipeline to READY: %s" %
                          (Gst.Element.state_change_return_get_name(ret)))

    def pad_added_handler(self, element, pad):
        """
        'pad-added' signal callback
        """
        self.log.info("Got 'pad-added' signal from %s" % (element.get_name()))
        if element is self.decodebin:
            if self.use_cutter:
                # Link decodebin's src pad to cutter's sink
                cutter_pad = self.cutter.get_static_pad('sink')
                if not cutter_pad.is_linked():
                    if pad.link(cutter_pad) is not Gst.PadLinkReturn.OK:
                        print("ERROR: 'decodebin' and 'cutter' could not be linked",
                              file=sys.stderr)
                        self.pipeline.set_state(Gst.State.NULL)
                        sys.exit(-1)
                    else:
                        self.log.info("Linked 'decodebin' to 'cutter'")
                else:
                    self.log.warning("cutter's sink pad is already linked")
            else:
                # Link decodebin's src pad to audioconvert's sink
                audioconvert_pad = self.audioconvert.get_static_pad('sink')
                if not audioconvert_pad.is_linked():
                    if pad.link(audioconvert_pad) is not Gst.PadLinkReturn.OK:
                        print(
                            "ERROR: 'decoder' and 'audioconvert' could not be linked", file=sys.stderr)
                        self.pipeline.set_state(Gst.State.NULL)
                        sys.exit(-1)
                    else:
                        self.log.info("Linked 'decodebin' to 'audioconvert'")
                else:
                    self.log.warning(
                        "audioconvert's sink pad is already linked")

            ret = self.decodebin.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                print("ERROR: Unable to set decodebin to the PLAYING state",
                      file=sys.stderr)
                self.pipeline.set_state(Gst.State.NULL)
                sys.exit(-1)
            else:
                self.log.info("Setting decodebin to PLAYING: %s" %
                              (Gst.Element.state_change_return_get_name(ret)))

            Gst.debug_bin_to_dot_file(
                self.pipeline, Gst.DebugGraphDetails.ALL, '%s_decodebin' % self.request_id)

    def pad_removed_handler(self, element, pad):
        """
        'pad-removed' signal callback
        """
        pad_names = ['UNKNOWN', 'SRC', 'SINK']
        pad_name = pad_names[pad.get_direction()]
        self.log.info("Removed %s pad from %s" %
                      (pad_name, element.get_name()))

    def init_request(self, id, caps_str):
        """
        Sets appsrc caps property for new request, changes filesink location property accordin to outdir, 
        starts transitioning pipeline to PLAYING state, and pushes empty buffer to appcrs.
        """
        self.request_id = id
        self.log.info("Initializing request: %s" % (self.request_id))

        # caps (capabilities) is media type (or content type)
        if caps_str and len(caps_str) > 0:
            self.appsrc.set_property('caps', Gst.caps_from_string(caps_str))
            self.log.info("Set appsrc property: %s = %s" % ('caps', self.appsrc.get_property('caps').to_string()))
        else:
            self.appsrc.set_property('caps', None)

        # make sure decoder is not silent
        self.asr.set_property('silent', False)

        if self.outdir:  # change filesink location property /dev/null -> outdir
            self.pipeline.set_state(Gst.State.PAUSED)
            self.filesink.set_state(Gst.State.NULL)
            self.filesink.set_property(
                'location', '%s/%s.raw' % (self.outdir, self.request_id))
            self.filesink.set_state(Gst.State.PLAYING)

        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            print("ERROR: Unable to set the pipeline to the PLAYING state",
                  file=sys.stderr)
            sys.exit(-1)
        else:
            self.log.info("Setting pipeline to PLAYING: %s" %
                          (Gst.Element.state_change_return_get_name(ret)))

        ret = self.filesink.set_state(Gst.State.PLAYING)
        if self.filesink.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            print("ERROR: Unable to set the filesink to the PLAYING state",
                  file=sys.stderr)
            sys.exit(-1)
        else:
            self.log.info("Setting filesink to PLAYING: %s" %
                          (Gst.Element.state_change_return_get_name(ret)))
        # Create a new empty buffer
        #buf = Gst.Buffer.new_allocate(None, 0, None)
        #if buf:
        #    self.log.info("Pushing empty buffer to pipeline")
        #    # Push empty buffer into the appsrc (to avoid hang on client diconnect)
        #    self.appsrc.emit('push-buffer', buf)

        self.finished = False

        Gst.debug_bin_to_dot_file(
            self.pipeline, Gst.DebugGraphDetails.ALL, '%s_init' % self.request_id)

    def process_data(self, data):
        """
        Passes data to appsrc with the 'push-buffer' action signal.
        """
        self.log.info("Pushing buffer of size %d to pipeline" % (len(data)))
        # Create a new empty buffer
        buf = Gst.Buffer.new_allocate(None, len(data), None)
        # Copy data to buffer at 0 offset
        buf.fill(0, data)
        if buf:
            # Push buffer into the appsrc
            self.appsrc.emit('push-buffer', buf)

    def end_request(self):
        """
        Emits 'end-of-stream' action signal after the app has finished putting data into appsrc.
        After this call, no more buffers can be pushed into appsrc until the state of the appsrc has gone through READY.
        """
        self.log.info("Pushing EOS to pipeline")
        # Notify appsrc that no more buffer are available
        self.appsrc.emit('end-of-stream')

    def finish_request(self):
        """
        Sets pipeline to the NULL state to free any resources it has allocated.
        Called by 'error_handler' and 'eos_handler' methods.
        """
        self.log.info("Finishing request")
        self.asr.set_property('silent', True)
        if self.outdir:  # change filesink location property outdir -> /dev/null
            self.pipeline.set_state(Gst.State.PAUSED)
            self.filesink.set_state(Gst.State.NULL)
            self.filesink.set_property('location', '/dev/null')
            self.filesink.set_state(Gst.State.PLAYING)
        self.pipeline.set_state(Gst.State.NULL)
        self.request_id = '<undefined>'
        self.finished = True

    def partial_result_handler(self, asr, result):
        """
        'kaldinnet2onlinedecoder' element's 'partial-result' signal callback
        """
        self.log.info("Got partial result: %s" % (result))
        if self.result_handler:
            self.result_handler(result, False)
            
    def final_result_handler(self, asr, result):
        """
        'kaldinnet2onlinedecoder' element's 'final-result' signal callback
        """
        self.log.info("Got final result: %s" % (result))
        if self.result_handler:
            self.result_handler(result, True)

    def full_final_result_handler(self, asr, json_result):
        """
        'kaldinnet2onlinedecoder' element's 'full-final-result' signal callback
        """
        self.log.info("Got full final result: %s" % (json_result))
        if self.result_handler:
            self.full_result_handler(json_result)

    def hyp_word_handler(self, asr, word):
        """
        'onlinegmmdecodefaster' element's 'hyp-word' signal callback
        """
        self.log.info("Got word: %s" % (word))
        if self.word_handler:  # from caller
            self.word_handler(word)

    def eos_handler(self, bus, msg):
        """
        'message::eos' message callback
        """
        self.log.info("End-Of-Stream reached")
        self.finish_request()
        if self.eos_handler:
            self.eos_handler()

    def error_handler(self, bus, msg):
        """
        'message::error' message callback
        """
        self.error, debug_info = msg.parse_error()
        self.log.error("Error received from element %s: %s" %
                       (msg.src.get_name(), self.error.message))
        if debug_info:
            self.log.debug("Debugging information: %s" % (debug_info))
        self.finish_request()
        if self.error_handler:
            self.error_handler(self.error.message)

    def sync_message_handler(self, bus, msg):
        """
        'sync-message' message callback
        """
        if msg.type is Gst.MessageType.ELEMENT:
            if msg.src is self.cutter:
                above = msg.get_structure().get_value('above')
                self.log.info("Got synch-msg of type %s from %s: above=%s" %
                              (Gst.message_type_get_name(msg.type), msg.src.get_name(), above))
                if above:
                    # send incoming audio to the decoder
                    self.asr.set_property('silent', False)
                else:
                    # don't send incoming audio to the decoder
                    self.asr.set_property('silent', True)
        if msg.type is Gst.MessageType.STATE_CHANGED:
            old_state, new_state, pending_state = msg.parse_state_changed()
            self.log.info("Got sync-msg of type %s from %s: %s -> %s" %
                          (Gst.message_type_get_name(msg.type), msg.src.get_name(),
                           Gst.Element.state_get_name(old_state), Gst.Element.state_get_name(new_state)))
        elif msg.type is Gst.MessageType.STREAM_STATUS:
            status_type, owner = msg.parse_stream_status()
            self.log.info("Got sync-msg of type %s from %s: status_type=%s" %
                          (Gst.message_type_get_name(msg.type), owner.get_name(), status_type))
        elif msg.type is Gst.MessageType.TAG:
            tag_list = msg.parse_tag()
            self.log.info("Got sync-msg of type %s from %s: %s" %
                          (Gst.message_type_get_name(msg.type), msg.src.get_name(), tag_list.to_string()))
        else:
            self.log.info("Got sync-msg of type %s from %s" %
                          (Gst.message_type_get_name(msg.type), msg.src.get_name()))

    def set_result_handler(self, handler):
        self.result_handler = handler

    def set_full_result_handler(self, handler):
        self.full_result_handler = handler

    def set_word_handler(self, handler):
        self.word_handler = handler

    def set_eos_handler(self, handler):
        self.eos_handler = handler

    def set_error_handler(self, handler):
        self.error_handler = handler
