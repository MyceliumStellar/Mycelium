# Mycelium Web IDE

This directory contains the Mycelium Web IDE codebase, split into the frontend client and the backend development server.

## Directory Structure

* **[frontend/](frontend/)**: The Next.js web application. Includes the Monaco contract editor playground, registry directory visualizer, and on-chain Bounty Board.
* **[backend/](backend/)**: The FastAPI backend gateway. Exposes APIs for syntax verification (`check`), compilation (`compile`), and agent repository scaffolding.

## Documentation

* Refer to the individual readmes for setup and local execution:
  - [Frontend README](frontend/README.md)
  - [Backend README](backend/README.md) (if present)
* For detailed architecture and API endpoints description, see [docs/ide.md](../docs/ide.md).
