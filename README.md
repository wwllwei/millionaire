# Stock Research Lab MVP

Research-only US-stock swing-trading prototype: FastAPI + Python backend, React/Vite dashboard, daily prices, technical indicators, and a simplified backtest. It does not place orders.

## Run (Python 3.11+, Node 18+)

Backend terminal:
```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```
API docs: http://127.0.0.1:8000/docs

Frontend terminal:
```powershell
cd frontend
npm install
npm run dev
```

`yfinance` is for prototyping; check its terms and data limitations. Backtests are simplified and do not predict future performance.
