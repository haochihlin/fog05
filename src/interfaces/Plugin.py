class Plugin(object):

    def __init__(self,version):
        self.version=version

    def getVersion(self):
        return self.version