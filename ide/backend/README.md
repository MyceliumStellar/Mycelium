# Mycelium Web IDE Backend

The IDE backend is a FastAPI gateway service that runs alongside the Web IDE frontend client. It manages developer workspaces, handles code compilation, and scaffolds new agent repositories.

---

## 🔌 API Endpoints Reference

* **`POST /api/check`**: Runs static syntax evaluation and type verification on a Python-DSL contract. (Invokes `mycelium check`).
* **`POST /api/compile`**: Compiles a Python-DSL contract into WebAssembly (`.wasm`). (Invokes `mycelium compile`).
* **`POST /api/agents/scaffold`**: Scaffolds a new agent repository with the selected framework (e.g. `langgraph`, `gemini`), sets up templates, and registers it.
* **`GET /api/workspace`**: Manages developer workspaces, directories, and code files.

---

## 🛠️ Local Running & Setup

### 1. Install Dependencies
Make sure you are in the `ide/backend` directory and run:
```bash
pip install -r requirements.txt
```

### 2. Run the Server
Launch the FastAPI development server:
```bash
uvicorn main:app --port 8000 --reload
```
The backend API runs on `http://localhost:8000`.
