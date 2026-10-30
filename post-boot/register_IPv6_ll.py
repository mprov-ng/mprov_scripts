#!/usr/bin/python3

# THIS SCRIPT REQUIRES mprov-jobserver>=0.0.34
# Script Type: post-boot

from mprov_jobserver.script import MProvScript
import netifaces
from socket import (
  socket,
  AF_INET,
  AF_INET6,
  AF_PACKET
)
from pyroute2 import IPDB
import platform, sys, json

class MScript(MProvScript):
  def __init__(self, **kwargs):
    print("Registering IPv6 Link-Local Address.")
    super().__init__(**kwargs)
    self.runonce = True

  def run(self):
    # grab our hostname locally
    myHostname = platform.node()
    if '.' in myHostname:
      # strip the domain if there is one.
      myHostname, _ = myHostname.split('.', 1)

    # get the known interfaces from mPCC
    url = f"{self.mprovURL}networkinterfaces/?hostname={myHostname}"
    result=self.session.get(url)
    if result.status_code != 200:
      print("Error: Unable to get interfaces form mPCC", file=sys.stderr)
      print(f"{result.text}", file=sys.stderr)
      return 1
    interfaces = []
    try: 
      interfaces = result.json()
    except:
      print("Error: unable to parse JSON from mPCC.", file=sys.stderr)
      return 1 
    ipdb = IPDB()
    for interface in interfaces:
      # find the local interface with this mac.
      for lInterface in netifaces.interfaces():
        if lInterface == "lo":
          continue
        if ipdb.interfaces[lInterface].operstate != "UP":
          continue
        if interface['mac'] == netifaces.ifaddresses(lInterface)[AF_PACKET][0]['addr']:
          # we found a local, up interface, let's get it's IPv6 LL address.
          for v6ip in netifaces.ifaddresses(lInterface)[AF_INET6]:
            if v6ip['addr'].startswith("fe80:"):
              # this is it.  Drop the %<iface> scope, which the mPCC doesn't
              # store, then compare it to the ipv6ll entry in the mPCC.
              ipv6ll, _, _ = v6ip['addr'].partition('%')
              if interface['ipv6ll'] != ipv6ll:
                # update it.  Send only ipv6ll, so nothing else on the
                # interface is overwritten with what we read earlier.
                url = f"{self.mprovURL}networkinterfaces/{interface['id']}/"
                result = self.session.patch(url, data=json.dumps({'ipv6ll': ipv6ll}))
                if result.status_code != 200:
                  print(f"Error: Unable to update mPCC, {result.status_code}: {result.text}", file=sys.stderr)
                  return 1
    return 0

  def main(self):
    self.startSession()
    # run() returns the exit status (#37).
    return self.run()

def main():
  script = MScript()
  return script.main()

def __main__():
    return main()
    
if __name__ == "__main__":
    sys.exit(main())

