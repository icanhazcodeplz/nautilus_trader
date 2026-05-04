#!/bin/bash

# Check size of macOS swap files

echo "=== Swap File Usage ==="
sudo du -sh /private/var/vm/ 2>/dev/null || echo "Need sudo to check swap files"
echo ""
echo "=== Individual Swap Files ==="
sudo ls -lh /private/var/vm/swapfile* 2>/dev/null || echo "No swap files found"
echo ""
echo "=== Memory Pressure ==="
memory_pressure 2>/dev/null | head -5
echo ""
echo "=== Disk Usage ==="
df -h /
echo ""

# Clean caches and logs
echo "=== ~/Library/Caches ==="
du -sh ~/Library/Caches 2>/dev/null
read -p "Delete ~/Library/Caches? (y/N) " choice
if [[ "$choice" == "y" ]]; then
    rm -rf ~/Library/Caches/*
    echo "Deleted."
fi

echo ""
echo "=== /var/log ==="
sudo du -sh /var/log 2>/dev/null
read -p "Delete /var/log contents? (y/N) " choice
if [[ "$choice" == "y" ]]; then
    sudo rm -rf /var/log/*
    echo "Deleted."
fi

# Check ~/Library/Application Support