#!/usr/bin/env bash
# One-time setup for a fresh Ubuntu 22.04/24.04 server. Run as root:   bash setup-server.sh
# Installs Docker, enables the firewall (SSH, 80, 443 only), brute-force protection and automatic security updates,
# adds a 2 GB swap file, and creates a non-root user "deploy" that inherits root's SSH keys.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get -y upgrade
DEBIAN_FRONTEND=noninteractive apt-get -y install ca-certificates curl ufw fail2ban unattended-upgrades rsync

command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh

# deploy user (use this instead of root from now on)
id deploy >/dev/null 2>&1 || adduser --disabled-password --gecos "" deploy
usermod -aG docker,sudo deploy
if [ -f /root/.ssh/authorized_keys ]; then
  install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
  install -m 600 -o deploy -g deploy /root/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys
fi
echo "deploy ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/deploy && chmod 440 /etc/sudoers.d/deploy

# swap (small servers run out of RAM during image builds)
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# firewall: Docker only publishes 80/443 (Caddy); the database and Redis are never exposed
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

systemctl enable --now fail2ban
echo 'APT::Periodic::Update-Package-Lists "1"; APT::Periodic::Unattended-Upgrade "1";' > /etc/apt/apt.conf.d/20auto-upgrades

echo
echo "Done. NEXT: open a NEW terminal and check you can log in with:  ssh deploy@<server-ip>"
echo "Only after that works, optionally disable password logins (see DEPLOY.md, step 4)."