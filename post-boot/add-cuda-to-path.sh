#!/bin/bash
# Put CUDA (/usr/local/cuda) on every login shell's PATH and library path.
# The variables are built without an empty element: an empty entry in
# LD_LIBRARY_PATH (or PATH) means the current directory (#76).
cat << 'EOF' > /etc/profile.d/zz-cuda.sh
export CUDA_HOME=/usr/local/cuda
export LD_LIBRARY_PATH=${LD_LIBRARY_PATH:+$LD_LIBRARY_PATH:}/usr/local/cuda/lib64:/usr/local/cuda/extras/CUPTI/lib64
export PATH=${PATH:+$PATH:}$CUDA_HOME/bin
EOF
