#!/usr/bin/python3

import json
import socket
import uuid
import yaml, os, sys, glob, time
import parted
import traceback
import requests
import sh

# This scirpt will:
# - grab the disklayout from the mPCC, 
# - build the partition table(s)
# - build the file system(s)
# - update the /etc/fstab

class mProvStatelessDiskFormatter():
  config_data = {}
  mprovURL = "http://127.0.0.1:8080/"
  apikey = ""
  heartbeatInterval = 10
  runonce = False
  sessionOk = False
  disklayout = {}
  session = requests.Session()
  ip_address = None

  def __init__(self, **kwargs):
    print("mProv Local Disk Formatter Starting.")

    # use the jobserver config
    self.configfile = "/etc/mprov/jobserver.yaml"
    
    # load our config
    self.load_config()

    # start a session
    if not self.startSession():
      print("Error: Unable to communicate with the mPCC.")
      sys.exit(1)

  def yaml_include(self, loader, node):
    # disable includes, no need.
    return {}
    #   # Get the path out of the yaml file
    # file_name = os.path.join(os.path.dirname(loader.name), node.value)
    
    # # we have a glob, so we will iterate.
    # result = {}
    # for file in glob.glob(file_name):
    #   with open(file) as inputfile:
    #     result.update(yaml.load(inputfile, Loader=yaml.FullLoader)[0])
    # return result

  def load_config(self):
    # load the config yaml
    # print(self.configfile)
    yaml.add_constructor("!include", self.yaml_include)
    
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

    # map the global config on to our object
    for config_entry in self.config_data['global'].keys():
      try:
        getattr(self, config_entry)
        setattr(self, config_entry, self.config_data['global'][config_entry])
      except:
        # ignore unused keys
        pass
    pass

  def startSession(self):
    
    self.session.headers.update({
      'Authorization': 'Api-Key ' + self.apikey,
      'Content-Type': 'application/json'
      })

    # connect to the mPCC
    try:
      response = self.session.get(self.mprovURL, stream=True)
    except:
      print("Error: Communication error to the server.  Retrying.", file=sys.stderr)
      self.sessionOk = False
      time.sleep(self.heartbeatInterval)
      return False
    self.sessionOk = True
    # get the sock from the session
    s = socket.fromfd(response.raw.fileno(), socket.AF_INET, socket.SOCK_STREAM)
    # get the address from the socket
    address = s.getsockname()
    self.ip_address=address
      
    # if we get a response.status_code == 200, we're ok.  If not,
    # our auth failed.
    return response.status_code == 200

  def buildDisks(self):
    self._getDiskLayout()
    disks = "" 

    print("Building partitions and filesystems...")
    # stop all arrays
    sh.mdadm(['--stop', '--scan'])
    try:
      os.unlink("/etc/fstab")
    except:
      pass
    partnum=1

    for pdisk in self.disklayout:
      # don't do raid disks here.
      if pdisk['dtype'] == 'mdrd':
        continue
      device = parted.getDevice(pdisk['diskname'])
      disk = parted.freshDisk(device, 'gpt' )
      
      sectorsize=device.sectorSize
      start=self.from_mebibytes(1, sectorsize)
      fillpart = None

      pdisk['partitions'] = sorted(pdisk['partitions'], key=lambda d: d['partnum'])
      for part in pdisk['partitions']:
        # if this is the fill partition, make note of that and skip for now.
        if part['fill']:
          fillpart=part
          continue
        
        start = start + self._createPartition(device, disk, part, sectorsize, start) + 1
        if part['filesystem'] != 'raid' and part['mount'] != 'raid':
          partuuid = self._makeFS(part, pdisk)


          # update /etc/fstab
          with open("/etc/fstab", "a") as fstab:
            fstab.write(f"UUID={partuuid}\t{part['mount']}\t\t{part['filesystem']}\tdefaults\t0 0\n")
          sh.mount([f"{pdisk['mount']}"])

        partnum+=1

      if fillpart is not None:
        self._createPartition(device, disk, fillpart, sectorsize, start)    
        
        if part['filesystem'] != 'raid' and part['mount'] != 'raid':
          partuuid = self._makeFS(fillpart, pdisk)
          # update /etc/fstab
          with open("/etc/fstab", "a") as fstab:
            fstab.write(f"UUID={partuuid}\t{fillpart['mount']}\t\t{fillpart['filesystem']}\tdefaults\t0 0\n")
          sh.mount([f"{fillpart['mount']}"])
    
  def buildRAIDs(self):
    print("Building software RAID devices ... ")
    for pdisk in self.disklayout:
      # don't do raid disks here.
      if pdisk['dtype'] != 'mdrd':
        continue
      #  mdadm --create /dev/md0 --level=1 --raid-devices=2 /dev/hd[ac]1
      mdadm_cmd = f"--create {pdisk['diskname']} -R --force --level={pdisk['raidlevel']} --raid-devices={len(pdisk['members'])} "
      for member in pdisk['members']:
        # find our dev from the disklayouts.
        for sdisk in self.disklayout:
          if sdisk['slug'] == member['disklayout']:
            dev = sdisk['diskname']
        partOffset = 0

        member_part = f"{dev}{member['partnum'] + partOffset}"
        mdadm_cmd += f"{member_part} "
        # zero the superblock
        sh.mdadm(['--zero-superblock', f"{member_part}"])
        sh.wipefs(['--all', '--force', f"{member_part}"])
      # convert mdadm_cmd to something the sh mod can use
      sh.mdadm(mdadm_cmd.split())
      # spoof a partition to _makeFS()
      part = {
        'filesystem': pdisk['filesystem'],
        'partnum':'',
      }
      partuuid = self._makeFS(part, pdisk)
      # update /etc/fstab
      with open("/etc/fstab", "a") as fstab:
        fstab.write(f"UUID={partuuid}\t{pdisk['mount']}\t\t{pdisk['filesystem']}\tdefaults\t0 0\n")
      
      sh.mount([f"{pdisk['mount']}"])
 
  def to_mebibytes(self, value):
    return value / (2 ** 20)

  def to_megabytes(self, value):
    return value / (10 ** 6)

  def from_mebibytes(self, value, sectorsize):
    return int((value * (2 ** 20))/sectorsize)

  def _getDiskLayout(self):
    # connect to the mPCC and get our information
    query = "systems/?self"
    response = self.session.get(self.mprovURL + query)
    if response.status_code == 200:
      try:
        systemDetails = response.json()[0]
      except:
        print("Error: Unable to parse server response.")
        sys.exit(1)
      self.disklayout = systemDetails['disks']
      return
    else:
      print(f"Error: Unable to get disk info from mPCC.  Server returned {response.status_code}")
      sys.exit(1)

  def _createPartition(self, device, disk, inpart, sectorsize, start, parttype=parted.PARTITION_NORMAL):
    part=inpart
    if part['fill']:
      geometry = parted.Geometry(
        device=device,
        start=start,
        end=device.getLength() - 1
      )
    else:
      geometry = parted.Geometry(
        device=device,
        start=start,
        length=self.from_mebibytes(part['size'],sectorsize)
      )
    if part['filesystem'] == 'linux-swap':
      part['filesystem'] = 'linux-swap(v1)'
    if part['filesystem'] == 'raid'  \
      or part['filesystem'] == None:
      # part['filesystem'] = None
      fs=None
    else:
      fs = parted.FileSystem(
        type=part['filesystem'], 
        geometry=geometry,
      )
    partition = parted.Partition(
      disk=disk,
      type=parttype,
      fs=fs,
      geometry=geometry
    )
    disk.addPartition(partition=partition, constraint=device.optimalAlignedConstraint)
    disk.commit()
    return self.from_mebibytes(part['size'], sectorsize)
    

  def _makeFS(self, part, pdisk):
    partOffset = 0
    if part['partnum'] != '':
      part['partnum'] = int(part['partnum']) + partOffset
    # make a uuid 
    partuuid = uuid.uuid4()
    if part['filesystem'] == 'xfs':
      part['uuid'] = f"uuid={partuuid}"
      part['uuidopt'] = "-m"
      part['force'] = "-f"
    else:
      part['uuidopt'] = "-U"
      part['uuid'] = f"{partuuid}"
      part['force'] = "-F"

    # make the filesystem
    if part['filesystem'] == 'linux-swap(v1)':
      # print("mkswap ",f"{pdisk['diskname']}{part['partnum']}",f"{part['uuidopt']}",  f"{part['uuid']}")
      print(f"Building {part['filesystem']} file system on {pdisk['diskname']}{part['partnum']}...")
      sh.mkswap(f"{pdisk['diskname']}{part['partnum']}",f"{part['uuidopt']}",  f"{part['uuid']}")
      part['mount'] = "none"
      # put this back
      part['filesystem'] = "swap"
      
    else:
      print(f"Building {part['filesystem']} file system on {pdisk['diskname']}{part['partnum']}...")
      sh.mkfs(f"{part['force']}", f"-t", f"{part['filesystem']}", f"{part['uuidopt']}", f"{part['uuid']}", f"{pdisk['diskname']}{part['partnum']}")
    return partuuid

def main():
  dFormatter = mProvStatelessDiskFormatter()
  dFormatter.buildDisks()
  dFormatter.buildRAIDs()
  pass

def __main__():
    main()
    
if __name__ == "__main__":
    main()
