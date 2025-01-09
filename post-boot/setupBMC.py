#!/usr/bin/python3
import subprocess
from token import EXACT_TOKEN_TYPES
import yaml, json
import os, sys, time, socket, requests, sh
from mprov_jobserver.script import MProvScript


class MScript(MProvScript):

  def __init__(self, **kwargs):
    print("Setting Up BMC")
    super().__init__(**kwargs)
    self.runonce = True

  def cidr_to_netmask(self, cidr):
    cidr = int(cidr)
    mask = (0xffffffff >> (32 - cidr)) << (32 - cidr)
    return (str( (0xff000000 & mask) >> 24)   + '.' +
            str( (0x00ff0000 & mask) >> 16)   + '.' +
            str( (0x0000ff00 & mask) >> 8)    + '.' +
            str( (0x000000ff & mask)))
  def mac2LL(self, mac=None):
      if mac == None:
          return None
      mac_octets = mac.split(":")
      # take the first octet and invert the 2nd to last bit.
      mac_octets[0] = "%X" % (bytearray.fromhex(mac_octets[0])[0] ^ 0x2)
      return f"fe80::{mac_octets[0]}{mac_octets[1]}:{mac_octets[2]}ff:fe{mac_octets[3]}:{mac_octets[4]}{mac_octets[5]}"

  def run(self):

      print("Attempting to get our BMC info from the MPCC...")
      response = self.session.get(f"{self.mprovURL}systems/?hostname={socket.gethostname()}")
      if response.status_code != 200:
        print(f"Error: Unable to determine our ID from {socket.gethostname()}")
        sys.exit(1)
      try:
        system = response.json()[0]
      except Exception as e:
        print("Error: Unable to parse mPCC response. {e}")
        sys.exit(1)

      response = self.session.get(f"{self.mprovURL}systembmcs/?system={system['id']}&detail")
      if response.status_code == 200:
        bmc = {}
        # we got a bmc object, let's load it.
        try:
          bmc = response.json()[0]
        except Exception as e:
          print("Error: Unable to parse mPCC reply for BMC. {e}")
          return
        # let's execute some local commands to try to set up the bmc.
        result = subprocess.run(["/sbin/modprobe",  "ipmi_devintf"])
        if result.returncode:
          print(result.stdout)
          print(result.stderr)
          return

        result = subprocess.run(["/usr/bin/ipmitool", "lan", "set", "1", "ipsrc", "static"])
        if result.returncode:
          print(result.stdout)
          print(result.stderr)
          return

        result = subprocess.run(["/usr/bin/ipmitool", "lan", "set", "1", "ipaddr", f"{bmc['ipaddress']}"])
        if result.returncode:
          print(result.stdout)
          print(result.stderr)
          return

        result = subprocess.run(["/usr/bin/ipmitool", "lan",  "set",  "1",  "netmask",  f"{self.cidr_to_netmask(bmc['network']['netmask'])} "])
        if result.returncode:
          print(result.stdout)
          print(result.stderr)
          return

        if bmc['vlan'] == 0 and bmc['network']['vlan'] == 0:
          bmc['vlan'] = 'off'
        elif bmc['vlan'] == 0:
          bmc['vlan'] = bmc['network']['vlan']
        result = subprocess.run(["/usr/bin/ipmitool", "lan",  "set",  "1",  "vlan", "id",  f"{bmc['vlan']}"])
        if result.returncode:
          print(result.stdout)
          print(result.stderr)
          return

        result = subprocess.run(["/usr/bin/ipmitool", "lan",  "set",  "1",  "netmask",  f"{self.cidr_to_netmask(bmc['network']['netmask'])} "])
        if result.returncode:
          print(result.stdout)
          print(result.stderr)
          return

        result = subprocess.run(["/usr/bin/ipmitool", "user", "set", "name", "2", f"{bmc['username']}"])
        if result.returncode:
          print(result.stdout)
          print(result.stderr)
          return

        result = subprocess.run(["/usr/bin/ipmitool", "user", "set", "password", "2", f"{bmc['password']}"])
        if result.returncode:
          print(result.stdout)
          print(result.stderr)
          return

        result = subprocess.run(["/usr/bin/ipmitool", "user", "enable", "2"])
        if result.returncode:
          print(result.stdout)
          print(result.stderr)
          return
      else:
        print(f"Error: Unable to get bmc info, {response.status_code}: {response.text}")

      bmcMac = None
      for line in sh.ipmitool(['lan', 'print'], _iter=True, _ok_code=(0,1)):
        if line.startswith("MAC Address"):
          _, bmcMac = line.split(":", 1)
          bmcMac = bmcMac.strip()
      if bmcMac is not None:
        # calculate the bmc LL from MAC.
        bmcLL = self.mac2LL(bmcMac)
        data = {
          "id": bmc['id'],
          "mac": bmcMac,
          "ipv6ll": bmcLL,
        }
        response = self.session.patch(f"{self.mprovURL}systembmcs/{bmc['id']}/", data=json.dumps(data))
        if response.status_code != 200:
          print(f"Error updating bmc mac.")
          print(response.status_code)
          print(response.text)


  def main(self):
    self.startSession()
    self.run()

def main():
  script = MScript()
  return script.main()

def __main__():
    return main()

if __name__ == "__main__":
    sys.exit(main())
