#!/usr/bin/python3
import subprocess
import tempfile
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

  def ipmitool(self, *args):
    """ Run one ipmitool command; on failure print its error and return False. """
    result = subprocess.run(["/usr/bin/ipmitool", *args], capture_output=True, text=True)
    if result.returncode:
      print(f"Error: ipmitool {' '.join(args)} failed ({result.returncode}): "
            f"{(result.stderr or result.stdout).strip()}", file=sys.stderr)
      return False
    return True

  def set_password(self, userid, password):
    """
    Set a BMC user's password without putting it on a command line, where
    any local user can read it (#35): 'ipmitool exec' reads the command from
    a root-only batch file.  exec's parser drops everything from a '#' on,
    splits on spaces unless a token is quoted, turns a '~' inside quotes into
    a space, and runs off the end of the line on a lone quote.  So the
    password goes in double quotes and can't contain '"', '#', '~' or
    anything but printable ASCII.
    """
    bad = sorted({c for c in password if c in '"#~' or not " " <= c <= "~"})
    if bad:
      print(f"Error: the BMC password has characters ipmitool exec can't carry: {bad}. "
            "Change it in the mPCC.", file=sys.stderr)
      return False
    fd, batchfile = tempfile.mkstemp(prefix="setupBMC-")
    try:
      with os.fdopen(fd, "w") as batch:
        batch.write(f'user set password {userid} "{password}"\n')
      result = subprocess.run(["/usr/bin/ipmitool", "exec", batchfile], capture_output=True, text=True)
    finally:
      os.unlink(batchfile)
    if result.returncode:
      print(f"Error: setting the BMC password failed ({result.returncode}): "
            f"{(result.stderr or result.stdout).strip()}", file=sys.stderr)
      return False
    return True

  def run(self):

      print("Attempting to get our BMC info from the MPCC...")
      response = self.session.get(f"{self.mprovURL}systems/?hostname={socket.gethostname()}")
      if response.status_code != 200:
        print(f"Error: Unable to determine our ID from {socket.gethostname()}")
        sys.exit(1)
      try:
        found=False
        for system in response.json():
          if system['hostname'] == socket.gethostname():
            found=True
            break
      except Exception as e:
        print("Error: Unable to parse mPCC response. {e}")
        sys.exit(1)
      if found != True:
        print("Error: unable to find host in mPCC.")
        sys.exit(1)

      response = self.session.get(f"{self.mprovURL}systembmcs/?system={system['id']}&detail")
      if response.status_code == 200:
        bmc = {}
        # we got a bmc object, let's load it.
        try:
          bmcs = response.json()
        except Exception as e:
          print(f"Error: Unable to parse mPCC reply for BMC. {e}", file=sys.stderr)
          sys.exit(1)
        if not bmcs:
          print("This system has no BMC in the mPCC, nothing to set up.")
          return
        bmc = bmcs[0]
        # let's execute some local commands to try to set up the bmc.
        result = subprocess.run(["/sbin/modprobe",  "ipmi_devintf"], capture_output=True, text=True)
        if result.returncode:
          print(f"Error: modprobe ipmi_devintf failed: {(result.stderr or result.stdout).strip()}", file=sys.stderr)
          return

        if not self.ipmitool("lan", "set", "1", "ipsrc", "static"):
          return
        if not self.ipmitool("lan", "set", "1", "ipaddr", f"{bmc['ipaddress']}"):
          return
        # once, and without the trailing space it used to carry (#27).
        if not self.ipmitool("lan", "set", "1", "netmask", self.cidr_to_netmask(bmc['network']['netmask'])):
          return
        # a network can have no gateway (#27).
        if bmc['network'].get('gateway'):
          if not self.ipmitool("lan", "set", "1", "defgw", "ipaddr", bmc['network']['gateway']):
            return

        if bmc['vlan'] == 0 and bmc['network']['vlan'] == 0:
          bmc['vlan'] = 'off'
        elif bmc['vlan'] == 0:
          bmc['vlan'] = bmc['network']['vlan']
        if not self.ipmitool("lan", "set", "1", "vlan", "id", f"{bmc['vlan']}"):
          return

        if not self.ipmitool("user", "set", "name", "2", f"{bmc['username']}"):
          return
        if bmc['password']:
          if not self.set_password(2, bmc['password']):
            return
        else:
          print("Warning: no BMC password in the mPCC, leaving the BMC's password as it is.", file=sys.stderr)
        if not self.ipmitool("user", "enable", "2"):
          return
      else:
        # without a BMC record there's nothing to register the MAC against (#36).
        print(f"Error: Unable to get bmc info, {response.status_code}: {response.text}", file=sys.stderr)
        sys.exit(1)

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
