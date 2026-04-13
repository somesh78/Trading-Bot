"""
SENTINEL QUANT - start.py  (v5)
Detects hardware profile at launch and prints the optimal run command.
"""
import subprocess
import sys
import os
import socket

def check_port(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0

def main():
    print("=" * 60)
    print("  SENTINEL QUANT - Global Predator Swarm v5")
    print("=" * 60)

    # Detect hardware
    try:
        import psutil
        cpus = psutil.cpu_count(logical=True)
        ram  = psutil.virtual_memory().total / (1024**3)
        profile = "SERVER" if cpus >= 8 and ram >= 16 else "LAPTOP"
        workers  = max(1, cpus // 4) if profile == "SERVER" else 1
        print(f"  Hardware: {profile} | CPUs: {cpus} | RAM: {ram:.1f} GB")
        print(f"  Uvicorn workers: {workers}")
    except ImportError:
        workers = 1
        print("  psutil not installed - using 1 worker")

    # Check ports
    be_free = check_port(8000)
    fe_free = check_port(3000)

    if not be_free:
        print("\n  [!]  Port 8000 in use - stop existing backend first.")
        sys.exit(1)

    print(f"\n  Backend  -> http://localhost:8000")
    print(f"  Frontend -> http://localhost:3000")
    print(f"  WebSocket-> ws://localhost:8000/ws")
    print("=" * 60 + "\n")

    # Install deps
    print("[*] Installing/verifying backend dependencies...")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r",
         os.path.join("backend", "requirements.txt"), "-q"],
        check=True,
    )

    # FIX 1: OpenRouter API Validation
    print("[*] Validating OpenRouter API Key...")
    import httpx
    import json
    try:
        or_key = None
        with open(os.path.join("backend", ".env"), "r") as f:
            for line in f:
                if line.startswith("OPENROUTER_KEY="):
                    or_key = line.split("=", 1)[1].strip()
                    break
        
        if not or_key:
            # Fallback to GROQ_KEY as many users use it interchangeably for OR models
            with open(os.path.join("backend", ".env"), "r") as f:
                f.seek(0)
                for line in f:
                    if line.startswith("GROQ_KEY="):
                        or_key = line.split("=", 1)[1].strip()
                        break
        
        if or_key:
            headers = {"Authorization": f"Bearer {or_key}", "Content-Type": "application/json"}
            r = httpx.get("https://openrouter.ai/api/v1/models", headers=headers, timeout=10.0)
            if r.status_code == 401:
                print(f"\n  [FATAL] OPENROUTER_KEY is invalid or expired.")
                print("  Get a new key from https://openrouter.ai/keys and update your .env file.")
                sys.exit(1)
            print("  [OK] API Key validated.")
        else:
            print("  [WARNING] No OPENROUTER_KEY found in .env — skipping validation.")
    except Exception as e:
        print(f"  [WARNING] Registry validation skipped: {e}")

    # Start backend
    print("[*] Starting Backend (FastAPI + GraphEngine)...")
    be_cmd = [
        sys.executable, "-m", "uvicorn",
        "main:app",
        "--app-dir", "backend",
        "--host", "0.0.0.0",
        "--port", "8000",
        "--workers", str(workers),
        "--log-level", "info",
    ]
    be_proc = subprocess.Popen(be_cmd)

    # Start frontend
    print("[*] Starting Frontend (Next.js dev)...")
    fe_proc = subprocess.Popen(
        ["npm", "run", "dev"],
        cwd=os.path.join(os.path.dirname(__file__), "frontend"),
        shell=True,
    )

    print("\n[SUCCESS] System is LIVE!")
    print("  -> Open http://localhost:3000 in your browser\n")
    print("  Press Ctrl+C to shut down.\n")

    try:
        be_proc.wait()
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Terminating processes...")
        be_proc.terminate()
        fe_proc.terminate()

if __name__ == "__main__":
    main()
