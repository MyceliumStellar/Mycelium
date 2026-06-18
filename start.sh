#!/bin/bash

# Mycelium Web IDE Startup Runner Script

# Trap Ctrl+C (SIGINT) and SIGTERM to kill background processes cleanly
cleanup() {
    echo -e "\n\033[1;31m[System]\033[0m Terminating Mycelium developer servers..."
    kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null
    exit 0
}

trap cleanup SIGINT SIGTERM

echo -e "\033[1;36m[System]\033[0m Running environment sanity checks..."

# 1. Check Virtual Environment
if [ ! -d "venv" ]; then
    echo -e "\033[1;31m[Error]\033[0m Virtual environment 'venv' not found at project root."
    echo -e "Please create it using: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

if [ ! -f "venv/bin/uvicorn" ]; then
    echo -e "\033[1;31m[Error]\033[0m 'uvicorn' not found in virtual environment."
    echo -e "Please run: venv/bin/pip install uvicorn fastapi requests cryptography pydantic"
    exit 1
fi

# 2. Check node_modules in frontend
if [ ! -d "ide/frontend/node_modules" ]; then
    echo -e "\033[1;33m[Warning]\033[0m frontend 'node_modules' is missing. Running npm install..."
    cd ide/frontend
    npm install
    cd ../..
fi

# 3. Check Docker and Build Compiler Image
if ! command -v docker &> /dev/null; then
    echo -e "\033[1;31m[Error]\033[0m 'docker' is not installed or not in PATH."
    echo -e "Please install Docker to enable the isolated compilation sandbox."
    exit 1
fi

if ! docker info &> /dev/null; then
    echo -e "\033[1;31m[Error]\033[0m Docker daemon is not running or current user lacks permissions."
    echo -e "Please start the Docker daemon (e.g., sudo systemctl start docker)."
    exit 1
fi

if [[ "$(docker images -q mycelium-compiler:latest 2> /dev/null)" == "" ]]; then
    echo -e "\033[1;36m[System]\033[0m Building 'mycelium-compiler:latest' Docker image (this will cache dependencies)..."
    docker build -t mycelium-compiler:latest -f compiler/Dockerfile .
    if [ $? -ne 0 ]; then
        echo -e "\033[1;31m[Error]\033[0m Failed to build compiler Docker image."
        exit 1
    fi
    echo -e "\033[1;32m[Success]\033[0m Compiler Docker image built successfully!"
fi

# 4. Check and resolve port binding conflicts
PORT_8000_PIDS=$(lsof -t -i :8000 2>/dev/null | tr '\n' ' ')
if [ ! -z "$PORT_8000_PIDS" ]; then
    echo -e "\033[1;33m[Warning]\033[0m Port 8000 (Backend) is in use by PID(s): $PORT_8000_PIDS. Terminating conflicting process(es)..."
    kill -9 $PORT_8000_PIDS 2>/dev/null
    sleep 1
fi

PORT_3000_PIDS=$(lsof -t -i :3000 2>/dev/null | tr '\n' ' ')
if [ ! -z "$PORT_3000_PIDS" ]; then
    echo -e "\033[1;33m[Warning]\033[0m Port 3000 (Frontend) is in use by PID(s): $PORT_3000_PIDS. Terminating conflicting process(es)..."
    kill -9 $PORT_3000_PIDS 2>/dev/null
    sleep 1
fi

# Clean up any leftover next dev servers on other ports if they are hanging around
NEXT_PIDS=$(pgrep -f "next" 2>/dev/null | tr '\n' ' ')
if [ ! -z "$NEXT_PIDS" ]; then
    kill -9 $NEXT_PIDS 2>/dev/null
fi

echo -e "\033[1;36m[System]\033[0m Launching Mycelium services..."

# 4. Start FastAPI Backend
echo -e "\033[1;34m[Backend]\033[0m Starting FastAPI gateway server on http://localhost:8000..."
cd ide/backend
PYTHONPATH="../../compiler:../../sdk:../..:$PYTHONPATH" ../../venv/bin/uvicorn main:app --port 8000 --reload &
BACKEND_PID=$!
cd ../..

# Give backend a moment to bind and launch
sleep 1.5

# 5. Start Next.js Frontend
echo -e "\033[1;32m[Frontend]\033[0m Starting Next.js Web IDE on http://localhost:3000..."
cd ide/frontend
npm run dev &
FRONTEND_PID=$!
cd ../..

echo -e "\033[1;32m[Ready]\033[0m Services are running. Open \033[1;36mhttp://localhost:3000/playground\033[0m in your browser."
echo -e "Press \033[1;33mCtrl+C\033[0m at any time to stop both servers."

# Wait indefinitely for both processes
wait

