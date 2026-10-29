#!/usr/bin/python3

import json
import socket
import uuid
import yaml, os, sys, glob, time, shutil
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
  runonce = True
  sessionOk = False
  disklayout = {}
  session = requests.Session()
  ip_address = None
  # the script runner writes entity.json next to the scripts it downloads.
  entityfile = os.path.join(os.path.dirname(os.path.abspath(__file__)), "entity.json")
  # for runs by hand: a node has the script runner's config, a secondary
  # jobserver has its own.
  configfiles = ["/etc/mprov/script-runner.yaml", "/etc/mprov/jobserver.yaml"]
  fstabfile = "/etc/fstab"
  # our entries go between these, so a rerun replaces them (swap has no
  # mount point to match an old entry by).
  FSTAB_BEGIN = "# BEGIN mprov localTempDisk.py"
  FSTAB_END = "# END mprov localTempDisk.py"
  # what _checkDisks() reads to see which devices are in use (#43).
  procMounts = "/proc/mounts"
  procSwaps = "/proc/swaps"
  sysBlock = "/sys/class/block"

  def __init__(self, **kwargs):
    print("mProv Local Disk Formatter Starting.")

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
    # run by the script runner: take the mPCC URL and API key it hands us.
    if os.path.isfile(self.entityfile) and os.access(self.entityfile, os.R_OK):
      with open(self.entityfile, "r") as entfile:
        entity = json.load(entfile)
      for config_entry in ("mprovURL", "apikey"):
        if entity.get(config_entry):
          setattr(self, config_entry, entity[config_entry])
      return

    # otherwise load the config yaml
    yaml.add_constructor("!include", self.yaml_include)

    for configfile in self.configfiles:
      if os.path.isfile(configfile) and os.access(configfile, os.R_OK):
        self.configfile = configfile
        break
    else:
      print(f"Error: Unable to find a working config file: no {self.entityfile} and none of {', '.join(self.configfiles)}.")
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
    # never repartition a disk the running system uses (#43).
    self._checkDisks()
    # /etc/fstab is left alone until everything is built (#33).
    self.fstabEntries = []
    self.partDevices = {}

    for pdisk in self.disklayout:
      # don't do raid disks here.
      if pdisk['dtype'] == 'mdrd':
        continue
      # partitions are numbered in the order they're created: the layout's
      # partitions by number, with the fill partition last.
      partnum=1
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
        self._setPartDevice(pdisk, part, partnum)
        if part['filesystem'] != 'raid' and part['mount'] != 'raid':
          partuuid = self._makeFS(part, pdisk)
          self._addToFstab(partuuid, part['mount'], part['filesystem'])
          self._activate(part, partuuid)

        partnum+=1

      if fillpart is not None:
        self._createPartition(device, disk, fillpart, sectorsize, start)
        self._setPartDevice(pdisk, fillpart, partnum)

        if fillpart['filesystem'] != 'raid' and fillpart['mount'] != 'raid':
          partuuid = self._makeFS(fillpart, pdisk)
          self._addToFstab(partuuid, fillpart['mount'], fillpart['filesystem'])
          self._activate(fillpart, partuuid)
    
  def buildRAIDs(self):
    print("Building software RAID devices ... ")
    for pdisk in self.disklayout:
      # don't do raid disks here.
      if pdisk['dtype'] != 'mdrd':
        continue
      #  mdadm --create /dev/md0 --level=1 --raid-devices=2 /dev/hd[ac]1
      mdadm_cmd = f"--create {pdisk['diskname']} -R --force --level={pdisk['raidlevel']} --raid-devices={len(pdisk['members'])} "
      for member in pdisk['members']:
        member_part = self.partDevices.get((member['disklayout'], member['partnum']))
        if member_part is None:
          print(f"Error: RAID {pdisk['diskname']} member partition {member['partnum']} of {member['disklayout']} wasn't created.", file=sys.stderr)
          sys.exit(1)
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
      self._addToFstab(partuuid, pdisk['mount'], pdisk['filesystem'])
      self._activate({'mount': pdisk['mount']}, partuuid)

  def writeFstab(self):
    """
    Write /etc/fstab once every disk, filesystem and RAID is built (#33): the
    existing entries, minus any for a mount point we just built and our block
    from an earlier run, then our block.  The old file is kept as
    /etc/fstab.mprov-bak, and the new one is swapped in with a rename, so a
    failed run leaves /etc/fstab as it was.
    """
    newmounts = {line.split()[1] for line in self.fstabEntries} - {"none"}
    kept = []
    inblock = False
    try:
      with open(self.fstabfile, "r") as fstab:
        for line in fstab:
          if line.strip() == self.FSTAB_BEGIN:
            inblock = True
          elif line.strip() == self.FSTAB_END:
            inblock = False
          else:
            fields = line.split()
            replaced = len(fields) > 1 and not fields[0].startswith("#") and fields[1] in newmounts
            if not inblock and not replaced:
              kept.append(line if line.endswith("\n") else line + "\n")
      shutil.copy2(self.fstabfile, self.fstabfile + ".mprov-bak")
    except FileNotFoundError:
      pass

    tmpfile = self.fstabfile + ".mprov-new"
    with open(tmpfile, "w") as fstab:
      fstab.writelines(kept + [self.FSTAB_BEGIN + "\n"] + self.fstabEntries + [self.FSTAB_END + "\n"])
      fstab.flush()
      os.fsync(fstab.fileno())
    os.chmod(tmpfile, 0o644)
    os.replace(tmpfile, self.fstabfile)

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


  def _checkDisks(self):
    """
    Refuse to repartition a disk the running system uses (#43): one that, or
    a partition of which, is mounted, active swap or still held by a RAID or
    LVM device (mdadm --stop --scan has stopped the idle arrays by now).  On a
    stateful node that is the root disk.  Every disk is checked before any is
    touched, and on a problem they're all printed and we exit.
    """
    inuse = {}
    for table, header, reason in ((self.procMounts, False, "is mounted on {}"),
                                  (self.procSwaps, True, "is active swap")):
      try:
        with open(table, "r") as entries:
          lines = entries.readlines()[1 if header else 0:]
      except FileNotFoundError:
        continue
      for line in lines:
        fields = line.split()
        if len(fields) > 1 and fields[0].startswith("/dev/"):
          inuse.setdefault(self._kernelName(fields[0]), reason.format(fields[1]))

    problems = []
    for pdisk in self.disklayout:
      if pdisk['dtype'] == 'mdrd':
        continue
      disk = self._kernelName(pdisk['diskname'])
      diskdir = os.path.join(self.sysBlock, disk)
      if not os.path.isdir(diskdir):
        problems.append(f"{pdisk['diskname']}: no such disk")
        continue
      parts = sorted(name for name in os.listdir(diskdir)
                     if os.path.isfile(os.path.join(diskdir, name, "partition")))
      for dev in [disk] + parts:
        if dev in inuse:
          problems.append(f"{pdisk['diskname']}: /dev/{dev} {inuse[dev]}")
        try:
          holders = sorted(os.listdir(os.path.join(self.sysBlock, dev, "holders")))
        except FileNotFoundError:
          holders = []
        if holders:
          problems.append(f"{pdisk['diskname']}: /dev/{dev} is in use by {', '.join('/dev/' + h for h in holders)}")

    if problems:
      print("Error: refusing to repartition disks the running system uses:", file=sys.stderr)
      for problem in problems:
        print(f"  {problem}", file=sys.stderr)
      sys.exit(1)

  def _kernelName(self, path):
    """ The kernel's name for a device path: /dev/disk/by-id/... -> sda, /dev/mapper/vg-lv -> dm-0. """
    return os.path.basename(os.path.realpath(path))

  def _addToFstab(self, partuuid, mount, filesystem):
    """ Note an fstab entry for writeFstab(). """
    self.fstabEntries.append(f"UUID={partuuid}\t{mount}\t\t{filesystem}\tdefaults\t0 0\n")

  def _activate(self, part, partuuid):
    """ Put a partition _makeFS() just built to use: swap on, or mount it. """
    if part['mount'] == "none":
      # swap: there's nothing to mount ('mount none' fails), turn it on (#54).
      sh.swapon([f"UUID={partuuid}"])
    else:
      # by UUID: /etc/fstab isn't written until everything is built (#33).
      sh.mount([f"UUID={partuuid}", f"{part['mount']}"])

  def _setPartDevice(self, pdisk, part, partnum):
    """ Note the device a layout partition got, for _makeFS() and buildRAIDs(). """
    prefix = "p" if pdisk['diskname'].startswith("/dev/nvme") else ""
    part['device'] = f"{pdisk['diskname']}{prefix}{partnum}"
    self.partDevices[(pdisk['slug'], part['partnum'])] = part['device']

  def _makeFS(self, part, pdisk):
    # layout partitions carry the device buildDisks() made for them; a RAID
    # device is pdisk itself.
    dev = part.get('device') or pdisk['diskname']
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
      print(f"Building {part['filesystem']} file system on {dev}...")
      sh.mkswap(f"{dev}",f"{part['uuidopt']}",  f"{part['uuid']}")
      part['mount'] = "none"
      # put this back
      part['filesystem'] = "swap"
      
    else:
      print(f"Building {part['filesystem']} file system on {dev}...")
      # -t first: mkfs stops reading its own options at the first one it
      # doesn't know, so after the force flag it never saw the type (#57).
      sh.mkfs("-t", f"{part['filesystem']}", f"{part['force']}", f"{part['uuidopt']}", f"{part['uuid']}", f"{dev}")
    return partuuid

def main():
  dFormatter = mProvStatelessDiskFormatter()
  dFormatter.buildDisks()
  dFormatter.buildRAIDs()
  dFormatter.writeFstab()

def __main__():
    main()
    
if __name__ == "__main__":
    main()
