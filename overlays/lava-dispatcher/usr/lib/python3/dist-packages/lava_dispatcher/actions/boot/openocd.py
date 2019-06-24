# Copyright (C) 2019 Linaro Limited
#
# Author: Vincent Wan <vincent.wan@linaro.org>
#
# This file is part of LAVA Dispatcher.
#
# LAVA Dispatcher is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# LAVA Dispatcher is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along
# with this program; if not, see <http://www.gnu.org/licenses>.

from lava_common.utils import binary_version
from lava_dispatcher.action import Pipeline, Action
from lava_dispatcher.logical import Boot, RetryAction
from lava_dispatcher.actions.boot import BootAction
from lava_dispatcher.connections.serial import ConnectDevice
from lava_dispatcher.utils.shell import which
from lava_dispatcher.utils.strings import substitute
from lava_dispatcher.power import ResetDevice
from lava_dispatcher.utils.udev import WaitDeviceBoardID


class OpenOCD(Boot):

    compatibility = 4  # FIXME: change this to 5 and update test cases

    def __init__(self, parent, parameters):
        super().__init__(parent)
        self.action = BootOpenOCD()
        self.action.section = self.action_type
        self.action.job = self.job
        parent.add_action(self.action, parameters)

    @classmethod
    def accepts(cls, device, parameters):
        if "openocd" not in device["actions"]["boot"]["methods"]:
            return False, '"openocd" was not in the device configuration boot methods'
        if "method" not in parameters:
            return False, '"method" was not in parameters'
        if parameters["method"] != "openocd":
            return False, '"method" was not "openocd"'
        if "board_id" not in device:
            return False, '"board_id" is not in the device configuration'
        return True, "accepted"


class BootOpenOCD(BootAction):

    name = "boot-openocd-image"
    description = "boot openocd image with retry"
    summary = "boot openocd image with retry"

    def populate(self, parameters):
        self.internal_pipeline = Pipeline(
            parent=self, job=self.job, parameters=parameters
        )
        self.internal_pipeline.add_action(BootOpenOCDRetry())


class BootOpenOCDRetry(RetryAction):

    name = "boot-openocd-image"
    description = "boot openocd image using the command line interface"
    summary = "boot openocd image"

    def populate(self, parameters):
        self.internal_pipeline = Pipeline(
            parent=self, job=self.job, parameters=parameters
        )
        if self.job.device.hard_reset_command:
            self.internal_pipeline.add_action(ResetDevice())
            self.internal_pipeline.add_action(
                WaitDeviceBoardID(self.job.device.get("board_id"))
            )
        self.internal_pipeline.add_action(FlashOpenOCDAction())
        self.internal_pipeline.add_action(ConnectDevice())


class FlashOpenOCDAction(Action):

    name = "flash-openocd"
    description = "use openocd to flash the image"
    summary = "use openocd to flash the image"

    def __init__(self):
        super().__init__()
        self.base_command = []
        self.exec_list = []

    def validate(self):
        super().validate()
        boot = self.job.device["actions"]["boot"]["methods"]["openocd"]
        openocd_binary = boot["parameters"]["command"]
        binary = which(openocd_binary)
        self.logger.info(
            binary_version(binary, "--version", "Open On-Chip Debugger (.*)")
        )
        self.base_command = [openocd_binary]
        job_cfg_file = ""

        # Build the substitutions dictionary and set cfg script based on
        # job definition
        substitutions = {}
        for action in self.get_namespace_keys("download-action"):
            filename = self.get_namespace_data(
                action="download-action", label=action, key="file"
            )
            if filename is None:
                self.logger.warning(
                    "Empty value for action='download-action' label='%s' " "key='file'",
                    action,
                )
                continue
            if action == "openocd_script":
                # if a url for openocd_script is specified in the job
                # definition, use that instead of the default for the device
                # type.
                job_cfg_file = filename
                self.base_command.extend(["-f", job_cfg_file])
            else:
                substitutions["{%s}" % action.upper()] = filename

        if job_cfg_file is "":
            for item in boot["parameters"]["options"].get("file", []):
                if item is not None:
                    self.base_command.extend(["-f", item])
        debug = boot["parameters"]["options"]["debug"]
        if debug is not None:
            self.base_command.extend(["-d" + str(debug)])
        for item in boot["parameters"]["options"].get("search", []):
            self.base_command.extend(["-s", item])
        for item in boot["parameters"]["options"].get("command", []):
            self.base_command.extend(["-c", item])

        if self.job.device["board_id"] == "00000000":
            self.errors = "[FLASH_OPENOCD] board_id unset"

        self.base_command = substitute(self.base_command, substitutions)
        self.exec_list.append(self.base_command)
        if not self.exec_list:
            self.errors = "No OpenOCD command to execute"

    def run(self, connection, max_end_time):
        connection = self.get_namespace_data(
            action="shared", label="shared", key="connection", deepcopy=False
        )
        connection = super().run(connection, max_end_time)
        for openocd_command in self.exec_list:
            self.run_cmd(openocd_command)
        self.set_namespace_data(
            action="shared", label="shared", key="connection", value=connection
        )
        return connection
