#!/bin/bash
# This script will grab an image from the given node and copy it to the named image on the mprov-jobserver.  
# It is meant to be run on the mprov-jobserver that is hosting the image to be synced.
# Usage: image-grab.sh -n <node> -i <image-name> [-d]
# Example: image-grab.sh -n node01 -i compute 
set -e

# process command line arguments
while getopts n:i: flag
do
    case "${flag}" in
        n) NODE=${OPTARG};;
        i) IMAGE_NAME=${OPTARG};;
        d) DELETE=--delete;;
        *)
            echo "Usage: image-grab.sh -n <node> -i <image-name>"
            exit 1
            ;;
    esac
done  

# validate arguments
if [ -z "$NODE" ] || [ -z "$IMAGE_NAME" ]; then
    echo
    echo "Usage: image-grab.sh -n <node> -i <image-name> [-d]"
    echo "Both -n <node> and -i <image-name> are required."
    echo
    echo -e "\t-d is optional and will delete files in the image that are not present on the node."
    echo
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

# check if the specified image exists in IMAGE_DIR
if [ ! -d "$IMAGE_DIR/$IMAGE_NAME/" ]; then
    echo "Warn: Image $IMAGE_NAME does not exist in $IMAGE_DIR"
    echo "Creating image directory $IMAGE_DIR/$IMAGE_NAME/"
    mkdir -p "$IMAGE_DIR/$IMAGE_NAME/"
    if [ $? -ne 0 ]; then
        echo "Error: Failed to create image directory $IMAGE_DIR/$IMAGE_NAME/"    
        exit 1
    fi
fi

# directories to exclude from the rsync, defaults to proc/ dev/ sys/ run/
EXCLUDE_DIRS="--exclude=proc/ --exclude=dev/ --exclude=sys/ --exclude=run/ --exclude=tmp/ --exclude=mnt/ --exclude=media/ --exclude=lost+found/ --exclude=snap/ --exclude=*.initramfs --exclude=*.vmlinuz"

# perform the rsync from the node to the image directory
echo -n "Grabbing image $IMAGE_NAME from node $NODE (log: /tmp/$NODE-to-$IMAGE_NAME.log)..."
rsync -avzx $EXCLUDE_DIRS --progress $DELETE root@$NODE:/ $IMAGE_DIR/$IMAGE_NAME/ 2>&1 >  /tmp/$NODE-to-$IMAGE_NAME.log

# check the exit status of the rsync command
if [ $? -ne 0 ]; then
    echo "Error: rsync failed"
    exit 1
fi
echo "Image $IMAGE_NAME successfully grabbed from node $NODE"
exit 0


