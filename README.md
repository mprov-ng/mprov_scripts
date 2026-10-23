Here you will find some scripts that you may find useful in your mprov_control_center.  The scripts here will be organized into directories which describe where we think the script may be useful.  Suggestions welcome, please open an issue to request new scripts, and if contributing, feel free to fork and open a PR.

## Repository layout

- `image-gen/` — scripts that run against a system image during image generation.
- `post-boot/` — scripts that run on a node after it boots.
- `tools/` — one-off commandline helpers for admins; these are not meant to be uploaded to the mPCC.
- `tests/` — pytest suite that runs the param-reading scripts in a Rocky 9 container to check config-param handling.
- `mpcc_scripts.yaml` — the manifest of scripts that the Control Center installer (`install_scripts/load_default_scripts.sh` in the mprov_control_center repo) loads into an mPCC instance.  Each entry names the script file, a display name, and its `scriptType` (`image-gen` or `post-boot`).

New scripts intended for the mPCC should be added to the appropriate directory *and* added to `mpcc_scripts.yaml`.

Generated documentation lives in [`openwiki/quickstart.md`](openwiki/quickstart.md).
