import ConfigParser
conf = ConfigParser.ConfigParser(dict(port='6667', channels='#infobat'))
conf.read(['infobat.cfg'])
