#/bin/bash
GPFS_MGRNODE=mgmt03.ib.cluster
/usr/lpp/mmfs/bin/mmsdrrestore -p $GPFS_MGRNODE
/usr/lpp/mmfs/bin/mmstartup