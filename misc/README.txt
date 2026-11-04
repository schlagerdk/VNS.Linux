######################################### Set root password #########################################
# Set root password
sudo passwd root

######################################### Enable root ssh acccess #########################################
# Edit sshd_config and set PermitRootLogin yes
sudo nano /etc/ssh/sshd_config

# Restart sshd service
sudo systemctl restart sshd.service

		############################# OR #############################
		echo Install aug tool
		sudo apt install augeas-tools -y

		echo Set PermitRootLogin to yes
		sudo augtool --autosave 'set /files/etc/ssh/sshd_config/PermitRootLogin yes'

		echo Restart sshd service
		sudo systemctl restart sshd.service

######################################### Set static IP #########################################
nano /etc/netplan/50-cloud-init.yaml

network:
  version: 2
  renderer: networkd
  ethernets:
    ens18:
      addresses:
        [IP/24]
      routes:
        - to: default
          via: GATEWAY
          metric: 100
      nameservers:
        search: [local]
        addresses:  [1.1.1.1, 8.8.8.8]
      dhcp6: false
      dhcp4: false

netplan try
netplan apply

######################################### Set timezone #########################################
# Set your timezone
sudo timedatectl set-timezone Europe/Copenhagen

# Enable timesyncd
sudo timedatectl set-ntp on

######################################### Resize Disk #########################################
# See disk info
df -h

# See disk info
lsblk

# Grow the partition
growpart /dev/sda 3

# Extend volumn size
lvextend -l +100%FREE /dev/mapper/ubuntu--vg-ubuntu--lv

# Resize filesystem
resize2fs /dev/mapper/ubuntu--vg-ubuntu--lv

######################################### MailCow (Unauthenticated Relaying) #########################################
Unauthenticated Relaying
# Here we have added LAN (192.168.88.0/24), all VLANs (172.23.0.0/16), outside IPs (185.150.199.32/28), ZeroTier IPs (192.168.192.9/24) and home IP (93.161.56.131/32)

nano /opt/mailcow-dockerized/data/conf/postfix/extra.cf
mynetworks=127.0.0.0/8, ::ffff:127.0.0.0/104, ::1/128, fe80::/10, 172.22.1.0/24, fd4d:6169:6c63:6f77::/64, 192.168.88.0/24, 172.23.0.0/16, 185.150.199.32/28, 192.168.192.0/24, 93.161.56.131/32

nano /opt/mailcow-dockerized/data/conf/rspamd/local.d/options.inc
local_addrs = [127.0.0.0/8, ::ffff:127.0.0.0/104, ::1/128, fe80::/10, 172.22.1.0/24, fd4d:6169:6c63:6f77::/64, 192.168.88.0/24, 172.23.0.0/16, 185.150.199.32/28, 192.168.192.0/24, 93.161.56.131/32];

# Restart postfix and rspamd
docker-compose restart postfix-mailcow rspamd-mailcow
