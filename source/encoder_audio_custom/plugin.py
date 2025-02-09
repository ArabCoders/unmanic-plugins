#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
    plugins.__init__.py

    Written by:               Josh.5 <jsunnex@gmail.com>
    Modified by:              Abdulmohsen <ACoders@Twitter>
    Date:                     5 Nov 2021, (4:05 PM)

    Copyright:
        Copyright (C) 2021 Josh Sunnex

        This program is free software: you can redistribute it and/or modify it under the terms of the GNU General
        Public License as published by the Free Software Foundation, version 3.

        This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the
        implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License
        for more details.

        You should have received a copy of the GNU General Public License along with this program.
        If not, see <https://www.gnu.org/licenses/>.

"""
import logging
import os

from unmanic.libs.unplugins.settings import PluginSettings

from encoder_audio_custom.lib.ffmpeg import StreamMapper, Probe, Parser

# Configure plugin logger
logger = logging.getLogger("Unmanic.Plugin.encoder_audio_custom")


class Settings(PluginSettings):
    settings = {
        "advanced": False,
        "custom_options": "",
        "if_not_found": "opus",
        "use_codec_lib": "libopus",
        "bitrate": 0,
    }

    def __init__(self):
        self.form_settings = {
            "advanced": {
                "label": "Write your own FFmpeg params",
            },
            "custom_options": self.__set_custom_options_form_settings(),
            "if_not_found": self.__set_if_not_found_options_form_settings(),
            "use_codec_lib": self.__set_use_codec_lib_options_form_settings(),
            "bitrate": self.__set_bitrate_options_form_settings(),
        }

    def __set_if_not_found_options_form_settings(self):
        values = {
            "label": "Does the stream use this codec?",
            "input_type": "text",
        }
        if self.get_setting('advanced'):
            values["display"] = 'hidden'
        return values

    def __set_use_codec_lib_options_form_settings(self):
        values = {
            "label": "If not convert to this stream.",
            "input_type": "text",
        }
        if self.get_setting('advanced'):
            values["display"] = 'hidden'
        return values

    def __set_bitrate_options_form_settings(self):
        values = {
            "label": "Bitrate (0 for auto calculate) i.e. (Channels x 64)",
            "input_type": "text",
        }
        if self.get_setting('advanced'):
            values["display"] = 'hidden'
        return values

    def __set_custom_options_form_settings(self):
        values = {
            "label": "Write your own custom audio options",
            "input_type": "textarea",
        }
        if not self.get_setting('advanced'):
            values["display"] = 'hidden'
        return values


class PluginStreamMapper(StreamMapper):
    def __init__(self):
        super(PluginStreamMapper, self).__init__(logger, ['audio'])

    @staticmethod
    def calculate_bitrate(stream_info: dict):
        channels = stream_info.get('channels')
        # If no channel count is provided, assume the highest bitrate for 6 channels
        if not channels:
            logger.debug("Stream did not contain 'channels'. Using community default (64k).")
            return 64

        bitrate = 64 * int(stream_info.get('channels'))

        logger.debug("Based on Number of Channels ({}) we calculated the bitrate to be ({}k)".format(int(stream_info.get('channels')), bitrate))

        return bitrate

    def test_stream_needs_processing(self, stream_info: dict):
        settings = Settings()
        # Ignore streams already of the required codec_name
        if stream_info.get('codec_name').lower() in [settings.get_setting('if_not_found')]:
            return False
        return True

    def custom_stream_mapping(self, stream_info: dict, stream_id: int):
        settings = Settings()

        toCodec = settings.get_setting('use_codec_lib')

        stream_encoding = ['-c:a:{}'.format(stream_id), toCodec]

        bitrate = settings.get_setting('bitrate')
        if not bitrate or int(bitrate) == 0:
            bitrate = self.calculate_bitrate(stream_info)

        stream_encoding += [
            '-b:a:{}'.format(stream_id),
            '{}k'.format(bitrate),
            '-ac:a:{}'.format(stream_id),
            '{}'.format(stream_info.get('channels', 0)),
            '-af:a:{}'.format(stream_id),
            'aformat={}'.format("channel_layouts='7.1|5.1|stereo|mono'"),
        ]

        if settings.get_setting('advanced'):
            stream_encoding += settings.get_setting('custom_options').split()

        return {
            'stream_mapping': ['-map', '0:a:{}'.format(stream_id)],
            'stream_encoding': stream_encoding,
        }


def on_library_management_file_test(data):
    """
    Runner function - enables additional actions during the library management file tests.

    The 'data' object argument includes:
        path                            - String containing the full path to the file being tested.
        issues                          - List of currently found issues for not processing the file.
        add_file_to_pending_tasks       - Boolean, is the file currently marked to be added to the queue for processing.

    :param data:
    :return:

    """
    # Get the path to the file
    abspath = data.get('path')

    # Get file probe
    probe = Probe(logger, allowed_mimetypes=['audio', 'video'])
    if not probe.file(abspath):
        # File probe failed, skip the rest of this test
        return data

    # Get stream mapper
    mapper = PluginStreamMapper()
    mapper.set_probe(probe)

    if mapper.streams_need_processing():
        # Mark this file to be added to the pending tasks
        data['add_file_to_pending_tasks'] = True
        logger.debug("File '{}' should be added to task list. Probe found streams require processing.".format(abspath))
    else:
        logger.debug("File '{}' does not contain streams require processing.".format(abspath))

    return data


def on_worker_process(data):
    """
    Runner function - enables additional configured processing jobs during the worker stages of a task.

    The 'data' object argument includes:
        exec_command            - A command that Unmanic should execute. Can be empty.
        command_progress_parser - A function that Unmanic can use to parse the STDOUT of the command to collect progress stats. Can be empty.
        file_in                 - The source file to be processed by the command.
        file_out                - The destination that the command should output (may be the same as the file_in if necessary).
        original_file_path      - The absolute path to the original file.
        repeat                  - Boolean, should this runner be executed again once completed with the same variables.

    :param data:
    :return:

    """
    # Default to no FFMPEG command required. This prevents the FFMPEG command from running if it is not required
    data['exec_command'] = []
    data['repeat'] = False

    # Get the path to the file
    abspath = data.get('file_in')

    # Get file probe
    probe = Probe(logger, allowed_mimetypes=['audio', 'video'])
    if not probe.file(abspath):
        # File probe failed, skip the rest of this test
        return data

    # Get stream mapper
    mapper = PluginStreamMapper()
    mapper.set_probe(probe)

    if mapper.streams_need_processing():
        # Set the input file
        mapper.set_input_file(abspath)

        # Set the output file
        mapper.set_output_file(data.get('file_out'))

        # Get generated ffmpeg args
        ffmpeg_args = mapper.get_ffmpeg_args()

        # Apply ffmpeg args to command
        data['exec_command'] = ['ffmpeg']
        data['exec_command'] += ffmpeg_args

        # Set the parser
        parser = Parser(logger)
        parser.set_probe(probe)
        data['command_progress_parser'] = parser.parse_progress

    return data
