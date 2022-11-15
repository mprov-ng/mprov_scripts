#!/bin/bash


cd /etc/yum.repos.d
wget -O NM-1.40-COPR.repo https://copr.fedorainfracloud.org/coprs/networkmanager/NetworkManager-1.40/repo/epel-8/networkmanager-NetworkManager-1.40-epel-8.repo

dnf -y update "NetworkManager*"

