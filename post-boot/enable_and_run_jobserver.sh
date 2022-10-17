#!/bin/bash
systemctl enable mprov-jobserver
systemctl start mprov-jobserver
firewall-cmd --zone=public --add-port=8080/tcp --permanent
firewall-cmd --reload