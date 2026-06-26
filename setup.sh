#!/bin/bash
set -e

echo "[+] Updating system packages..."
sudo apt-get update -y

echo "[+] Installing system dependencies..."
sudo apt-get install -y ffmpeg pulseaudio python3.11 python3.11-venv python3-pip

echo "[+] Loading snd-aloop kernel module (virtual audio loopback)..."
sudo modprobe snd-aloop || echo "[!] snd-aloop not available on this kernel — skipping"
echo 'snd-aloop' | sudo tee -a /etc/modules 2>/dev/null || true

echo "[+] Starting PulseAudio daemon..."
pulseaudio --start --exit-idle-time=-1 2>/dev/null || true

echo "[+] Creating Python 3.11 virtual environment..."
python3.11 -m venv venv
source venv/bin/activate

echo "[+] Upgrading pip..."
pip install --upgrade pip

echo "[+] Installing Python requirements..."
pip install -r requirements.txt

echo ""
echo "========================================"
echo "[✓] Setup complete!"
echo "Next steps:"
echo "  1. source venv/bin/activate"
echo "  2. cp .env.example .env"
echo "  3. nano .env   (fill in your credentials)"
echo "  4. python bot.py"
echo "========================================"
