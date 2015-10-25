#!/usr/bin/python
# -*- coding: utf-8 -*-

"""
This file contains the classes used to send videostreams to Twitch
"""
from __future__ import print_function
import numpy as np
import subprocess as sp
import signal
import threading
import sys
import Queue
import time

class TwitchOutputStream(object):
    """
    Initialize a TwitchOutputStream object and starts the pipe.
    The stream is only started on the first frame.

    :param twitch_stream_key:
    :type twitch_stream_key:
    :param width: the width of the videostream (in pixels)
    :type width: int
    :param height: the height of the videostream (in pixels)
    :type height: int
    :param fps: the number of frames per second of the videostream
    :type fps: float
    :param ffmpeg_binary: the binary to use to create a videostream
        This is usually ffmpeg, but avconv on some (older) platforms
    :type ffmpeg_binary: String
    :param verbose: show ffmpeg output in stdout
    :type verbose: boolean
    """
    def __init__(self,
                 twitch_stream_key,
                 width=640,
                 height=480,
                 fps=30.,
                 ffmpeg_binary="ffmpeg",
                 verbose=False):
        self.twitch_stream_key = twitch_stream_key
        self.width = width
        self.height = height
        self.fps = fps
        self.pipe = None
        self.ffmpeg_binary = ffmpeg_binary
        self.verbose = verbose
        try:
            self.reset()
        except OSError:
            print("There seems to be no %s available" % ffmpeg_binary)
            if ffmpeg_binary == "ffmpeg":
                print("ffmpeg can be installed using the following"
                      "commands")
                print("> sudo add-apt-repository "
                      "ppa:mc3man/trusty-media")
                print("> sudo apt-get update && "
                      "sudo apt-get install ffmpeg")
            sys.exit(1)

    def reset(self):
        """
        Reset the videostream by restarting ffmpeg
        """

        if self.pipe is not None:
            # Close the previous stream
            try:
                self.pipe.send_signal(signal.SIGINT)
            except OSError:
                pass

        command = [
            self.ffmpeg_binary,
            '-loglevel', 'verbose',
            '-y',       # overwrite previous file/stream
            # '-re',    # native frame-rate
            '-analyzeduration', '1',
            '-f', 'rawvideo',
            '-r', '%d' % self.fps,  # set a fixed frame rate
            '-vcodec', 'rawvideo',
            # size of one frame
            '-s', '%dx%d' % (self.width, self.height),
            '-pix_fmt', 'rgb24',  # The input are raw bytes
            '-i', '-',            # The input comes from a pipe

            # Twitch needs to receive sound in their streams!
            # '-an',            # Tells FFMPEG not to expect any audio
            '-ar', '8000',
            '-ac', '1',
            '-f', 's16le',
            '-i', '/dev/zero',  # silence alternative, works forever.
            # '-i','http://stream1.radiostyle.ru:8001/tunguska',
            # '-filter_complex',
            # '[0:1][1:0]amix=inputs=2:duration=first[all_audio]'

            # VIDEO CODEC PARAMETERS
            '-vcodec', 'libx264',
            '-r', '%d' % self.fps,
            '-b:v', '3000k',
            '-s', '%dx%d' % (self.width, self.height),
            '-preset', 'faster', '-tune', 'zerolatency',
            '-crf', '23',
            '-pix_fmt', 'yuv420p',
            # '-force_key_frames', r'expr:gte(t,n_forced*2)',
            '-minrate', '3000k', '-maxrate', '3000k',
            '-bufsize', '12000k',
            '-g', '60',     # key frame distance
            '-keyint_min', '1',
            # '-filter:v "setpts=0.25*PTS"'
            # '-vsync','passthrough',

            # AUDIO CODEC PARAMETERS
            '-acodec', 'libmp3lame', '-ar', '44100', '-b:a', '160k',
            # '-bufsize', '8192k',
            '-ac', '1',
            # '-acodec', 'aac', '-strict', 'experimental',
            # '-ab', '128k', '-ar', '44100', '-ac', '1',
            # '-async','44100',
            # '-filter_complex', 'asplit', #for audio sync?

            # STORE THE VIDEO PARAMETERS
            # '-vcodec', 'libx264', '-s', '%dx%d'%(width, height),
            # '-preset', 'libx264-fast',
            # 'my_output_videofile2.avi'

            # MAP THE STREAMS
            # use only video from first input and only audio from second
            '-map', '0:v', '-map', '1:a',

            # NUMBER OF THREADS
            '-threads', '2',

            # STREAM TO TWITCH
            '-f', 'flv', 'rtmp://live-ams.twitch.tv/app/%s' %
            self.twitch_stream_key
            ]

        fh = open("/dev/null", "w")     # Throw away stream
        if self.verbose:
            fh = None    # uncomment this line for viewing ffmpeg output
        self.pipe = sp.Popen(
            command,
            stdin=sp.PIPE,
            stderr=fh,
            stdout=fh)

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        # sigint so avconv can clean up the stream nicely
        self.pipe.send_signal(signal.SIGINT)
        # waiting doesn't work because of reasons I don't know
        # self.pipe.wait()

    def send_frame(self, frame):
        """send frame of shape (height, width, 3)
        with values between 0 and 1

        :param frame: array containing the frame.
        :type frame: numpy array with shape (height, width, 3)
            containing values between 0.0 and 1.0
        """
        if self.pipe.poll():
            self.reset()
        assert frame.shape == (self.height, self.width, 3)

        frame = np.clip(255*frame, 0, 255).astype('uint8')
        self.pipe.stdin.write(frame.tostring())


class TwitchOutputStreamRepeater(TwitchOutputStream):
    """
    This stream makes sure a steady framerate is kept by repeating the
    last frame when needed.

    Note: this will not make for a stable, stutter-less stream!
     It does not keep a buffer and you cannot synchronize using this
     stream. Use TwitchBufferedOutputStream for this.
    """
    def __init__(self, *args, **kwargs):
        super(TwitchOutputStreamRepeater, self).__init__(*args, **kwargs)
        self.lastframe = np.ones((self.height, self.width, 3))
        self._send_me_last_frame_again()     # Start sending the stream

    def _send_me_last_frame_again(self):
        try:
            super(TwitchOutputStreamRepeater,
                  self).send_frame(self.lastframe)
        except IOError:
            # stream has been closed.
            # This function is still called once when that happens.
            pass
        else:
            # send the next frame at the appropriate time
            threading.Timer(1./self.fps,
                            self._send_me_last_frame_again).start()

    def send_frame(self, frame):
        """send frame of shape (height, width, 3)
        with values between 0 and 1

        :param frame: array containing the frame.
        :type frame: numpy array with shape (height, width, 3)
            containing values between 0.0 and 1.0
        """
        self.lastframe = frame


class TwitchBufferedOutputStream(TwitchOutputStream):
    """
    This stream makes sure a steady framerate is kept by buffering
    frames. Make sure not to have too many frames in buffer, since it
    will increase the memory load considerably!

    Adding frames is thread safe.
    """
    def __init__(self, *args, **kwargs):
        super(TwitchBufferedOutputStream, self).__init__(*args, **kwargs)
        self.last_frame = np.ones((self.height, self.width, 3))
        self.last_frame_time = None
        self.next_send_time = None
        self.frame_counter = 0
        self.q = Queue.PriorityQueue()
        self.t = threading.Timer(0.0, self._send_me_last_frame_again)
        self.t.daemon = True
        self.t.start()

    def _send_me_last_frame_again(self):
        start_time = time.time()
        try:
            frame = self.q.get_nowait()
            # frame[0] is frame count of the frame
            # frame[1] is the frame
            frame = frame[1]
        except IndexError:
            frame = self.last_frame
        except Queue.Empty:
            frame = self.last_frame
        else:
            self.last_frame = frame

        try:
            super(TwitchBufferedOutputStream, self).send_frame(frame)
        except IOError:
            # stream has been closed.
            # This function is still called once when that happens.
            pass

        # send the next frame at the appropriate time
        if self.next_send_time is None:
            threading.Timer(1./self.fps,
                            self._send_me_last_frame_again).start()
            self.next_send_time = start_time + 1./self.fps
        else:
            self.next_send_time += 1./self.fps
            next_event_time = self.next_send_time - start_time
            if next_event_time > 0:
                threading.Timer(next_event_time,
                                self._send_me_last_frame_again).start()
            else:
                # we should already have sent something!
                #
                # not allowed for recursion problems :-(
                # (maximum recursion depth)
                # self.send_me_last_frame_again()
                #
                # other solution:
                self.t = threading.Thread(
                    target=self._send_me_last_frame_again).start()

    def send_frame(self, frame, frame_counter=None):
        """send frame of shape (height, width, 3)
        with values between 0 and 1

        :param frame: array containing the frame.
        :type frame: numpy array with shape (height, width, 3)
            containing values between 0.0 and 1.0
        :param frame_counter: frame position number within stream.
            Provide this when multi-threading to make sure frames don't
            switch position
        :type frame_counter: int
        """
        if frame_counter is None:
            frame_counter = self.frame_counter
            self.frame_counter += 1

        self.q.put((frame_counter, frame))
