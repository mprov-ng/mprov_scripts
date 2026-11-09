#!/bin/sh
''''true; p=/usr/libexec/mprov/bin/python3; [ -x "$p" ] || p=/usr/bin/python3; exec "$p" "$0" "$@" #'''
# The two lines above pick the interpreter (#86, #89): sh runs line 2, which
# execs the job server's bundled python (/usr/libexec/mprov/bin/python3, in
# images built by mprov_jobserver 0.7.0 and later) if it's there, or else the
# system /usr/bin/python3 (images built by 0.6.x).  Python reads line 2 as a
# plain string and skips it.  Keep both lines exactly as they are.

# Script Type: post-boot
#
# Tell the mPCC this node has booted (#91, mprov_control_center#433), for a
# time-to-provision metric: the mPCC logs "provisioning: <host> (<ip>) booted
# image <image>" and records the system's last_booted time.  It's logged when
# this script runs, so it covers only the post-boot scripts ordered before it:
# make it depend on the others (dependsOn) to mean "fully set up".
#
# It also reports the node's serial number (#94, mprov_control_center#60), so
# the mPCC can catch a board swap on every boot.  The serial is read from
# /sys/class/dmi/id/product_serial and sent in a POST of its own, after the
# booted one: an mPCC without #60 answers 403 to any key it doesn't know, so
# sending both in one body would lose the booted report too.
#
# Purely informational: it never fails the boot or holds up other scripts.
# Any problem (an mPCC before 0.7.0 answers 403, an unknown node 404, the mPCC
# can't be reached, no serial to read) is logged and the script still exits 0.

import json, sys

# mprov-ng/mprov_control_center#452: sent as the mProv-Version header (for
# the mPCC's logs only).  Keep in sync with the repo-root VERSION file; these
# scripts are copied standalone into each mPCC's Scripts catalogue, so
# there's no build step to pull the version from VERSION at runtime.
MPROV_SCRIPTS_VERSION = "0.7"

NOT_RECORDED = "booted not recorded"
SERIAL_NOT_RECORDED = "serial not recorded"

SERIAL_FILE = "/sys/class/dmi/id/product_serial"
# System.serial's max_length on the mPCC (#60); a longer value gets a 400
SERIAL_MAX = 255


def read_serial():
  """The node's DMI serial number, stripped, or None (logged) if there's none."""
  try:
    with open(SERIAL_FILE, errors="replace") as f:
      serial = f.read().strip()
  except OSError as err:
    print(f"no serial number to report: unable to read {SERIAL_FILE}: {err}")
    return None
  if not serial:
    print(f"no serial number to report: {SERIAL_FILE} is empty.")
    return None
  return serial[:SERIAL_MAX]

try:
  from mprov_jobserver.script import MProvScript
except Exception as err:
  MProvScript = None
  IMPORT_ERROR = err


if MProvScript is not None:
  class MScript(MProvScript):
    script_version = MPROV_SCRIPTS_VERSION

    def __init__(self, **kwargs):
      print("Reporting booted to the mPCC.")
      super().__init__(**kwargs)
      self.runonce = True

    def run(self):
      self.report_booted()
      self.report_serial()
      return 0

    def report_booted(self):
      url = f"{self.mprovURL}systems/?self"
      try:
        result = self.session.post(url, data=json.dumps({"booted": True}))
      except Exception as err:
        print(f"{NOT_RECORDED}: unable to reach the mPCC at {url}: {err}", file=sys.stderr)
        return
      if not 200 <= result.status_code < 300:
        why = {403: " (an mPCC before 0.7.0 doesn't record it)",
               404: " (the mPCC doesn't know this node's address)"}.get(result.status_code, "")
        print(f"{NOT_RECORDED}: the mPCC answered {result.status_code}{why}: {result.text}", file=sys.stderr)
        return
      print(f"reported booted to the mPCC ({result.status_code}).")

    def report_serial(self):
      serial = read_serial()
      if serial is None:
        return
      url = f"{self.mprovURL}systems/?self"
      try:
        result = self.session.post(url, data=json.dumps({"serial": serial}))
      except Exception as err:
        print(f"{SERIAL_NOT_RECORDED}: unable to reach the mPCC at {url}: {err}", file=sys.stderr)
        return
      if result.status_code == 403:
        # expected on an mPCC without #60, so not an error
        print(f"{SERIAL_NOT_RECORDED}: the mPCC is too old for serial reporting (403).")
        return
      if not 200 <= result.status_code < 300:
        why = {404: " (the mPCC doesn't know this node's address)"}.get(result.status_code, "")
        print(f"{SERIAL_NOT_RECORDED}: the mPCC answered {result.status_code}{why}: {result.text}", file=sys.stderr)
        return
      print(f"reported serial number to the mPCC ({result.status_code}).")

    def main(self):
      return self.run()


def main():
  if MProvScript is None:
    print(f"{NOT_RECORDED}: unable to load mprov_jobserver: {IMPORT_ERROR}", file=sys.stderr)
    return 0
  try:
    # MProvScript's __init__ sys.exit(1)s when it can't start a session with
    # the mPCC; that mustn't fail the boot either.
    return MScript().main()
  except BaseException as err:
    if isinstance(err, KeyboardInterrupt):
      raise
    print(f"{NOT_RECORDED}: {type(err).__name__}: {err}", file=sys.stderr)
    return 0


if __name__ == "__main__":
  sys.exit(main())
