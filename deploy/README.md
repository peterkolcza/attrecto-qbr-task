# Deployment Runbook — Oracle VPS

Step-by-step guide to deploy the QBR Portfolio Health Report on an Oracle Cloud VPS.

## Prerequisites

- Oracle Cloud VPS (Ubuntu 22.04+ recommended, ARM or x86)
- Domain name pointing to the VPS IP (A record)
- SSH access to the VPS

## 1. Server Setup

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# Log out and back in for group change to take effect

# Install Docker Compose plugin
sudo apt install docker-compose-plugin -y

# Verify
docker --version
docker compose version
```

## 2. Clone & Configure

```bash
# Clone the repository
git clone https://github.com/peterkolcza/attrecto-qbr-task.git
cd attrecto-qbr-task

# Create production environment file
cp .env.prod.example .env

# Edit with your values
nano .env
# Set: QBR_DOMAIN=qbr.yourdomain.com
# Set: ANTHROPIC_API_KEY=sk-ant-api03-...
```

## 3. DNS Setup

Point your domain to the VPS IP address:

```
Type: A
Name: qbr (or your subdomain)
Value: <VPS-IP-ADDRESS>
TTL: 300
```

Wait for DNS propagation (usually 1-5 minutes).

## 4. Deploy

```bash
# Build and start
docker compose up -d --build

# Check status
docker compose ps
docker compose logs -f web
docker compose logs -f caddy
```

Caddy will automatically obtain a Let's Encrypt certificate for your domain.

## 5. Verify

```bash
# Health check
curl -s https://qbr.yourdomain.com/healthz | jq .

# Or use the smoke test script
bash deploy/smoke-test.sh https://qbr.yourdomain.com
```

Open `https://qbr.yourdomain.com` in a browser — you should see the QBR dashboard.

## 6. Operations

### View logs
```bash
docker compose logs -f          # all services
docker compose logs -f web      # just the app
docker compose logs -f caddy    # just Caddy
```

### Restart
```bash
docker compose restart web      # restart app only
docker compose up -d --build    # rebuild and restart
```

### Update
```bash
git pull origin main
docker compose up -d --build
```

### Backup reports
```bash
# Reports are in a Docker volume
docker compose exec web ls /app/reports/
docker compose cp web:/app/reports/ ./backup-reports/
```

## Oracle Cloud Firewall

Make sure ports 80 and 443 are open in your VPS's security list:

1. Go to Oracle Cloud Console → Networking → Virtual Cloud Networks
2. Select your VCN → Security Lists → Default Security List
3. Add ingress rules:
   - Source: `0.0.0.0/0`, Protocol: TCP, Dest Port: 80
   - Source: `0.0.0.0/0`, Protocol: TCP, Dest Port: 443

Also check `iptables` on the VPS:
```bash
sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
```
