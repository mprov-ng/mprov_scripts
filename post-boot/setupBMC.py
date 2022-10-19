import subprocess
from token import EXACT_TOKEN_TYPES
import yaml
import os, sys, time, socket, requests

class App():
  mprovURL=""
  apikey=""
  configfile = "/etc/mprov/jobserver.yaml"
  config_data={}
  session = session = requests.Session()
  myaddress = ""

  def __init__(self) -> None:
    self.load_config()
    if not self.startSession():
        print("Error: Unable to log into mProv Control Center.",file=sys.stderr)
    self.setupBMC()
  def yaml_include(self, loader, node):
    # Placeholder: Unused.  
    return {}
  def load_config(self):
    # load the config yaml
    # print(self.configfile)
    yaml.add_constructor("!include", self.yaml_include)
    if not(os.path.isfile(self.configfile) and os.access(self.configfile, os.R_OK)):
      self.configfile = os.getcwd() + "/jobserver.yaml"
    # print(self.configfile)
    if not(os.path.isfile(self.configfile) and os.access(self.configfile, os.R_OK)):
      print("Error: Unable to find a working config file.")
      sys.exit(1)


    with open(self.configfile, "r") as yamlfile:
      self.config_data = yaml.load(yamlfile, Loader=yaml.FullLoader)
    # flatten the config space
    result = {}
    for entry in self.config_data:
      result.update(entry)
    self.config_data = result
    if 'mprovURL' in self.config_data['global']:
      self.mprovURL = self.config_data['global']['mprovURL']
    if 'apikey' in self.config_data['global']:
      self.apikey = self.config_data['global']['apikey']

  def cidr_to_netmask(self, cidr):
    cidr = int(cidr)
    mask = (0xffffffff >> (32 - cidr)) << (32 - cidr)
    return (str( (0xff000000 & mask) >> 24)   + '.' +
            str( (0x00ff0000 & mask) >> 16)   + '.' +
            str( (0x0000ff00 & mask) >> 8)    + '.' +
            str( (0x000000ff & mask)))


  def setupBMC(self):
      
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

  def startSession(self):
    
    self.session.headers.update({
      'Authorization': 'Api-Key ' + self.apikey,
      'Content-Type': 'application/json'
      })

    # test connectivity
    try:
      response = self.session.get(self.mprovURL, stream=True)
    except Exception as e:
      print(f"Error: Communication error to the server: {e} .  Retrying.", file=sys.stderr)
      self.sessionOk = False
      time.sleep(10)
      return
    self.sessionOk = True
    if self.myaddress is not None:
      if self.myaddress != '':
        self.ip_address = self.myaddress
      else:
        print("Warning: No address set in config, attempting autodetection.  This may not work right...")
        # get the sock from the session
        s = socket.fromfd(response.raw.fileno(), socket.AF_INET, socket.SOCK_STREAM)
        # get the address from the socket
        address, _ = s.getsockname()
        self.ip_address=address
      
    # if we get a response.status_code == 200, we're ok.  If not,
    # our auth failed.
    return response.status_code == 200  


app = App()