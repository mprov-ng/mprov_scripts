#!/bin/bash
cat << EOF > /etc/profile.d/zz-cuda.sh
export CUDA_HOME=/usr/local/cuda
export LD_LIBRARY_PATH=\${LD_LIBRARY_PATH:+\$LD_LIBRARY_PATH:}/usr/local/cuda/lib64:/usr/local/cuda/extras/CUPTI/lib64
export PATH=\${PATH:+\$PATH:}\$CUDA_HOME/bin
EOF