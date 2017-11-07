import sys
import os
from fog05.interfaces.States import State
from fog05.interfaces.Entity import Entity


class ROS2Entity(Entity):

    def __init__(self, uuid, name, command, args, outfile, url):

        super(ROS2Entity, self).__init__()
        self.uuid = uuid
        self.name = name
        self.command = command
        self.args = args
        self.outfile = outfile
        self.url = url
        self.pid = -1

    def onConfigured(self):
        self.state = State.CONFIGURED

    def onClean(self):
        self.state = State.DEFINED

    def onStart(self, pid, process):
        self.pid = pid
        self.process = process
        self.state = State.RUNNING
    
    def onStop(self):
        self.pid = -1
        self.process = None
        self.state = State.CONFIGURED

    def onPause(self):
        self.state = State.PAUSED

    def onResume(self):
        self.state = State.RUNNING