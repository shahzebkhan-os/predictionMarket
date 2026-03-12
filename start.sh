#!/bin/bash

# NSE Options Trading System - Managed Startup Script
# This script handles environment setup and runs the project.

# Color codes
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}===============================================${NC}"
echo -e "${BLUE}   NSE OPTIONS TRADING SYSTEM STARTUP         ${NC}"
echo -e "${BLUE}===============================================${NC}"

# 1. Check for .env file
if [ ! -f .env ]; then
    echo -e "${YELLOW}[!] .env file missing. Copying from .env.example...${NC}"
    cp .env.example .env
    echo -e "${GREEN}[✓] .env file created. Please update it with your credentials.${NC}"
fi

# 2. Setup/Activate Virtual Environment
if [ -d ".venv" ]; then
    echo -e "${BLUE}[*] Activating virtual environment (.venv)...${NC}"
    source .venv/bin/activate
elif [ -d "venv" ]; then
    echo -e "${BLUE}[*] Activating virtual environment (venv)...${NC}"
    source venv/bin/activate
else
    echo -e "${RED}[X] Virtual environment not found. Please run installation steps first.${NC}"
    exit 1
fi

# 3. Detect and use compatible Python if needed
PYTHON_BINARY=$(which python3)
PYTHON_VERSION=$($PYTHON_BINARY -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')

if [[ $(echo "$PYTHON_VERSION < 3.11" | bc -l) -eq 1 ]]; then
    echo -e "${YELLOW}[!] Current Python ($PYTHON_VERSION) is < 3.11.${NC}"
    # Try to find a better one
    if [ -f "/opt/homebrew/bin/python3.11" ]; then
        export PATH="/opt/homebrew/bin:$PATH"
        echo -e "${GREEN}[✓] Using Python 3.11 from Homebrew.${NC}"
    fi
fi

# 4. Initialize Database
if [ ! -f nse_bot.db ] && [ ! -f nse_advisor.db ]; then
    echo -e "${BLUE}[*] Initializing database...${NC}"
    python -c "from nse_advisor.storage.db import init_database; import asyncio; asyncio.run(init_database())"
    echo -e "${GREEN}[✓] Database initialized.${NC}"
fi

# 5. Run the Project
echo -e "${GREEN}[✓] Starting NSE Advisor and Dashboard...${NC}"
echo -e "${YELLOW}[!] Press Ctrl+C to stop the system.${NC}"

# Run the main module
# Note: nse_advisor.main automatically starts the Streamlit dashboard
python -m nse_advisor.main
