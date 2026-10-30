#!/bin/bash
# This script will grab an image from the given node and copy it to the named image on the mprov-jobserver.
# It is meant to be run on the mprov-jobserver that is hosting the image to be synced.
# Usage: image-grab.sh -n <node> -i <image-name> [-d]
# Example: image-grab.sh -n node01 -i compute
set -e

usage() {
    echo
    echo "Usage: image-grab.sh -n <node> -i <image-name> [-d]"
    echo "Both -n <node> and -i <image-name> are required."
    echo
    echo -e "\t-d is optional and will delete files in the image that are not present on the node."
    echo
}

# process command line arguments
DELETE=()
while getopts n:i:d flag
do
    case "${flag}" in
        n) NODE=${OPTARG};;
        i) IMAGE_NAME=${OPTARG};;
        d) DELETE=(--delete);;
        *)
            usage
            exit 1
            ;;
    esac
done

# validate arguments
if [ -z "$NODE" ] || [ -z "$IMAGE_NAME" ]; then
    usage
    exit 1
fi

# The image name is a directory name inside the image store, never a path:
# with -d, rsync deletes whatever the node doesn't have from the destination.
if ! [[ "$IMAGE_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
    echo "Error: invalid image name '$IMAGE_NAME' (letters, digits, '.', '_' and '-' only, starting with a letter or digit)" >&2
    exit 1
fi

# The node name also goes into the log file name.
if ! [[ "$NODE" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
    echo "Error: invalid node name '$NODE' (a host name or IPv4 address)" >&2
    exit 1
fi

# read the image path (imageDir) from the image-update.yaml configuration file in /etc/mprov/plugins
IMAGE_DIR=$(cat /etc/mprov/plugins/image-update.yaml | grep "imageDir:" | awk '{print $2}')

# remove any single or double quotes from the IMAGE_DIR
IMAGE_DIR=${IMAGE_DIR//\'/}
IMAGE_DIR=${IMAGE_DIR//\"/}

# check if IMAGE_DIR is empty
if [ -z "$IMAGE_DIR" ]; then
    echo "Error: imageDir not found in /etc/mprov/plugins/image-update.yaml"
    exit 1
fi

# check if IMAGE_DIR exists
if [ ! -d "$IMAGE_DIR" ]; then
    echo "Error: imageDir $IMAGE_DIR does not exist"
    exit 1
fi

# The destination must resolve to a directory directly inside the image
# store, so an image directory that is a symlink to somewhere else is refused.
IMAGE_DIR=$(realpath "$IMAGE_DIR")
DEST=$(realpath -m "$IMAGE_DIR/$IMAGE_NAME")
if [ "$(dirname "$DEST")" != "$IMAGE_DIR" ]; then
    echo "Error: image $IMAGE_NAME resolves to $DEST, outside $IMAGE_DIR" >&2
    exit 1
fi

# check if the specified image exists in IMAGE_DIR
if [ ! -d "$DEST" ]; then
    echo "Warn: Image $IMAGE_NAME does not exist in $IMAGE_DIR"
    echo "Creating image directory $DEST/"
    if ! mkdir -p "$DEST"; then
        echo "Error: Failed to create image directory $DEST/" >&2
        exit 1
    fi
fi

# directories to exclude from the rsync, defaults to proc/ dev/ sys/ run/
EXCLUDE_DIRS=(--exclude=proc/ --exclude=dev/ --exclude=sys/ --exclude=run/ --exclude=tmp/ --exclude=mnt/ --exclude=media/ --exclude=lost+found/ --exclude=snap/ '--exclude=*.initramfs' '--exclude=*.vmlinuz')

# perform the rsync from the node to the image directory
LOG="/tmp/$NODE-to-$IMAGE_NAME.log"
echo -n "Grabbing image $IMAGE_NAME from node $NODE (log: $LOG)..."
status=0
rsync -avzx "${EXCLUDE_DIRS[@]}" --progress "${DELETE[@]}" "root@$NODE:/" "$DEST/" > "$LOG" 2>&1 || status=$?
echo
# 24: files vanished on the node while it was being copied, normal for a
# running system, so only a warning.
if [ "$status" -eq 24 ]; then
    echo "Warn: some files vanished on $NODE during the copy (rsync exit 24), see $LOG" >&2
elif [ "$status" -ne 0 ]; then
    echo "Error: rsync failed (exit $status), see $LOG" >&2
    exit 1
fi
echo "Image $IMAGE_NAME successfully grabbed from node $NODE"
exit 0

